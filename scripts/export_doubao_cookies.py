#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_doubao_cookies.py - 批量提取 Windows Chrome 全账号豆包 (Doubao) 登录态
用于将 26+ Chrome Profile 的豆包 Cookie 批量免密解密导出，方便无缝迁移至 Mac 或备份。
"""

import os
import sys
import json
import base64
import sqlite3
import shutil
import tempfile
import ctypes
from ctypes import wintypes
from pathlib import Path

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    print("❌ 缺少 cryptography 依赖库，请先运行: pip install cryptography")
    sys.exit(1)

def get_chrome_master_key(user_data_dir: Path) -> bytes:
    """从 Local State 解密 Chrome Master Key (AES-256)"""
    local_state_path = user_data_dir / "Local State"
    if not local_state_path.exists():
        raise FileNotFoundError(f"未找到 Local State 文件: {local_state_path}")
    
    local_state = json.loads(local_state_path.read_text(encoding="utf-8"))
    encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    # 去除开头的 b'DPAPI' 标志 (5字节)
    encrypted_key = encrypted_key[5:]
    
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
        
    blob_in = DATA_BLOB(len(encrypted_key), ctypes.cast(ctypes.create_string_buffer(encrypted_key), ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()
    
    ret = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ret:
        raise RuntimeError("Windows DPAPI CryptUnprotectData 解密失败！")
        
    master_key = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    return master_key

def decrypt_cookie_value(aes_gcm: AESGCM, encrypted_value: bytes) -> str:
    """解密单条 Cookie 密文 (Chrome v80+ AES-GCM)"""
    if not encrypted_value:
        return ""
    prefix = encrypted_value[:3]
    if prefix in (b"v10", b"v11"):
        nonce = encrypted_value[3:15]
        ciphertext = encrypted_value[15:]
        try:
            decrypted = aes_gcm.decrypt(nonce, ciphertext, None)
            return decrypted.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    else:
        # 老版本未加密或普通文本
        return encrypted_value.decode("utf-8", errors="ignore")

def chrome_time_to_unix(chrome_time: int) -> float:
    """Chrome 微秒时间戳 (从1601年开始) 转 Unix 秒级时间戳"""
    if chrome_time == 0:
        return 0.0
    return (chrome_time - 11644473600000000) / 1000000.0

def samesite_int_to_str(val: int) -> str:
    """SQLite 中的 SameSite 整数转换为 Cookie-Editor 字符串"""
    if val == 0:
        return "unspecified"
    elif val == 1:
        return "no_restriction"
    elif val == 2:
        return "lax"
    elif val == 3:
        return "strict"
    return "unspecified"

def export_all_doubao_cookies():
    user_data_dir = Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"))
    if not user_data_dir.exists():
        print(f"❌ 错误: Chrome 用户数据目录不存在: {user_data_dir}")
        return

    print("==================================================")
    print("🍪 VG Chrome 全账号豆包 (Doubao) Cookie 批量导出工具")
    print("==================================================")
    print(f"📁 Chrome 目录: {user_data_dir}\n")

    try:
        master_key = get_chrome_master_key(user_data_dir)
        aes_gcm = AESGCM(master_key)
        print("✅ 成功解密 Chrome Master Key (AES-256)！\n")
    except Exception as e:
        print(f"❌ 获取 Master Key 失败: {e}")
        return

    # 扫描所有 Profile 目录
    profiles = []
    for p in sorted(user_data_dir.iterdir()):
        if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile ")):
            cfile = p / "Network" / "Cookies"
            if not cfile.exists():
                cfile = p / "Cookies"
            if cfile.exists():
                profiles.append((p.name, cfile))

    print(f"🔍 共扫描到 {len(profiles)} 个 Chrome Profile 配置目录。\n")

    output_dir = Path(__file__).resolve().parent.parent / "data" / "doubao_cookies_export"
    output_dir.mkdir(parents=True, exist_ok=True)
    per_profile_dir = output_dir / "by_profile"
    per_profile_dir.mkdir(parents=True, exist_ok=True)

    all_cookies_map = {}
    locked_count = 0
    extracted_profiles = 0
    total_cookies_count = 0

    for pname, cfile in profiles:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_name = tmp.name

        try:
            shutil.copy2(cfile, tmp_name)
        except PermissionError:
            print(f"⚠️ [{pname}] 被 Chrome 进程独占锁定（请在导出前彻底关闭 Chrome 浏览器）")
            locked_count += 1
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            continue
        except Exception as e:
            print(f"⚠️ [{pname}] 复制失败: {e}")
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            continue

        try:
            conn = sqlite3.connect(tmp_name)
            cur = conn.cursor()
            # 查询与 doubao.com 相关的全部 cookie
            query = """
            SELECT name, host_key, path, is_secure, is_httponly, samesite, expires_utc, encrypted_value
            FROM cookies
            WHERE host_key LIKE '%doubao.com%'
            """
            cur.execute(query)
            rows = cur.fetchall()
            conn.close()

            if not rows:
                continue

            profile_cookies = []
            for name, host, path, sec, http, ssite, expires, enc_val in rows:
                val = decrypt_cookie_value(aes_gcm, enc_val)
                exp_unix = chrome_time_to_unix(expires)
                
                # 遵循标准 Cookie-Editor / Chrome Extension JSON 结构
                cookie_obj = {
                    "domain": host,
                    "name": name,
                    "value": val,
                    "path": path,
                    "secure": bool(sec),
                    "httpOnly": bool(http),
                    "sameSite": samesite_int_to_str(ssite),
                    "expirationDate": exp_unix if exp_unix > 0 else None,
                    "storeId": None
                }
                profile_cookies.append(cookie_obj)

            if profile_cookies:
                all_cookies_map[pname] = profile_cookies
                extracted_profiles += 1
                total_cookies_count += len(profile_cookies)
                
                # 为该账号单独保存一份 Cookie-Editor 导入格式的 JSON
                single_file = per_profile_dir / f"{pname}.json"
                single_file.write_text(json.dumps(profile_cookies, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"✅ [{pname}] 成功提取 {len(profile_cookies)} 个豆包 Cookie ➔ {single_file.name}")

        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

    # 汇总输出到总文件
    summary_file = output_dir / "doubao_cookies_all.json"
    summary_file.write_text(json.dumps(all_cookies_map, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n--------------------------------------------------")
    print(f"🎉 导出完成统计:")
    print(f"  • 成功导出账号: {extracted_profiles} 个")
    print(f"  • Cookie 总条数: {total_cookies_count} 条")
    if locked_count > 0:
        print(f"  • ⚠️ 被锁定跳过账号: {locked_count} 个（请彻底关闭 Chrome 浏览器后重新运行一次脚本即可全部捕获）")
    print(f"  • 📦 汇总文件: {summary_file}")
    print(f"  • 📁 各账号独立 JSON 目录: {per_profile_dir}")
    print("--------------------------------------------------")

if __name__ == "__main__":
    export_all_doubao_cookies()
