#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harvest_all_cookies.py - 利用 Chrome 原生插件全自动批量提取本机账号真实明文 Cookie
通过官方扩展直接调用 chrome.cookies API，100% 解密提取包括 HttpOnly sessionid 在内的全量有效凭据。
"""

import os
import sys
import time
import json
import subprocess
import urllib.request
from pathlib import Path

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

WORKBENCH_DIR = Path(__file__).resolve().parents[1]
CHROME_EXE = ""
TARGET_DIR = WORKBENCH_DIR / "data" / "cookie_exports" / "by_profile"
ALL_PROFILES = []

def kill_all_chrome():
    subprocess.run("taskkill /F /IM chrome.exe", shell=True, capture_output=True)
    time.sleep(0.5)

def harvest_one_profile(profile_name: str, index: int, total: int) -> bool:
    target_file = TARGET_DIR / f"{profile_name}.json"
    old_mtime = target_file.stat().st_mtime if target_file.exists() else 0

    url = f"http://127.0.0.1:8765/export_cookies.html?profile={profile_name}"
    cmd = [
        CHROME_EXE,
        f"--profile-directory={profile_name}",
        "--no-first-run",
        "--no-default-browser-check",
        url
    ]

    print(f"[{index:02d}/{total:02d}] 正在提取 【{profile_name}】(豆包账号 {index:02d})...", end="", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    success = False
    # 最多等待 5 秒
    for _ in range(25):
        time.sleep(0.2)
        if target_file.exists() and target_file.stat().st_mtime > old_mtime + 0.1:
            success = True
            break

    # 提取完毕立即关闭该 Chrome 实例
    kill_all_chrome()

    if success:
        try:
            cookies = json.loads(target_file.read_text(encoding="utf-8"))
            # 校验首条 cookie 是否为有效纯净 ASCII 明文
            first_val = cookies[0]["value"] if cookies else ""
            is_ascii = all(ord(c) < 128 for c in first_val)
            has_session = any(c.get("name") == "sessionid" for c in cookies)
            print(f" ✅ 成功提取 {len(cookies)} 条明文 Cookie (sessionid={'有' if has_session else '无'}, 明文正常={'是' if is_ascii else '否'})")
            return True
        except Exception as e:
            print(f" ⚠️ 数据解析异常: {e}")
            return False
    else:
        print(" ❌ 超时未收到扩展回传")
        return False

def main():
    print("==================================================")
    print("🍪 VG Chrome 全账号真实明文 Cookie 自动收割程序")
    print("==================================================")

    global CHROME_EXE, TARGET_DIR, ALL_PROFILES
    # 使用运行中的工作台配置和本机实际账号，避免辅助脚本与 EXE 数据目录不一致。
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/api/system/info", timeout=5) as response:
            info = json.load(response)["data"]
        CHROME_EXE = info["chrome_exe"]
        TARGET_DIR = Path(info["cookie_export_dir"]) / "by_profile"
        TARGET_DIR.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen("http://127.0.0.1:8765/api/accounts", timeout=5) as response:
            ALL_PROFILES = [account["id"] for account in json.load(response)["data"]]
    except Exception:
        print("本地工作台未就绪，请先启动工作台")
        sys.exit(1)
    print(f"目标目录: {TARGET_DIR}")
    if not ALL_PROFILES:
        print("当前机器没有可用 Chrome 账号")
        return
    kill_all_chrome()

    # 可指定本机账号进行单次验证
    if len(sys.argv) > 1 and sys.argv[1] == "--single":
        single_p = sys.argv[2] if len(sys.argv) > 2 else ALL_PROFILES[0]
        harvest_one_profile(single_p, 1, 1)
        return

    success_count = 0
    total = len(ALL_PROFILES)

    for idx, p_name in enumerate(ALL_PROFILES, 1):
        if harvest_one_profile(p_name, idx, total):
            success_count += 1
        time.sleep(0.3)

    print("\n--------------------------------------------------")
    print(f"🎉 全部收割完成！成功: {success_count}/{total} 个账号")
    print(f"📁 结果目录: {TARGET_DIR}")
    print("--------------------------------------------------")

if __name__ == "__main__":
    main()

