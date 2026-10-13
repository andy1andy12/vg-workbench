#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VG 视频生产与账号矩阵 Web 工作台 - 后端服务
运行环境: Python 3.12+ (零第三方依赖，纯标准库)
"""

import os
import sys
import re
import json
import subprocess
import webbrowser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

IS_WIN = (sys.platform == "win32")
IS_MAC = (sys.platform == "darwin")

PORT = 8765
WORKBENCH_DIR = Path(__file__).resolve().parent
DATA_DIR = WORKBENCH_DIR / "data"
PUBLIC_DIR = WORKBENCH_DIR / "public"

# 动态相对探测 VG 根目录：优先同级 ../VG，其次环境变量，最后回退
def resolve_vg_root():
    candidate = WORKBENCH_DIR.parent / "VG"
    if candidate.exists() and candidate.is_dir():
        return candidate
    env_root = os.environ.get("VG_ROOT")
    if env_root and Path(env_root).exists():
        return Path(env_root)
    if IS_WIN:
        fallback = Path(r"C:\Users\admin\DeskBox\其他\VG")
        if fallback.exists():
            return fallback
    elif IS_MAC:
        fallback = Path.home() / "DeskBox" / "其他" / "VG"
        if fallback.exists():
            return fallback
    return WORKBENCH_DIR.parent / "VG"

VG_ROOT = resolve_vg_root()

# 动态探测 Chrome 执行路径
def resolve_chrome_exe():
    env_chrome = os.environ.get("CHROME_PATH")
    if env_chrome and os.path.exists(env_chrome):
        return env_chrome
    if IS_WIN:
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    elif IS_MAC:
        mac_candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        ]
        for c in mac_candidates:
            if os.path.exists(c):
                return c
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return "google-chrome"

CHROME_EXE = resolve_chrome_exe()

# 动态探测 Chrome User Data 根目录 (Local State)
def resolve_chrome_user_data_dir():
    if IS_WIN:
        return Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"))
    elif IS_MAC:
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    else:
        return Path.home() / ".config" / "google-chrome"

CHROME_USER_DATA_DIR = resolve_chrome_user_data_dir()

# 动态探测脚手架脚本
def resolve_scaffold_script():
    candidates = [
        Path.home() / ".agents" / ".agents" / "skills" / "vg-index-checker" / "scripts" / "scaffold.py",
        Path.home() / ".agents" / "skills" / "vg-index-checker" / "scripts" / "scaffold.py",
        WORKBENCH_DIR / "scripts" / "scaffold.py"
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]

SCAFFOLD_SCRIPT = resolve_scaffold_script()

DATA_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

def open_file_or_dir(target_path):
    """跨平台在系统文件管理器中打开目录或文件"""
    p_str = str(target_path)
    if IS_WIN:
        try:
            os.startfile(p_str)
        except Exception:
            subprocess.Popen(f'explorer.exe "{p_str}"', shell=True)
    elif IS_MAC:
        subprocess.run(["open", p_str], check=False)
    else:
        subprocess.run(["xdg-open", p_str], check=False)

def ensure_launch_task():
    """确保 Windows 交互式桌面调度桥梁已注册 (仅 Windows 需要)"""
    if not IS_WIN:
        return
    bat_path = Path(r"C:\Users\admin\launch_account.bat")
    if not bat_path.exists():
        bat_path.write_text("@echo off\r\n", encoding="gbk")
    cmd = ["schtasks", "/create", "/tn", "VG_Launch_Profile", "/tr", str(bat_path), "/sc", "once", "/st", "23:59", "/f"]
    subprocess.run(cmd, capture_output=True, check=False)

ensure_launch_task()

# ----------------- 数据存取工具 -----------------

def get_chrome_profiles():
    """全自动扫描并纳管所有有效 Chrome Profile，按数字与名称自然排序并动态编号"""
    local_state_path = CHROME_USER_DATA_DIR / "Local State"
    profiles = []
    if not local_state_path.exists():
        return profiles
    try:
        with open(local_state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        info_cache = data.get("profile", {}).get("info_cache", {})
        for p_id, p_info in info_cache.items():
            if p_info.get("is_ephemeral") or p_info.get("is_omitted_from_profile_list"):
                continue
            name = p_info.get("name", "").strip()
            if not name:
                name = p_id
            
            # 提取名称中出现的首个数字作为主要排序权重（如 01-, 2-, 豆包3）
            num_matches = re.findall(r"\d+", name)
            primary_num = int(num_matches[0]) if num_matches else 999999
            
            profiles.append({
                "id": p_id,
                "name": name,
                "primary_num": primary_num,
                "avatar": p_info.get("avatar_icon", ""),
            })

        # 智能自然排序：先按数字大小，再按名称字母，最后按 Profile ID
        profiles.sort(key=lambda x: (x["primary_num"], x["name"].lower(), x["id"]))

        # 动态分配 1..N 的流水线显示序列
        for idx, p in enumerate(profiles, start=1):
            p["seq"] = idx
    except Exception as e:
        print(f"[Error] 读取 Chrome profiles 失败: {e}")
    return profiles

def load_accounts_quota():
    """加载或初始化 26 账号今日配额数据"""
    quota_file = DATA_DIR / "accounts_quota.json"
    profiles = get_chrome_profiles()
    quota_data = {}
    if quota_file.exists():
        try:
            quota_data = json.loads(quota_file.read_text(encoding="utf-8"))
        except Exception:
            quota_data = {}

    accounts = []
    for p in profiles:
        p_id = p["id"]
        saved = quota_data.get(p_id, {})
        # 默认每天容量：5条10s 或 10条5s (以点数单位计数，默认5个10s额度)
        accounts.append({
            "id": p_id,
            "seq": p.get("seq", 1),
            "name": p["name"],
            "remaining_10s": saved.get("remaining_10s", 5),
            "remaining_5s": saved.get("remaining_5s", 10),
            "remaining_15s": saved.get("remaining_15s", 4),
            "status": saved.get("status", "ready"), # ready / exhausted
            "notes": saved.get("notes", ""),
            "last_used": saved.get("last_used", "")
        })
    return accounts

def save_accounts_quota(accounts):
    quota_file = DATA_DIR / "accounts_quota.json"
    data = {acc["id"]: acc for acc in accounts}
    quota_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def load_daily_tasks():
    tasks_file = DATA_DIR / "daily_tasks.json"
    if tasks_file.exists():
        try:
            data = json.loads(tasks_file.read_text(encoding="utf-8"))
            # 补全缺少 spec 的任务（从关联的提示词文件正文智能解析 5s/10s/15s，默认 10s）
            for t in data.get("tasks", []):
                if not t.get("spec") and t.get("path"):
                    try:
                        p = Path(os.path.normpath(t["path"]))
                        if p.exists() and p.is_file():
                            head = p.read_text(encoding="utf-8", errors="ignore")[:600]
                            m_sec = re.search(r"^\s*(\d+)\s*(?:秒|s)\b", head, re.IGNORECASE)
                            if m_sec and m_sec.group(1) in ["5", "10", "15"]:
                                t["spec"] = f"{m_sec.group(1)}s"
                            elif re.search(r"\b15\s*(?:秒|s)\b|15秒", head, re.IGNORECASE):
                                t["spec"] = "15s"
                            elif re.search(r"\b5\s*(?:秒|s)\b|5秒", head, re.IGNORECASE):
                                t["spec"] = "5s"
                            else:
                                t["spec"] = "10s"
                    except Exception:
                        t["spec"] = "10s"
                elif not t.get("spec"):
                    t["spec"] = "10s"
            return data
        except Exception:
            pass
    return {
        "date": "",
        "product": "",
        "script": "",
        "tasks": []
    }


LATEST_ACTIVE_TASK_ID = ""

def get_task_context(task_id: str = None, account_id: str = None, latest: bool = False):
    """根据任务ID或账号获取该任务的完整上下文信息（提示词文本、关联参考图URL列表等）"""
    global LATEST_ACTIVE_TASK_ID
    tasks_data = load_daily_tasks()
    tasks = tasks_data.get("tasks", [])
    target_task = None
    
    if task_id:
        for t in tasks:
            if t.get("id") == task_id:
                target_task = t
                break
    elif account_id:
        # 优先找该账号名下尚未发送的任务
        for t in tasks:
            if t.get("assignedAccount") == account_id and not t.get("sent"):
                target_task = t
                break
        if not target_task:
            for t in tasks:
                if t.get("assignedAccount") == account_id:
                    target_task = t
                    break
    elif latest and LATEST_ACTIVE_TASK_ID:
        for t in tasks:
            if t.get("id") == LATEST_ACTIVE_TASK_ID:
                target_task = t
                break

    if not target_task and tasks:
        target_task = tasks[0]

    if not target_task:
        return None

    # 读取提示词全文
    prompt_text = ""
    file_path = target_task.get("path")
    p = None
    if file_path:
        norm_p = Path(os.path.normpath(file_path))
        if norm_p.exists() and norm_p.is_file():
            p = norm_p
        else:
            alt_p = VG_ROOT / file_path.lstrip("/\\")
            if alt_p.exists() and alt_p.is_file():
                p = alt_p

    if p and p.exists():
        try:
            prompt_text = p.read_text(encoding="utf-8")
        except Exception:
            pass

    # 定位产品目录及产品参考图
    ref_images = []
    prod_name = ""
    if p and p.exists():
        prod_dir = None
        for parent in p.parents:
            if re.match(r"^\d{3}_", parent.name):
                prod_dir = parent
                prod_name = parent.name
                break

        if prod_dir:
            ref_dir = prod_dir / "产品参考图"
            if ref_dir.exists():
                from urllib.parse import quote
                import base64

                # 1. 提取提示词正文中的图片提及，保留首次出现的顺序
                mentions_raw = re.findall(r"(\w+\.(?:png|jpg|jpeg|webp|PNG|JPG))", prompt_text)
                mention_order = {}
                for idx, m in enumerate(mentions_raw):
                    m_lower = m.lower()
                    if m_lower not in mention_order:
                        mention_order[m_lower] = idx

                # 2. 扫描所有图片文件，同名文件优先保留一级目录(产品参考图根目录)
                unique_files = {}
                for f in sorted(ref_dir.rglob("*.*")):
                    if f.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                        k = f.name.lower()
                        is_top = (f.parent == ref_dir)
                        if k not in unique_files:
                            unique_files[k] = f
                        elif is_top:
                            unique_files[k] = f

                # 3. 组装并排序
                candidate_list = []
                for k, f in unique_files.items():
                    is_match = k in mention_order
                    is_top_level = (f.parent == ref_dir)
                    order_idx = mention_order.get(k, 9999)
                    candidate_list.append({
                        "file": f,
                        "name": f.name,
                        "matched": is_match,
                        "order_idx": order_idx,
                        "top_level": is_top_level
                    })

                # 排序规则：正文提及的优先且按正文顺序排列；未提及的一级目录优先按文件名排
                candidate_list.sort(key=lambda x: (not x["matched"], x["order_idx"], not x["top_level"], x["name"].lower()))

                # 4. 生成数据对象并对所有命中的图片（及前4张兜底）生成 Base64 直送
                for idx, item in enumerate(candidate_list):
                    f = item["file"]
                    encoded_path = quote(str(f).replace("\\", "/"))
                    b64_str = ""
                    # 规则：凡是正文中明确提及命中的图片，100%全量生成 Base64；未命中的前10张也全量生成 Base64 兜底
                    if item["matched"] or idx < 10:
                        try:
                            mime = "image/png" if f.suffix.lower() == ".png" else "image/jpeg"
                            b64_str = f"data:{mime};base64," + base64.b64encode(f.read_bytes()).decode('utf-8')
                        except Exception:
                            pass

                    ref_images.append({
                        "name": item["name"],
                        "filename": item["name"],
                        "path": str(f).replace("\\", "/"),
                        "url": f"http://127.0.0.1:{PORT}/api/image?path={encoded_path}",
                        "base64": b64_str,
                        "matched": item["matched"],
                        "top_level": item["top_level"]
                    })

    # 智能识别视频时长规格 (5s / 10s / 15s)
    spec = target_task.get("spec")
    if not spec and prompt_text:
        m_sec = re.search(r"^\s*(\d+)\s*(?:秒|s)\b", prompt_text[:600], re.IGNORECASE)
        if m_sec and m_sec.group(1) in ["5", "10", "15"]:
            spec = f"{m_sec.group(1)}s"
        elif re.search(r"\b15\s*(?:秒|s)\b|15秒", prompt_text[:600], re.IGNORECASE):
            spec = "15s"
        elif re.search(r"\b5\s*(?:秒|s)\b|5秒", prompt_text[:600], re.IGNORECASE):
            spec = "5s"
        else:
            spec = "10s"

    return {
        "id": target_task.get("id"),
        "title": target_task.get("title"),
        "account": target_task.get("assignedAccount"),
        "path": target_task.get("path"),
        "sent": target_task.get("sent", False),
        "spec": spec or "10s",
        "product_name": prod_name,
        "prompt": prompt_text,
        "ref_images": ref_images
    }

def save_daily_tasks(data, make_snapshot=True):
    tasks_file = DATA_DIR / "daily_tasks.json"
    tasks_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    if make_snapshot and data.get("tasks"):
        try:
            history_dir = DATA_DIR / "history"
            history_dir.mkdir(parents=True, exist_ok=True)
            import datetime
            now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            snap_file = history_dir / f"snapshot_{now_str}.json"
            snap_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            
            # 最多保留 30 个快照
            all_snaps = sorted(history_dir.glob("snapshot_*.json"), key=os.path.getmtime, reverse=True)
            for old_snap in all_snaps[30:]:
                try:
                    old_snap.unlink()
                except Exception:
                    pass
        except Exception as e:
            print(f"[Warning] 保存历史快照失败: {e}")

def get_snapshots_list():
    history_dir = DATA_DIR / "history"
    if not history_dir.exists():
        return []
    snapshots = []
    import datetime
    for f in sorted(history_dir.glob("snapshot_*.json"), key=os.path.getmtime, reverse=True):
        try:
            content = json.loads(f.read_text(encoding="utf-8"))
            tasks = content.get("tasks", [])
            total = len(tasks)
            sent_count = sum(1 for t in tasks if t.get("sent"))
            edited_count = sum(1 for t in tasks if t.get("edited"))
            mtime = os.path.getmtime(f)
            time_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            snapshots.append({
                "filename": f.name,
                "time": time_str,
                "total": total,
                "sent": sent_count,
                "edited": edited_count,
                "sample_title": tasks[0].get("title", "") if tasks else "空批次"
            })
        except Exception:
            continue
    return snapshots

def scan_vg_tree():
    """扫描 VG 目录结构（全层级深度递归，无遗漏抓取所有有效提示词）"""
    tree = []
    if not VG_ROOT.exists():
        return tree
    
    for prod_dir in sorted(VG_ROOT.iterdir()):
        if not prod_dir.is_dir() or not re.match(r"^\d{3}_", prod_dir.name):
            continue
        
        prod_data = {
            "name": prod_dir.name,
            "path": str(prod_dir).replace("\\", "/"),
            "scripts": [],
            "folders": [], # 全层级文件夹列表
            "ref_images": []
        }
        
        # 1. 产品参考图
        ref_dir = prod_dir / "产品参考图"
        if ref_dir.exists() and ref_dir.is_dir():
            for img in ref_dir.rglob("*.*"):
                if img.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                    prod_data["ref_images"].append(str(img.relative_to(ref_dir)).replace("\\", "/"))
        
        # 2. 全层级递归收集所有有效 .md 提示词（排除 00_*.md 以及 成品/实拍/参考图）
        folder_dict = {}
        for md_file in sorted(prod_dir.rglob("*.md")):
            if md_file.name.startswith("00_"):
                continue
            rel_parts = md_file.relative_to(prod_dir).parts
            if any(p in ["产品参考图", "成品", "实拍"] for p in rel_parts):
                continue
            
            parent_dir = md_file.parent
            parent_path_str = str(parent_dir).replace("\\", "/")
            rel_folder = str(parent_dir.relative_to(prod_dir)).replace("\\", "/")
            
            if parent_path_str not in folder_dict:
                folder_dict[parent_path_str] = {
                    "name": parent_dir.name if parent_dir != prod_dir else "产品根目录",
                    "rel_path": rel_folder if rel_folder != "." else "根目录",
                    "path": parent_path_str,
                    "files": []
                }
            
            spec = "10s"
            try:
                head_text = md_file.read_text(encoding="utf-8", errors="ignore")[:600]
                m_sec = re.search(r"^\s*(\d+)\s*(?:秒|s)\b", head_text, re.IGNORECASE)
                if m_sec and m_sec.group(1) in ["5", "10", "15"]:
                    spec = f"{m_sec.group(1)}s"
                elif re.search(r"\b15\s*(?:秒|s)\b|15秒", head_text, re.IGNORECASE):
                    spec = "15s"
                elif re.search(r"\b5\s*(?:秒|s)\b|5秒", head_text, re.IGNORECASE):
                    spec = "5s"
            except Exception:
                pass

            folder_dict[parent_path_str]["files"].append({
                "name": md_file.name,
                "title": md_file.stem,
                "path": str(md_file).replace("\\", "/"),
                "spec": spec
            })
        
        prod_data["folders"] = list(folder_dict.values())

        # 3. 兼容旧接口的 scripts 结构
        for script_dir in sorted(prod_dir.iterdir()):
            if not script_dir.is_dir() or script_dir.name in ["产品参考图", "成品", "实拍"]:
                continue
            script_data = {
                "name": script_dir.name,
                "path": str(script_dir).replace("\\", "/"),
                "masters": [f.name for f in script_dir.glob("*.md") if not f.name.startswith("00_")],
                "variants": []
            }
            for sub_dir in script_dir.rglob("*"):
                if sub_dir.is_dir():
                    md_in_sub = [vf.name for vf in sub_dir.glob("*.md") if not vf.name.startswith("00_")]
                    if md_in_sub:
                        script_data["variants"].append({
                            "group_name": str(sub_dir.relative_to(script_dir)).replace("\\", "/"),
                            "files": md_in_sub
                        })
            prod_data["scripts"].append(script_data)
        
        tree.append(prod_data)
    return tree

# ----------------- HTTP 请求处理 -----------------

class WorkbenchHandler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path

        if path == "/api/system/info":
            self.send_json({
                "code": 0,
                "data": {
                    "platform": "mac" if IS_MAC else ("win" if IS_WIN else "linux"),
                    "vg_root": str(VG_ROOT).replace("\\", "/"),
                    "workbench_dir": str(WORKBENCH_DIR).replace("\\", "/"),
                    "chrome_exe": str(CHROME_EXE),
                    "chrome_user_data_dir": str(CHROME_USER_DATA_DIR).replace("\\", "/")
                }
            })

        elif path == "/api/accounts":
            accounts = load_accounts_quota()
            self.send_json({"code": 0, "data": accounts})

        elif path == "/api/vg/tree":
            tree = scan_vg_tree()
            self.send_json({"code": 0, "data": tree})

        elif path == "/api/tasks":
            tasks = load_daily_tasks()
            self.send_json({"code": 0, "data": tasks})

        elif path == "/api/tampermonkey_script":
            # 返回配合豆包使用的油猴脚本源码
            script_path = WORKBENCH_DIR / "doubao_auto_paste.user.js"
            code = script_path.read_text(encoding="utf-8") if script_path.exists() else "// 暂无脚本"
            self.send_json({"code": 0, "data": code})

        elif path == "/api/image":
            # 提供本地参考图二进制流，带 CORS 与 MIME 响应
            query = parse_qs(url.query)
            img_path = query.get("path", [""])[0]
            if img_path:
                img_p = Path(os.path.normpath(img_path))
                if img_p.exists() and img_p.is_file():
                    ext = img_p.suffix.lower().lstrip(".")
                    mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                    content = img_p.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.end_headers()
                    self.wfile.write(content)
                    return
            self.send_response(404)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"Image not found")

        elif path == "/api/video":
            # 本地成品视频流式预览服务
            query = parse_qs(url.query)
            vid_path = query.get("path", [""])[0]
            task_id = query.get("task_id", [""])[0]
            target_p = None
            if vid_path:
                target_p = Path(os.path.normpath(vid_path))
            elif task_id:
                tasks_data = load_daily_tasks()
                for t in tasks_data.get("tasks", []):
                    if t.get("id") == task_id and t.get("local_video_path"):
                        target_p = Path(os.path.normpath(t["local_video_path"]))
                        break
            if target_p and target_p.exists() and target_p.is_file():
                content = target_p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                self.wfile.write(content)
                return
            self.send_response(404)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"Video not found")

        elif path == "/api/task/context":
            # 获取指定任务详情（含提示词、参考图URL等）
            query = parse_qs(url.query)
            task_id = query.get("task_id", [None])[0]
            account_id = query.get("account", [None])[0]
            latest = "latest" in query
            ctx = get_task_context(task_id, account_id, latest)
            if ctx:
                self.send_json({"code": 0, "data": ctx})
            else:
                self.send_json({"code": 1, "message": "未找到指定任务上下文"}, status=404)

        elif path == "/api/tasks/snapshots":
            snaps = get_snapshots_list()
            self.send_json({"code": 0, "data": snaps})

        else:
            # 静态文件服务
            if path == "/" or path == "":
                filepath = PUBLIC_DIR / "index.html"
            else:
                filepath = PUBLIC_DIR / path.lstrip("/")
                if not filepath.exists():
                    alt = WORKBENCH_DIR / path.lstrip("/")
                    if alt.exists():
                        filepath = alt

            if filepath.exists() and filepath.is_file():
                content_type = "text/html; charset=utf-8"
                if filepath.suffix == ".css":
                    content_type = "text/css; charset=utf-8"
                elif filepath.suffix == ".js":
                    content_type = "application/javascript; charset=utf-8"
                elif filepath.suffix in [".png", ".jpg", ".jpeg"]:
                    content_type = f"image/{filepath.suffix.lstrip('.')}"
                
                content = filepath.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"404 Not Found")

    def do_POST(self):
        url = urlparse(self.path)
        path = url.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            payload = {}

        if path == "/api/accounts/update":
            # 更新账号状态或扣减
            p_id = payload.get("id")
            delta_10s = payload.get("delta_10s", 0)
            set_status = payload.get("status")
            accounts = load_accounts_quota()
            for acc in accounts:
                if acc["id"] == p_id:
                    if delta_10s != 0:
                        acc["remaining_10s"] = max(0, acc["remaining_10s"] + delta_10s)
                        acc["remaining_5s"] = acc["remaining_10s"] * 2
                        acc["remaining_15s"] = int(acc["remaining_10s"] * 0.8)
                    if set_status:
                        acc["status"] = set_status
                        if set_status == "exhausted":
                            acc["remaining_10s"] = 0
                            acc["remaining_5s"] = 0
                            acc["remaining_15s"] = 0
                        elif set_status == "ready" and acc.get("remaining_10s", 0) == 0:
                            acc["remaining_10s"] = 5
                            acc["remaining_5s"] = 10
                            acc["remaining_15s"] = 4
            save_accounts_quota(accounts)
            self.send_json({"code": 0, "data": accounts})

        elif path == "/api/accounts/reset":
            # 每日一键满血重置（保留已禁用的 disabled 状态）
            accounts = load_accounts_quota()
            for acc in accounts:
                acc["remaining_10s"] = 5
                acc["remaining_5s"] = 10
                acc["remaining_15s"] = 4
                if acc.get("status") != "disabled":
                    acc["status"] = "ready"
            save_accounts_quota(accounts)
            self.send_json({"code": 0, "data": accounts, "message": "全员账号已满血重置（保留禁用设置）！"})

        elif path == "/api/accounts/launch":
            # 唤起对应的 Chrome 资料并打开豆包 (Win/Mac 双平台原生自适应)
            p_id = payload.get("id", "Default")
            target_url = "https://www.doubao.com/chat/"
            try:
                if IS_WIN:
                    launcher_bat = Path(r"C:\Users\admin\launch_account.bat")
                    bat_code = (
                        f'@echo off\r\n'
                        f'start "" "{CHROME_EXE}" --profile-directory="{p_id}" "{target_url}"\r\n'
                        f'timeout /t 1 /nobreak >nul\r\n'
                        f'powershell -NoProfile -Command "(New-Object -ComObject WScript.Shell).AppActivate(\'Google Chrome\')"\r\n'
                    )
                    launcher_bat.write_text(bat_code, encoding="gbk")
                    subprocess.run(["schtasks", "/run", "/tn", "VG_Launch_Profile"], capture_output=True, check=False)
                elif IS_MAC:
                    ext_dir = str(WORKBENCH_DIR / "vg_extension")
                    cmd = [
                        "open", "-na", "Google Chrome", "--args",
                        f"--profile-directory={p_id}",
                        f"--load-extension={ext_dir}",
                        target_url
                    ]
                    subprocess.Popen(cmd)
                    # AppleScript 激活 Chrome 到最前
                    subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], check=False)
                else:
                    subprocess.Popen([CHROME_EXE, f"--profile-directory={p_id}", target_url])

                self.send_json({"code": 0, "message": f"已成功唤起 Chrome [{p_id}] 窗口！"})
            except Exception as e:
                self.send_json({"code": 1, "message": f"唤起 Chrome 失败: {e}"}, status=500)

        elif path == "/api/vg/open_folder":
            # 在系统文件管理器/Finder打开指定目录 (支持快捷目标与跨平台路径)
            target_type = payload.get("type")
            target_path = payload.get("path")
            p = None
            if target_type == "vg_root":
                p = VG_ROOT
            elif target_type == "extension":
                p = WORKBENCH_DIR / "vg_extension"
            elif target_type == "product_ref":
                prod_name = payload.get("product")
                if prod_name:
                    p = VG_ROOT / prod_name / "产品参考图"
            elif target_path:
                p = Path(os.path.normpath(target_path))

            if p and p.exists():
                try:
                    open_file_or_dir(p)
                    self.send_json({"code": 0, "message": "已在系统文件管理器中打开"})
                except Exception as e:
                    self.send_json({"code": 1, "message": str(e)}, status=500)
            else:
                self.send_json({"code": 1, "message": f"路径不存在或未找到: {p or target_path}"}, status=400)

        elif path == "/api/vg/read_file":
            # 读取某个文件内容
            file_path = payload.get("path", "").strip()
            p = None
            if file_path:
                norm_p = Path(os.path.normpath(file_path))
                if norm_p.exists() and norm_p.is_file():
                    p = norm_p
                else:
                    alt_p = VG_ROOT / file_path.lstrip("/\\")
                    if alt_p.exists() and alt_p.is_file():
                        p = alt_p

            if p and p.exists() and p.is_file():
                try:
                    text = p.read_text(encoding="utf-8")
                    self.send_json({"code": 0, "data": text})
                except Exception as e:
                    self.send_json({"code": 1, "message": f"读取失败: {e}"}, status=500)
            else:
                self.send_json({"code": 1, "message": f"文件不存在: {file_path}"}, status=400)

        elif path == "/api/tasks/save":
            save_daily_tasks(payload)
            self.send_json({"code": 0, "message": "今日任务保存成功"})

        elif path == "/api/tasks/restore_snapshot":
            filename = payload.get("filename")
            if not filename:
                self.send_json({"code": 1, "message": "未指定快照文件"}, status=400)
            else:
                snap_path = DATA_DIR / "history" / filename
                if snap_path.exists() and snap_path.is_file():
                    try:
                        content = json.loads(snap_path.read_text(encoding="utf-8"))
                        save_daily_tasks(content, make_snapshot=False)
                        self.send_json({"code": 0, "message": "快照已成功恢复！", "data": content})
                    except Exception as e:
                        self.send_json({"code": 1, "message": f"恢复失败: {e}"}, status=500)
                else:
                    self.send_json({"code": 1, "message": "快照文件不存在"}, status=404)

        elif path == "/api/task/mark_step":
            task_id = payload.get("task_id")
            step = payload.get("step", "sent") # sent / downloaded / edited
            val = payload.get("value", True)
            account_id = payload.get("account_id")
            tasks_data = load_daily_tasks()
            updated = False
            task_spec = "10s"
            for t in tasks_data.get("tasks", []):
                if t.get("id") == task_id:
                    t[step] = val
                    if not account_id and t.get("assignedAccount"):
                        account_id = t.get("assignedAccount")
                    task_spec = t.get("spec", "10s")
                    updated = True
                    break
            if updated:
                save_daily_tasks(tasks_data)
                # 如果标记为 sent 且指定了账号，根据任务的时长规格 (5s/10s/15s) 扣减对应额度
                if step == "sent" and val and account_id:
                    accounts = load_accounts_quota()
                    for acc in accounts:
                        if acc["id"] == account_id:
                            if task_spec == "5s":
                                acc["remaining_5s"] = max(0, acc["remaining_5s"] - 1)
                                acc["remaining_10s"] = max(0, int(acc["remaining_5s"] / 2))
                                acc["remaining_15s"] = max(0, int(acc["remaining_10s"] * 0.8))
                            elif task_spec == "15s":
                                acc["remaining_15s"] = max(0, acc["remaining_15s"] - 1)
                                acc["remaining_10s"] = max(0, int(acc["remaining_15s"] * 1.25))
                                acc["remaining_5s"] = acc["remaining_10s"] * 2
                            else: # 10s
                                acc["remaining_10s"] = max(0, acc["remaining_10s"] - 1)
                                acc["remaining_5s"] = acc["remaining_10s"] * 2
                                acc["remaining_15s"] = max(0, int(acc["remaining_10s"] * 0.8))
                    save_accounts_quota(accounts)
                self.send_json({"code": 0, "message": "任务状态已更新", "data": tasks_data})
            else:
                self.send_json({"code": 1, "message": "任务未找到"}, status=404)

        elif path == "/api/accounts/launch_task":
            # 唤起 Chrome 并打开带 task_id 的豆包标签页
            global LATEST_ACTIVE_TASK_ID
            account_id = payload.get("account_id", "Default")
            task_ids = payload.get("task_ids", [])
            if not task_ids and payload.get("task_id"):
                task_ids = [payload.get("task_id")]
            
            if task_ids:
                LATEST_ACTIVE_TASK_ID = task_ids[0]
            
            # 单次限制最多打开 10 条
            task_ids = task_ids[:10]
            
            urls = []
            if task_ids:
                for tid in task_ids:
                    urls.append(f"https://www.doubao.com/chat/?vg_task={tid}")
            else:
                urls.append("https://www.doubao.com/chat/")
            
            urls_arg = " ".join([f'"{u}"' for u in urls])
            
            try:
                ext_dir = str(WORKBENCH_DIR / "vg_extension")
                if IS_WIN:
                    launcher_bat = Path(r"C:\Users\admin\launch_account.bat")
                    bat_code = (
                        f'@echo off\r\n'
                        f'start "" "{CHROME_EXE}" --profile-directory="{account_id}" --load-extension="{ext_dir}" {urls_arg}\r\n'
                    )
                    launcher_bat.write_text(bat_code, encoding="gbk")
                    subprocess.run(["schtasks", "/run", "/tn", "VG_Launch_Profile"], capture_output=True, check=False)
                elif IS_MAC:
                    cmd = [
                        "open", "-na", "Google Chrome", "--args",
                        f"--profile-directory={account_id}",
                        f"--load-extension={ext_dir}"
                    ] + urls
                    subprocess.Popen(cmd)
                    subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], check=False)
                else:
                    cmd = [CHROME_EXE, f"--profile-directory={account_id}", f"--load-extension={ext_dir}"] + urls
                    subprocess.Popen(cmd)

                self.send_json({"code": 0, "message": f"已成功为账号 [{account_id}] 唤起 {len(urls)} 个任务窗口！"})
            except Exception as e:
                self.send_json({"code": 1, "message": f"唤起失败: {e}"}, status=500)

        elif path == "/api/scaffold/run":
            # 调用 scaffold.py 执行骨架创建
            cmd_type = payload.get("type") # new-product, new-script, new-variant
            cmd = [sys.executable, str(SCAFFOLD_SCRIPT), cmd_type]
            if cmd_type == "new-product":
                cmd.extend(["--name", payload["name"], "--category", payload.get("category", "车品配件"), "--desc", payload.get("desc", "")])
            elif cmd_type == "new-script":
                cmd.extend(["--product", payload["product"], "--name", payload["name"], "--desc", payload.get("desc", ""), "--spec", payload.get("spec", "")])
            elif cmd_type == "new-variant":
                cmd.extend([
                    "--product", payload["product"],
                    "--script", payload["script"],
                    "--subdir", payload.get("subdir", "迁移"),
                    "--code", payload.get("code", "C"),
                    "--version", payload.get("version", "V2"),
                    "--scene", payload["scene"]
                ])
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
                if res.returncode == 0:
                    self.send_json({"code": 0, "message": "执行成功！", "output": res.stdout})
                else:
                    self.send_json({"code": 1, "message": res.stderr or res.stdout}, status=400)
            except Exception as e:
                self.send_json({"code": 1, "message": str(e)}, status=500)

        elif path == "/api/task/report_video":
            # 扩展自动上报豆包解密出的无水印视频 URL
            task_id = payload.get("task_id")
            video_url = payload.get("video_url")
            account_id = payload.get("account_id")
            if not video_url:
                self.send_json({"code": 1, "message": "缺少视频URL"}, status=400)
                return

            tasks_data = load_daily_tasks()
            all_tasks = tasks_data.get("tasks", [])
            target_task = None
            if task_id:
                for t in all_tasks:
                    if t.get("id") == task_id:
                        target_task = t
                        break
            if not target_task and account_id:
                # 匹配该账号名下已发送且未绑定视频的任务（倒序优先匹配最新）
                for t in reversed(all_tasks):
                    if t.get("assignedAccount") == account_id and t.get("sent") and not t.get("video_url"):
                        target_task = t
                        break
            if not target_task:
                # 兜底匹配全流水线中已发送且未下载的第一条无视频URL任务
                for t in all_tasks:
                    if t.get("sent") and not t.get("downloaded") and not t.get("video_url"):
                        target_task = t
                        break

            if target_task:
                target_task["video_url"] = video_url
                target_task["video_ready"] = True
                save_daily_tasks(tasks_data)
                print(f"[Video Hook] 成功关联任务 [{target_task['id']}] -> {video_url[:60]}...")
                self.send_json({"code": 0, "message": "视频已成功关联任务", "task_id": target_task["id"], "video_url": video_url})
            else:
                self.send_json({"code": 1, "message": "未找到适合关联的任务"}, status=404)

        elif path == "/api/tasks/download_videos":
            # 批量下载已选任务的无水印成片至对应产品的【成品/素材/】目录
            task_ids = payload.get("task_ids", [])
            tasks_data = load_daily_tasks()
            all_tasks = tasks_data.get("tasks", [])

            if not task_ids:
                # 若未指定，则下载所有视频就绪且未下载的任务
                task_ids = [t["id"] for t in all_tasks if (t.get("video_url") or t.get("video_ready")) and not t.get("downloaded")]

            if not task_ids:
                self.send_json({"code": 1, "message": "未勾选或没有可下载的视频任务"}, status=400)
                return

            downloaded_list = []
            failed_list = []

            for tid in task_ids:
                t = next((item for item in all_tasks if item.get("id") == tid), None)
                if not t:
                    failed_list.append({"id": tid, "error": "任务不存在"})
                    continue
                v_url = t.get("video_url")
                if not v_url:
                    failed_list.append({"id": tid, "error": "尚未捕获到无水印视频链接"})
                    continue

                # 定位产品目录
                prod_dir = None
                if t.get("path"):
                    p = Path(os.path.normpath(t["path"]))
                    for parent in p.parents:
                        if re.match(r"^\d{3}_", parent.name):
                            prod_dir = parent
                            break
                if not prod_dir and VG_ROOT.exists():
                    for d in sorted(VG_ROOT.iterdir()):
                        if d.is_dir() and re.match(r"^\d{3}_", d.name):
                            prod_dir = d
                            break

                if not prod_dir:
                    failed_list.append({"id": tid, "error": "无法定位产品目录"})
                    continue

                # 严格按照用户决策存入：成品/素材/ 目录
                material_dir = prod_dir / "成品" / "素材"
                material_dir.mkdir(parents=True, exist_ok=True)

                clean_title = re.sub(r'[\\/*?:"<>|]', '_', t.get("title", "未命名")).strip()
                filename = f"{t['id']}_{clean_title}.mp4"
                dest_file = material_dir / filename

                # 下载文件 (支持断点重试与防伪装 Header)
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        v_url,
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                            "Referer": "https://www.doubao.com/"
                        }
                    )
                    with urllib.request.urlopen(req, timeout=90) as resp, open(dest_file, "wb") as out_f:
                        while True:
                            chunk = resp.read(128 * 1024)
                            if not chunk:
                                break
                            out_f.write(chunk)

                    t["downloaded"] = True
                    t["local_video_path"] = str(dest_file).replace("\\", "/")
                    downloaded_list.append({
                        "id": t["id"],
                        "title": t["title"],
                        "file_path": str(dest_file).replace("\\", "/"),
                        "prod_dir": str(prod_dir).replace("\\", "/")
                    })
                except Exception as e:
                    failed_list.append({"id": tid, "error": f"下载网络异常: {e}"})

            if downloaded_list:
                save_daily_tasks(tasks_data)

            self.send_json({
                "code": 0,
                "message": f"成功下载 {len(downloaded_list)} 条成片到【成品/素材/】！" + (f"（{len(failed_list)}条失败）" if failed_list else ""),
                "data": {
                    "downloaded": downloaded_list,
                    "failed": failed_list
                }
            })

        elif path == "/api/vg/open_material_folder":
            # 在系统文件管理器中打开当前产品的 成品/素材 目录 (Win/Mac自适应)
            prod_name = payload.get("prod_name")
            target_dir = None
            if prod_name:
                p = VG_ROOT / prod_name / "成品" / "素材"
                p.mkdir(parents=True, exist_ok=True)
                target_dir = p
            else:
                tasks_data = load_daily_tasks()
                tasks = tasks_data.get("tasks", [])
                if tasks and tasks[0].get("path"):
                    for parent in Path(os.path.normpath(tasks[0]["path"])).parents:
                        if re.match(r"^\d{3}_", parent.name):
                            target_dir = parent / "成品" / "素材"
                            target_dir.mkdir(parents=True, exist_ok=True)
                            break
            if not target_dir and VG_ROOT.exists():
                for d in sorted(VG_ROOT.iterdir()):
                    if d.is_dir() and re.match(r"^\d{3}_", d.name):
                        target_dir = d / "成品" / "素材"
                        target_dir.mkdir(parents=True, exist_ok=True)
                        break
            if target_dir and target_dir.exists():
                open_file_or_dir(target_dir)
                self.send_json({"code": 0, "message": f"已打开目录: {target_dir}"})
            else:
                self.send_json({"code": 1, "message": "未定位到素材目录"}, status=404)

        else:
            self.send_json({"code": 404, "message": "未知接口"}, status=404)

def run():
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, WorkbenchHandler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"==================================================")
    print(f"🚀 VG 视频生产与账号矩阵 Web 工作台已启动！")
    print(f"🌐 访问地址: {url}")
    print(f"📂 工作台路径: {WORKBENCH_DIR}")
    print(f"==================================================")
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")

if __name__ == "__main__":
    run()
