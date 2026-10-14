#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VG 视频生产与账号矩阵 Web 工作台 - 后端服务
运行环境: Python 3.12+ (零第三方依赖，纯标准库)
"""

import os
import base64
import sys
import re
import json
import subprocess
import webbrowser
import threading
import time
import shutil
import tempfile
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
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

# 支持 PyInstaller 打包自适应：可写数据目录基于 exe 所在真实物理目录，静态模板支持 _MEIPASS
if getattr(sys, 'frozen', False):
    WORKBENCH_DIR = Path(sys.executable).resolve().parent
else:
    WORKBENCH_DIR = Path(__file__).resolve().parent

def resolve_runtime_dir():
    """优先程序旁保存数据；只读安装目录使用当前用户的数据目录。"""
    try:
        with tempfile.TemporaryFile(dir=WORKBENCH_DIR):
            pass
        return WORKBENCH_DIR
    except OSError:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
        target = base / "VGWorkbench"
        target.mkdir(parents=True, exist_ok=True)
        return target

RUNTIME_DIR = resolve_runtime_dir()

# PyInstaller --noconsole 会将标准输出/错误设为 None；HTTP 响应日志
# 依赖 stderr.write，必须在处理请求前提供可写流，避免空响应。
if sys.stdout is None or sys.stderr is None:
    runtime_log = open(RUNTIME_DIR / "workbench.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = runtime_log
    if sys.stderr is None:
        sys.stderr = runtime_log

DATA_DIR = RUNTIME_DIR / "data"
COOKIE_EXPORT_DIR = DATA_DIR / "cookie_exports"
LAUNCHER_BAT = DATA_DIR / "runtime" / "launch_account.bat"
LAUNCH_TASK_NAME = "VG_Launch_Profile_" + hashlib.sha256(str(Path.home()).encode("utf-8")).hexdigest()[:12]

def resolve_resource_dir(sub_name: str) -> Path:
    """自适应探测静态资源目录：优先工作台外部当前目录（方便实时热更新），其次 PyInstaller 内置解压包"""
    local_path = WORKBENCH_DIR / sub_name
    if local_path.is_dir():
        return local_path
    if hasattr(sys, '_MEIPASS'):
        bundle_path = Path(sys._MEIPASS) / sub_name
        if bundle_path.exists():
            return bundle_path
    return local_path

PUBLIC_DIR = resolve_resource_dir("public")

def ensure_extension_dir():
    """将打包内置插件落地到固定目录，避免 Chrome 引用临时解压路径。"""
    source = resolve_resource_dir("vg_extension")
    target = RUNTIME_DIR / "vg_extension"
    if not (source / "manifest.json").is_file():
        raise FileNotFoundError(f"插件资源缺失: {source / 'manifest.json'}")
    if source.resolve() != target.resolve():
        target.mkdir(parents=True, exist_ok=True)
        for item in source.rglob("*"):
            if item.is_file():
                dest = target / item.relative_to(source)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.is_file() or dest.read_bytes() != item.read_bytes():
                    shutil.copy2(item, dest)
    return target

def resolve_desktop_dir():
    """读取系统桌面位置，兼容 OneDrive 和用户重定向桌面。"""
    if IS_WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                value, _ = winreg.QueryValueEx(key, "Desktop")
                return Path(os.path.expandvars(value))
        except OSError:
            pass
    return Path.home() / "Desktop"

# 默认使用当前用户的系统桌面；环境变量可指定独立目录。
def resolve_vg_root():
    env_root = os.environ.get("VG_ROOT")
    if env_root:
        return Path(env_root)
    return resolve_desktop_dir() / "VG"

VG_ROOT = resolve_vg_root()

SETTINGS_FILE = DATA_DIR / "settings.json"

def get_default_download_dir():
    default_dir = resolve_desktop_dir() / "成片导出"
    return str(default_dir).replace("\\", "/")

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if not data.get("download_dir"):
                data["download_dir"] = get_default_download_dir()
            return data
        except Exception:
            pass
    return {"download_dir": get_default_download_dir()}

def save_settings(settings):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[Settings] 保存配置失败: {e}")

VG_INIT_LOCK = threading.Lock()
DURATION_LOCK = threading.Lock()

def initialize_vg_library():
    with VG_INIT_LOCK:
        settings = load_settings()
        first_run = settings.get("vg_library_layout") != "duration-folders-v2"
        VG_ROOT.mkdir(parents=True, exist_ok=True)
        if first_run:
            example = resolve_resource_dir("vg_example")
            if not example.is_dir():
                example = WORKBENCH_DIR / "resources" / "vg_example"
            if not example.is_dir():
                raise OSError("内置样例不存在，请重新打包工作台")
            for item in example.iterdir():
                dest = VG_ROOT / item.name
                if item.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    for source in item.rglob("*"):
                        target = dest / source.relative_to(item)
                        if source.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        elif not target.exists():
                            shutil.copy2(source, target)
                elif item.is_file() and not dest.exists():
                    shutil.copy2(item, dest)
            settings["vg_library_initialized"] = True
            settings["vg_library_layout"] = "duration-folders-v2"
            save_settings(settings)
        return {"first_run": first_run, "vg_root": str(VG_ROOT)}

def duration_path_key(path):
    return os.path.normcase(str(Path(path).resolve()))

def load_duration_overrides():
    file = DATA_DIR / "duration_overrides.json"
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"files": {}, "folders": {}}

def get_prompt_duration(path, overrides=None):
    try:
        parts = Path(path).resolve().relative_to(VG_ROOT.resolve()).parts
        category = parts[0] if len(parts) >= 3 else ""
    except (ValueError, IndexError):
        category = ""
    match = re.fullmatch(r"(5|10|15)秒", category)
    return {"spec": f"{match.group(1)}s" if match else "",
            "spec_requires_confirmation": not bool(match),
            "duration_source": "directory"}

def save_duration_confirmations(entries):
    raise ValueError("时长由目录决定，请将文件放入 VG 下的 5秒、10秒或15秒目录")

def prompt_choose_directory(initial_dir=""):
    """跨平台弹出系统原生文件资源管理器/访达窗口供用户选择目录"""
    if IS_WIN:
        try:
            # 必须使用独立的外部进程 (PowerShell STA)，严禁在 HTTP 工作线程内调用 Tkinter，
            # 且不能依赖 sys.executable (打包成 EXE 后 sys.executable 是 workbench.exe)。
            norm_init = str(initial_dir or "").replace("/", "\\").replace("'", "''")
            set_path_cmd = f"if (Test-Path '{norm_init}') {{ $f.SelectedPath = '{norm_init}' }}; " if norm_init else ""

            ps_script = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
                "$dummy = New-Object System.Windows.Forms.Form; "
                "$dummy.TopMost = $true; "
                "$dummy.TopLevel = $true; "
                "$dummy.Show(); "
                "$dummy.BringToFront(); "
                "$dummy.Visible = $false; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = '请选择成片保存目录'; "
                "$f.ShowNewFolderButton = $true; "
                f"{set_path_cmd}"
                "if ($f.ShowDialog($dummy) -eq [System.Windows.Forms.DialogResult]::OK) { "
                "    [Console]::WriteLine($f.SelectedPath) "
                "} "
                "$dummy.Dispose(); "
            )

            res = subprocess.run(
                ["powershell.exe", "-Sta", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=180
            )
            if res.returncode == 0:
                out = res.stdout.strip()
                if out:
                    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
                    if lines:
                        selected = lines[-1].replace("\\", "/")
                        print(f"[Dialog] Windows 成功选择成片目录: {selected}")
                        return selected
            print(f"[Dialog] Windows 用户取消选择或未返回目录")
            return ""
        except Exception as e:
            print(f"[Dialog] Windows PowerShell 弹窗选择目录异常: {e}")
            return ""

    if IS_MAC:
        try:
            cmd = ['osascript', '-e', 'POSIX path of (choose folder with prompt "请选择成片保存目录")']
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                p = res.stdout.strip()
                if p:
                    return p.replace("\\", "/")
        except Exception as e:
            print(f"[Dialog] Mac 弹窗选择目录异常: {e}")

    return ""

def get_prod_ref_dir(prod_dir: Path):
    """查找产品专属参考图目录（强隔离，确保该产品下的提示词仅从该目录找图）"""
    if not prod_dir.exists() or not prod_dir.is_dir():
        return None
    for name in ["参考图", "产品参考图", "参考图片", "产品图"]:
        d = prod_dir / name
        if d.exists() and d.is_dir():
            return d
    try:
        for d in prod_dir.iterdir():
            if d.is_dir() and ("参考图" in d.name or d.name.lower() in ["images", "ref", "refs"]):
                return d
    except Exception:
        pass
    return None

def is_valid_product_dir(d: Path):
    """判断是否为有效产品目录（排除系统隐藏与插件目录）"""
    if not d.is_dir() or d.name.startswith(".") or d.name.startswith("__"):
        return False
    if any(kw in d.name.lower() for kw in ["helper", "seedance-", "extension", "node_modules"]):
        return False
    if get_prod_ref_dir(d) is not None:
        return True
    try:
        for f in d.rglob("*.*"):
            if f.suffix.lower() in [".md", ".txt"] and not f.name.startswith("00_") and not f.name.startswith("000_"):
                return True
    except Exception:
        pass
    return False

# 动态探测 Chrome 执行路径
def resolve_chrome_exe():
    env_chrome = os.environ.get("CHROME_PATH")
    if env_chrome and os.path.exists(env_chrome):
        return env_chrome
    if IS_WIN:
        candidates = [
            str(Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return shutil.which("chrome") or "chrome.exe"
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
    custom_dir = os.environ.get("CHROME_USER_DATA_DIR")
    if custom_dir:
        return Path(custom_dir)
    if IS_WIN:
        return Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"))
    elif IS_MAC:
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    else:
        return Path.home() / ".config" / "google-chrome"

CHROME_USER_DATA_DIR = resolve_chrome_user_data_dir()

def get_extension_status(account_id):
    """只读检查指定 Chrome 资料中的插件登记信息，不修改浏览器设置。"""
    if account_id != "Default" and not re.fullmatch(r"Profile \d+", account_id):
        raise ValueError("无效的 Chrome 资料 ID")
    target = ensure_extension_dir()
    found_status = None
    seen_ids = set()
    for name in ("Secure Preferences", "Preferences"):
        pref_file = CHROME_USER_DATA_DIR / account_id / name
        if not pref_file.is_file():
            continue
        try:
            prefs = json.loads(pref_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for ext_id, item in prefs.get("extensions", {}).get("settings", {}).items():
            if ext_id in seen_ids:
                continue
            manifest = item.get("manifest") or {}
            ext_path = item.get("path", "")
            resource_path = Path(ext_path)
            if not resource_path.is_absolute():
                resource_path = CHROME_USER_DATA_DIR / account_id / "Extensions" / resource_path
            manifest_file = resource_path / "manifest.json"
            # Chrome 155 的解压插件记录可能完全省略 manifest，须从其登记目录读取。
            disk_manifest = {}
            if ext_path:
                try:
                    disk_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            if (manifest.get("name") != "VG 豆包生产填词与无水印下载助手"
                    and disk_manifest.get("name") != "VG 豆包生产填词与无水印下载助手"
                    and (not ext_path or resource_path.resolve() != target.resolve())):
                continue
            seen_ids.add(ext_id)
            enabled = item.get("state", 1) == 1 and not item.get("disable_reasons")
            resources_exist = bool(ext_path) and bool(disk_manifest)
            status = {"installed": True, "enabled": enabled and resources_exist,
                    "extension_id": ext_id, "path": ext_path,
                    "install_path": str(target), "version": disk_manifest.get("version", manifest.get("version", "")),
                    "reason": "" if enabled and resources_exist else "插件已停用或目录不存在"}
            if status["enabled"]:
                return status
            if found_status is None:
                found_status = status
    if found_status is not None:
        return found_status
    return {"installed": False, "enabled": False, "install_path": str(target),
            "reason": "此 Chrome 账号尚未安装 VG 插件"}

def open_extension_setup(account_id, open_page=True):
    status = get_extension_status(account_id)
    if open_page:
        if IS_WIN:
            # 与账号启动使用同一个交互式桌面桥梁；EXE 后台直接 Popen Chrome
            # 不能保证窗口出现在用户桌面上，且此前没有检查启动失败。
            launcher_bat = LAUNCHER_BAT
            launcher_bat.write_text(
                f'@echo off\r\nchcp 65001 >nul\r\nstart "" "{CHROME_EXE}" --profile-directory="{account_id}" --new-window "chrome://extensions/"\r\n',
                encoding="utf-8"
            )
            res = run_launch_task()
            if res.returncode != 0:
                raise OSError("无法打开此账号的扩展安装页，请重新启动工作台后重试")
        elif IS_MAC:
            subprocess.Popen(["open", "-na", "Google Chrome", "--args", f"--profile-directory={account_id}", "chrome://extensions/"])
        else:
            subprocess.Popen([CHROME_EXE, f"--profile-directory={account_id}", "--new-window", "chrome://extensions/"])
    return {"code": 2, "setup_required": True, "account_id": account_id,
            "page_opened": open_page,
            "message": "请先在此账号的 Chrome 中安装或启用 VG 插件", "data": status}

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
    bat_path = LAUNCHER_BAT
    bat_path.parent.mkdir(parents=True, exist_ok=True)
    if not bat_path.exists():
        bat_path.write_text("@echo off\r\nchcp 65001 >nul\r\n", encoding="utf-8")
    cmd = ["schtasks", "/create", "/tn", LAUNCH_TASK_NAME, "/tr", f'"{bat_path}"', "/sc", "once", "/st", "23:59", "/it", "/f"]
    subprocess.run(cmd, capture_output=True, check=False)

def run_launch_task():
    result = subprocess.run(["schtasks", "/run", "/tn", LAUNCH_TASK_NAME], capture_output=True, timeout=10)
    if result.returncode != 0:
        # 当前用户没有注册计划任务的权限时，仍可从当前桌面启动 Chrome。
        subprocess.Popen(["cmd.exe", "/d", "/c", str(LAUNCHER_BAT)],
                         creationflags=subprocess.CREATE_NO_WINDOW)
        return subprocess.CompletedProcess(result.args, 0)
    return result

ensure_launch_task()

# ----------------- 窗口切窗与事件调度 -----------------
PENDING_SWITCHES = {}  # { task_id: { "task_id": ..., "account_id": ..., "session_url": ..., "time": ... } }
PENDING_SWITCH_LOCK = threading.Lock()

def focus_chrome_window_native(task_id, scene_title="", account_name=""):
    """使用 Windows 原生 Win32 API 跨会话穿透置顶 Chrome 窗口"""
    if not IS_WIN:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 挂接到当前活跃交互桌面 (WinSta0\\default)
        hwinsta = user32.OpenWindowStationW('WinSta0', False, 0x10000000)
        if hwinsta:
            user32.SetProcessWindowStation(hwinsta)
            hdesk = user32.OpenDesktopW('default', 0, False, 0x10000000)
            if hdesk:
                user32.SetThreadDesktop(hdesk)

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        matched_hwnd = None
        fallback_hwnd = None

        def enum_cb(hwnd, lparam):
            nonlocal matched_hwnd, fallback_hwnd
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value
                    if "Chrome" in title or "豆包" in title:
                        # 1. 优先匹配包含任务指纹 [Vxx] 的标签页
                        if task_id and f"[{task_id}]" in title:
                            matched_hwnd = hwnd
                            return False
                        # 2. 匹配包含场景标题的标签页
                        if scene_title and len(scene_title) > 2 and scene_title in title:
                            matched_hwnd = hwnd
                            return False
                        # 3. 备选匹配账号名称或豆包窗口
                        if account_name and account_name in title:
                            if not fallback_hwnd:
                                fallback_hwnd = hwnd
                        elif "豆包" in title and not fallback_hwnd:
                            fallback_hwnd = hwnd
            return True

        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        target = matched_hwnd or fallback_hwnd
        if target:
            cur_tid = kernel32.GetCurrentThreadId()
            fg_hwnd = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
            if cur_tid != fg_tid:
                user32.AttachThreadInput(cur_tid, fg_tid, True)
            user32.ShowWindow(target, 9)  # SW_RESTORE
            user32.BringWindowToTop(target)
            user32.SetForegroundWindow(target)
            if cur_tid != fg_tid:
                user32.AttachThreadInput(cur_tid, fg_tid, False)
            return True
    except Exception as e:
        logger.warning(f"Native window focus error: {e}")
    return False

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

QUOTA_LOCK = threading.RLock()
QUOTA_TIMEZONE = timezone(timedelta(hours=8))

def quota_today():
    return datetime.now(QUOTA_TIMEZONE).strftime("%Y-%m-%d")

def ensure_daily_quota_reset():
    """UTC+8 每日零点重置；首次升级以原额度文件日期为基准。"""
    with QUOTA_LOCK:
        quota_file = DATA_DIR / "accounts_quota.json"
        marker = DATA_DIR / "quota_reset.json"
        today = quota_today()
        last_date = ""
        if marker.exists():
            try:
                last_date = json.loads(marker.read_text(encoding="utf-8")).get("date", "")
            except (OSError, ValueError):
                pass
        if not last_date:
            last_date = datetime.fromtimestamp(quota_file.stat().st_mtime, QUOTA_TIMEZONE).strftime("%Y-%m-%d") if quota_file.exists() else today
        if last_date < today and quota_file.exists():
            data = json.loads(quota_file.read_text(encoding="utf-8"))
            history = DATA_DIR / "quota_history"
            history.mkdir(exist_ok=True)
            (history / f"quota_{last_date}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            for saved in data.values():
                saved.update(remaining_10s=5, remaining_5s=10, remaining_15s=4)
                if saved.get("status") != "disabled":
                    saved["status"] = "ready"
            quota_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[Quota] 已自动重置 {today} 的账号额度（UTC+8）")
        if not marker.exists() or last_date != today:
            marker.write_text(json.dumps({"date": today, "timezone": "UTC+8"}), encoding="utf-8")

def daily_quota_worker():
    while True:
        try:
            ensure_daily_quota_reset()
        except Exception as e:
            print(f"[Quota] 自动重置失败: {e}")
        time.sleep(30)

def load_accounts_quota():
    """加载或初始化 26 账号今日配额数据"""
    ensure_daily_quota_reset()
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
    with QUOTA_LOCK:
        quota_file = DATA_DIR / "accounts_quota.json"
        history = DATA_DIR / "quota_history"
        history.mkdir(exist_ok=True)
        snapshot = history / f"quota_{quota_today()}.json"
        if quota_file.exists() and not snapshot.exists():
            shutil.copy2(quota_file, snapshot)
        data = {acc["id"]: acc for acc in accounts}
        temp = DATA_DIR / "accounts_quota.json.tmp"
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(quota_file)

def resolve_task_prompt_path(value):
    p = Path(os.path.normpath(value))
    if p.is_file():
        return p
    # 目录调整后，仅按相同脚本名和文件名唯一匹配，避免跨脚本误关联。
    matches = []
    for seconds in ("5秒", "10秒", "15秒"):
        candidate = VG_ROOT / seconds / p.parent.name / p.name
        if candidate.is_file():
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None

def load_daily_tasks():
    tasks_file = DATA_DIR / "daily_tasks.json"
    if tasks_file.exists():
        try:
            data = json.loads(tasks_file.read_text(encoding="utf-8"))
            migrated = False
            # 未发送任务重新检测；用户手动确认的时长及已发送任务保持不变。
            duration_overrides = load_duration_overrides()
            for t in data.get("tasks", []):
                if t.get("path") and (not t.get("sent") or not t.get("spec")):
                    try:
                        p = resolve_task_prompt_path(t["path"])
                        if p:
                            if Path(os.path.normpath(t["path"])) != p:
                                t["path"] = p.as_posix()
                                migrated = True
                            info = get_prompt_duration(p, duration_overrides)
                            if t.get("spec_manual") and info.get("duration_source") == "prompt":
                                continue
                            if t.get("spec") != info["spec"] and not t.get("sent"):
                                t["assignedAccount"] = ""
                            t.update(info)
                        else:
                            t["spec_requires_confirmation"] = True
                    except Exception:
                        t["spec_requires_confirmation"] = True
                elif not t.get("spec") and not t.get("spec_requires_confirmation"):
                    t["spec"] = "10s"
            if migrated:
                save_daily_tasks(data, make_snapshot=True)
            return data
        except Exception:
            pass
    return {
        "date": "",
        "product": "",
        "script": "",
        "tasks": []
    }


def get_task_context(task_id: str = None, account_id: str = None, latest: bool = False, session_url: str = None):
    """根据任务ID或账号获取该任务的完整上下文信息（提示词文本、关联参考图URL列表等）"""
    tasks_data = load_daily_tasks()
    tasks = tasks_data.get("tasks", [])
    target_task = None
    
    if task_id:
        for t in tasks:
            if t.get("id") == task_id:
                target_task = t
                break
    elif session_url:
        m1 = re.search(r'/chat/([A-Za-z0-9_-]+)', session_url)
        c1 = m1.group(1) if m1 and m1.group(1) not in ('new', '') else None
        for t in tasks:
            t_sess = t.get("session_url", "")
            if t_sess:
                if c1:
                    m2 = re.search(r'/chat/([A-Za-z0-9_-]+)', t_sess)
                    c2 = m2.group(1) if m2 and m2.group(1) not in ('new', '') else None
                    if c2 and c1 == c2:
                        target_task = t
                        break
                elif t_sess == session_url:
                    target_task = t
                    break
    elif account_id:
        # 仅在明确指定账号且未传 task_id 时，优先找该账号名下尚未发送的任务
        for t in tasks:
            if t.get("assignedAccount") == account_id and not t.get("sent"):
                target_task = t
                break
        if not target_task:
            for t in tasks:
                if t.get("assignedAccount") == account_id:
                    target_task = t
                    break

    # 严格第一性原理：严禁在未匹配到任务时随意兜底为 tasks[0] 或跨任务覆盖
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

    if not p or not prompt_text.strip():
        raise ValueError("提示词文件不存在或内容为空，请检查任务关联的文件路径并重新添加任务")

    # VG / 秒数 / 脚本文件夹：仅收集任务所属脚本的产品参考图。
    ref_images = []
    prod_name = ""
    if p and p.exists():
        try:
            parts = p.resolve().relative_to(VG_ROOT.resolve()).parts
            script_dir = VG_ROOT / parts[0] / parts[1] if len(parts) >= 3 else None
        except ValueError:
            script_dir = None
        if script_dir:
            prod_name = script_dir.name
            ref_dir = script_dir / "产品参考图"
            if ref_dir.is_dir():
                from urllib.parse import quote
                images = sorted((f for f in ref_dir.rglob("*") if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")),
                                key=lambda f: f.relative_to(ref_dir).as_posix().lower())
                for f in images:
                    # 不按正文筛选、不去重、不截断，完整传输每个图片文件。
                    if not f.resolve().is_relative_to(ref_dir.resolve()):
                        continue
                    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[f.suffix.lower()]
                    b64 = "data:" + mime + ";base64," + base64.b64encode(f.read_bytes()).decode("ascii")
                    ref_images.append({"name": f.relative_to(ref_dir).as_posix(), "filename": f.name,
                                       "path": f.as_posix(), "url": f"http://127.0.0.1:{PORT}/api/image?path={quote(f.as_posix())}",
                                       "base64": b64, "top_level": f.parent == ref_dir})

    # 智能识别视频时长规格 (5s / 10s / 15s)
    spec = target_task.get("spec")
    if not spec and prompt_text:
        spec = get_prompt_duration(p)["spec"]

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
    """扫描 VG 目录结构（全层级深度递归，无遗漏抓取所有有效提示词，支持 .md 与 .txt）"""
    tree = []
    duration_overrides = load_duration_overrides()
    if not VG_ROOT.exists():
        return tree

    candidate_prods = []
    try:
        for item in sorted(VG_ROOT.iterdir()):
            if not item.is_dir() or item.name.startswith("."):
                continue
            # VG / 秒数 / 脚本文件夹：每个脚本单独收集提示词和参考图。
            if re.fullmatch(r"(5|10|15)秒", item.name):
                for sub in sorted(item.iterdir()):
                    if is_valid_product_dir(sub):
                        candidate_prods.append(sub)
    except Exception as e:
        print(f"[ScanTree] 遍历 VG 根目录失败: {e}")
        return tree

    for prod_dir in candidate_prods:
        ref_dir = get_prod_ref_dir(prod_dir)
        prod_data = {
            "name": f"{prod_dir.parent.name} / {prod_dir.name}" if re.fullmatch(r"(5|10|15)秒", prod_dir.parent.name) else prod_dir.name,
            "path": str(prod_dir).replace("\\", "/"),
            "scripts": [],
            "folders": [],
            "ref_images": []
        }

        # 1. 产品专属参考图收集 (强隔离，仅收集该产品 ref_dir 目录下的图片)
        if ref_dir and ref_dir.exists():
            for img in sorted(ref_dir.rglob("*.*")):
                if img.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                    prod_data["ref_images"].append(str(img.relative_to(ref_dir)).replace("\\", "/"))

        # 2. 全层级递归收集所有有效 .md 与 .txt 提示词（排除 00_*.md 以及 成品/实拍/参考图）
        folder_dict = {}
        for p_file in sorted(prod_dir.rglob("*.*")):
            if p_file.suffix.lower() not in [".md", ".txt"]:
                continue
            if p_file.name.startswith("00_") or p_file.name.startswith("000_"):
                continue
            if ref_dir and (p_file == ref_dir or ref_dir in p_file.parents):
                continue
            rel_parts = p_file.relative_to(prod_dir).parts
            if any(p in ["参考图", "产品参考图", "成品", "实拍", "node_modules", ".git"] for p in rel_parts):
                continue

            parent_dir = p_file.parent
            parent_path_str = str(parent_dir).replace("\\", "/")
            rel_folder = str(parent_dir.relative_to(prod_dir)).replace("\\", "/")

            if parent_path_str not in folder_dict:
                folder_dict[parent_path_str] = {
                    "name": parent_dir.name if parent_dir != prod_dir else "产品根目录",
                    "rel_path": rel_folder if rel_folder != "." else "根目录",
                    "path": parent_path_str,
                    "files": []
                }

            duration_info = {"spec": "", "spec_requires_confirmation": True}
            try:
                duration_info = get_prompt_duration(p_file, duration_overrides)
            except OSError:
                pass

            folder_dict[parent_path_str]["files"].append({
                "name": p_file.name,
                "title": p_file.stem,
                "path": str(p_file).replace("\\", "/"),
                "product": prod_dir.name,
                **duration_info
            })

        prod_data["folders"] = list(folder_dict.values())

        # 3. 兼容旧接口的 scripts 结构
        for script_dir in sorted(prod_dir.iterdir()):
            if not script_dir.is_dir() or script_dir.name in ["参考图", "产品参考图", "成品", "实拍"]:
                continue
            script_data = {
                "name": script_dir.name,
                "path": str(script_dir).replace("\\", "/"),
                "masters": [f.name for f in script_dir.glob("*.*") if f.suffix.lower() in [".md", ".txt"] and not f.name.startswith("00_")],
                "variants": []
            }
            for sub_dir in script_dir.rglob("*"):
                if sub_dir.is_dir():
                    md_in_sub = [vf.name for vf in sub_dir.glob("*.*") if vf.suffix.lower() in [".md", ".txt"] and not vf.name.startswith("00_")]
                    if md_in_sub:
                        script_data["variants"].append({
                            "group_name": str(sub_dir.relative_to(script_dir)).replace("\\", "/"),
                            "files": md_in_sub
                        })
            prod_data["scripts"].append(script_data)

        # 只要包含提示词文件或参考图，就作为有效资产呈现
        if prod_data["folders"] or prod_data["ref_images"]:
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
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Access-Control-Request-Private-Network")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Access-Control-Request-Private-Network")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path

        if path == "/api/system/info":
            settings = load_settings()
            self.send_json({
                "code": 0,
                "data": {
                    "platform": "mac" if IS_MAC else ("win" if IS_WIN else "linux"),
                    "vg_root": str(VG_ROOT).replace("\\", "/"),
                    "workbench_dir": str(WORKBENCH_DIR).replace("\\", "/"),
                    "data_dir": str(DATA_DIR),
                    "cookie_export_dir": str(COOKIE_EXPORT_DIR),
                    "chrome_exe": str(CHROME_EXE),
                    "chrome_user_data_dir": str(CHROME_USER_DATA_DIR).replace("\\", "/"),
                    "download_dir": settings.get("download_dir", get_default_download_dir())
                }
            })

        elif path == "/api/extensions/status":
            account_id = parse_qs(url.query).get("account_id", ["Default"])[0]
            try:
                self.send_json({"code": 0, "data": get_extension_status(account_id)})
            except ValueError as e:
                self.send_json({"code": 1, "message": str(e)}, status=400)

        elif path == "/api/settings":
            self.send_json({"code": 0, "data": load_settings()})

        elif path == "/api/accounts":
            accounts = load_accounts_quota()
            self.send_json({"code": 0, "data": accounts})

        elif path == "/api/vg/tree":
            tree = scan_vg_tree()
            self.send_json({"code": 0, "data": tree})

        elif path == "/api/tasks":
            tasks = load_daily_tasks()
            self.send_json({"code": 0, "data": tasks})

        elif path == "/api/tasks/pending_switch":
            # 供扩展 Background 轮询待激活的任务与会话
            now = time.time()
            res_list = []
            with PENDING_SWITCH_LOCK:
                # 过滤出 30 秒内的有效切窗请求
                expired_keys = [k for k, v in PENDING_SWITCHES.items() if now - v.get("time", 0) > 30]
                for k in expired_keys:
                    PENDING_SWITCHES.pop(k, None)
                res_list = list(PENDING_SWITCHES.values())
            self.send_json({"code": 0, "data": res_list})

        elif path == "/api/tampermonkey_script":
            self.send_json({"code": 1, "message": "油猴脚本已弃用，请使用原生 Chrome 扩展 vg_extension！"})

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
            session_url = query.get("session_url", [None])[0]
            latest = "latest" in query
            try:
                ctx = get_task_context(task_id, account_id, latest, session_url=session_url)
            except ValueError as e:
                self.send_json({"code": 1, "message": str(e)}, status=400)
                return
            if ctx:
                self.send_json({"code": 0, "data": ctx})
            else:
                self.send_json({"code": 1, "message": "未找到指定任务上下文"}, status=404)

        elif path == "/api/tasks/download_video_stream":
            # 真实 SSE 进度流下载单个任务成片 (解决假进度与坏文件卡死)
            query = parse_qs(url.query)
            task_id = query.get("task_id", [None])[0]
            force = query.get("force", ["0"])[0] in ["1", "true", "True"]

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            def send_sse(data_dict):
                try:
                    msg = f"data: {json.dumps(data_dict, ensure_ascii=False)}\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass

            if not task_id:
                send_sse({"status": "error", "message": "缺少 task_id 参数"})
                return

            tasks_data = load_daily_tasks()
            all_tasks = tasks_data.get("tasks", [])
            t = next((item for item in all_tasks if item.get("id") == task_id), None)
            if not t:
                send_sse({"status": "error", "message": f"任务 [{task_id}] 不存在"})
                return

            v_url = t.get("video_url")
            if not v_url and t.get("video_urls"):
                v_url = t.get("video_urls")[-1]

            if not v_url:
                send_sse({"status": "error", "message": f"任务 [{task_id}] 尚未捕获到无水印成片链接"})
                return

            # 定位下载保存目录 (支持用户指定目录，若未指定则保存至用户设置或默认桌面成片导出)
            settings = load_settings()
            user_dl_dir = query.get("download_dir", [None])[0] or query.get("save_dir", [None])[0] or settings.get("download_dir")

            target_dir = None
            if user_dl_dir and str(user_dl_dir).strip():
                target_dir = Path(os.path.normpath(str(user_dl_dir).strip()))
            else:
                prod_dir = None
                if t.get("path"):
                    p = Path(os.path.normpath(t["path"]))
                    for parent in p.parents:
                        if get_prod_ref_dir(parent) is not None or parent.parent == VG_ROOT or (parent.parent and parent.parent.name == "提示词及参考图"):
                            prod_dir = parent
                            break
                if prod_dir:
                    target_dir = prod_dir / "成品" / "素材"
                else:
                    target_dir = Path(get_default_download_dir())

            try:
                target_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                send_sse({"status": "error", "message": f"创建成片保存目录失败 [{target_dir}]: {e}"})
                return

            clean_title = re.sub(r'[\\/*?:"<>|]', '_', t.get("title", "未命名")).strip()
            prod_title = re.sub(r'[\\/*?:"<>|]', '_', t.get("product", "")).strip()
            if prod_title and not clean_title.startswith(prod_title):
                filename = f"{t['id']}_{prod_title}_{clean_title}.mp4"
            else:
                filename = f"{t['id']}_{clean_title}.mp4"

            dest_file = target_dir / filename
            tmp_file = target_dir / f"{filename}.tmp"

            # 本地防重检测：若已存在完整且未要求 force，秒级完成
            if dest_file.exists() and dest_file.stat().st_size > 100 * 1024 and not force:
                t["downloaded"] = True
                t["local_video_path"] = str(dest_file).replace("\\", "/")
                save_daily_tasks(tasks_data, make_snapshot=False)
                send_sse({
                    "status": "completed",
                    "pct": 100,
                    "downloaded": dest_file.stat().st_size,
                    "total": dest_file.stat().st_size,
                    "speed": "本地就绪",
                    "file_path": str(dest_file).replace("\\", "/"),
                    "message": "本地已存在完整素材，秒级入库！"
                })
                return

            send_sse({"status": "starting", "pct": 0, "message": "正在连接视频服务器..."})

            try:
                import urllib.request
                req = urllib.request.Request(
                    v_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                        "Referer": "https://www.doubao.com/"
                    }
                )

                start_time = time.time()
                last_push_time = start_time
                downloaded_bytes = 0

                with urllib.request.urlopen(req, timeout=90) as resp, open(tmp_file, "wb") as out_f:
                    total_bytes = 0
                    content_length = resp.headers.get("Content-Length")
                    if content_length and content_length.isdigit():
                        total_bytes = int(content_length)

                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        out_f.write(chunk)
                        downloaded_bytes += len(chunk)

                        now = time.time()
                        if now - last_push_time >= 0.12 or downloaded_bytes == total_bytes:
                            duration = max(0.01, now - start_time)
                            speed_mb = (downloaded_bytes / (1024 * 1024)) / duration
                            pct = min(99, int((downloaded_bytes / total_bytes) * 100)) if total_bytes > 0 else 50
                            send_sse({
                                "status": "downloading",
                                "pct": pct,
                                "downloaded": downloaded_bytes,
                                "total": total_bytes,
                                "speed": f"{speed_mb:.1f} MB/s",
                                "message": f"正在高速下载 ({pct}%)"
                            })
                            last_push_time = now

                # 下载完成，原子性替换为正式 .mp4
                if tmp_file.exists():
                    if dest_file.exists():
                        dest_file.unlink()
                    tmp_file.rename(dest_file)

                t["downloaded"] = True
                t["video_ready"] = True
                t["local_video_path"] = str(dest_file).replace("\\", "/")
                # 严格第一性原理：高频单任务下载不创建历史快照，消除快照雪崩
                save_daily_tasks(tasks_data, make_snapshot=False)

                # 下载完成自动触发关闭对应豆包标签页，释放内存并物理绝杀直链回溯
                with PENDING_SWITCH_LOCK:
                    PENDING_SWITCHES[t["id"]] = {
                        "task_id": t["id"],
                        "action": "close",
                        "session_url": t.get("session_url", ""),
                        "account_id": t.get("assignedAccount", ""),
                        "time": time.time()
                    }

                send_sse({
                    "status": "completed",
                    "pct": 100,
                    "downloaded": downloaded_bytes,
                    "total": downloaded_bytes,
                    "speed": "完成",
                    "file_path": str(dest_file).replace("\\", "/"),
                    "message": "✅ 下载完成并入库！"
                })
            except Exception as e:
                if tmp_file.exists():
                    try:
                        tmp_file.unlink()
                    except Exception:
                        pass
                send_sse({"status": "error", "message": f"下载网络异常: {e}"})
            return

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

        # 每个 Chrome 资料独立安装插件；未就绪时引导安装，不误报任务已启动。
        if path in ("/api/accounts/launch", "/api/accounts/launch_task", "/api/extensions/setup"):
            if path == "/api/accounts/launch_task":
                ids = payload.get("task_ids") or [payload.get("task_id")]
                if any(t.get("id") in ids and t.get("spec_requires_confirmation") for t in load_daily_tasks().get("tasks", [])):
                    self.send_json({"code": 1, "message": "请先确认任务的整段总时长"}, status=400)
                    return
            account_id = payload.get("id" if path == "/api/accounts/launch" else "account_id", "Default")
            try:
                status = get_extension_status(account_id)
                if path == "/api/extensions/setup" or not status["enabled"]:
                    self.send_json(open_extension_setup(account_id, open_page=(path == "/api/extensions/setup")))
                    return
            except (ValueError, OSError, subprocess.SubprocessError) as e:
                self.send_json({"code": 1, "message": str(e)}, status=400)
                return

        if path == "/api/vg/initialize":
            try:
                self.send_json({"code": 0, "data": initialize_vg_library()})
            except OSError as e:
                self.send_json({"code": 1, "message": str(e)}, status=500)

        elif path == "/api/durations/resolve":
            try:
                results = []
                for item in payload.get("paths", []):
                    p = Path(item).resolve()
                    if not p.is_relative_to(VG_ROOT.resolve()) or not p.is_file() or p.suffix.lower() not in (".md", ".txt"):
                        raise ValueError("提示词文件不存在或不在 VG 目录中")
                    results.append({"path": item, "folder": str(p.parent), **get_prompt_duration(p)})
                self.send_json({"code": 0, "data": results})
            except (ValueError, OSError) as e:
                self.send_json({"code": 1, "message": str(e)}, status=400)

        elif path == "/api/durations/confirm":
            try:
                save_duration_confirmations(payload.get("entries", []))
                self.send_json({"code": 0, "message": "时长设置已记住"})
            except (ValueError, OSError) as e:
                self.send_json({"code": 1, "message": str(e)}, status=400)

        elif path == "/api/accounts/calibrate":
            values = {}
            for key in ("remaining_5s", "remaining_10s", "remaining_15s"):
                value = payload.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 999:
                    self.send_json({"code": 1, "message": "剩余次数必须是 0–999 的整数"}, status=400)
                    return
                values[key] = value
            with QUOTA_LOCK:
                accounts = load_accounts_quota()
                account = next((a for a in accounts if a["id"] == payload.get("id")), None)
                if account is None:
                    self.send_json({"code": 1, "message": "账号不存在"}, status=404)
                    return
                account.update(values)
                if account.get("status") != "disabled":
                    account["status"] = "ready" if any(values.values()) else "exhausted"
                save_accounts_quota(accounts)
            self.send_json({"code": 0, "message": "额度已校准", "data": accounts})

        elif path == "/api/accounts/update":
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
                    launcher_bat = LAUNCHER_BAT
                    bat_code = (
                        f'@echo off\r\nchcp 65001 >nul\r\n'
                        f'start "" "{CHROME_EXE}" --profile-directory="{p_id}" "{target_url}"\r\n'
                        f'timeout /t 1 /nobreak >nul\r\n'
                        f'powershell -NoProfile -Command "(New-Object -ComObject WScript.Shell).AppActivate(\'Google Chrome\')"\r\n'
                    )
                    launcher_bat.write_text(bat_code, encoding="utf-8")
                    run_launch_task()
                elif IS_MAC:
                    ext_dir = str(ensure_extension_dir())
                    cmd = [
                        "open", "-na", "Google Chrome", "--args",
                        f"--profile-directory={p_id}",
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
                p = ensure_extension_dir()
            elif target_type == "extension_install":
                p = ensure_extension_dir()
            elif target_type == "product_ref":
                prod_name = payload.get("product")
                if prod_name:
                    p = VG_ROOT / prod_name / "产品参考图"
            elif target_path:
                p = Path(os.path.normpath(target_path))

            if p and p.exists():
                try:
                    if target_type == "extension_install" and IS_WIN:
                        subprocess.Popen(["explorer.exe", "/select,", str(p)])
                    elif target_type == "extension_install" and IS_MAC:
                        subprocess.run(["open", "-R", str(p)], check=True)
                    elif target_type == "extension_install":
                        open_file_or_dir(p.parent)
                    else:
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
            account_id = payload.get("account_id", "Default")
            task_ids = payload.get("task_ids", [])
            if not task_ids and payload.get("task_id"):
                task_ids = [payload.get("task_id")]
            
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
                ext_dir = str(ensure_extension_dir())
                if IS_WIN:
                    launcher_bat = LAUNCHER_BAT
                    bat_code = (
                        f'@echo off\r\nchcp 65001 >nul\r\n'
                        f'start "" "{CHROME_EXE}" --profile-directory="{account_id}" {urls_arg}\r\n'
                    )
                    launcher_bat.write_text(bat_code, encoding="utf-8")
                    run_launch_task()
                elif IS_MAC:
                    cmd = [
                        "open", "-na", "Google Chrome", "--args",
                        f"--profile-directory={account_id}",
                    ] + urls
                    subprocess.Popen(cmd)
                    subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], check=False)
                else:
                    cmd = [CHROME_EXE, f"--profile-directory={account_id}"] + urls
                    subprocess.Popen(cmd)

                self.send_json({"code": 0, "message": f"已成功为账号 [{account_id}] 唤起 {len(urls)} 个任务窗口！"})
            except Exception as e:
                self.send_json({"code": 1, "message": f"唤起失败: {e}"}, status=500)

        elif path == "/api/task/recover_session":
            # 重新唤起已发送但未捕获成片的会话标签页，进入成片打捞模式
            task_id = payload.get("task_id")
            tasks_data = load_daily_tasks()
            all_tasks = tasks_data.get("tasks", [])
            target_task = next((t for t in all_tasks if t.get("id") == task_id), None)
            if not target_task:
                self.send_json({"code": 1, "message": f"未找到任务 [{task_id}]"}, status=404)
                return

            account_id = target_task.get("assignedAccount", "Default")
            if not get_extension_status(account_id)["enabled"]:
                self.send_json(open_extension_setup(account_id, open_page=False))
                return
            session_url = target_task.get("session_url")
            if not session_url:
                session_url = f"https://www.doubao.com/chat/?vg_task={task_id}&vg_view=1"
            elif "vg_task=" not in session_url:
                sep = "&" if "?" in session_url else "?"
                session_url = f"{session_url}{sep}vg_task={task_id}&vg_view=1"

            ext_dir = str(ensure_extension_dir())
            try:
                if IS_WIN:
                    launcher_bat = LAUNCHER_BAT
                    bat_code = (
                        f'@echo off\r\nchcp 65001 >nul\r\n'
                        f'start "" "{CHROME_EXE}" --profile-directory="{account_id}" "{session_url}"\r\n'
                    )
                    launcher_bat.write_text(bat_code, encoding="utf-8")
                    run_launch_task()
                elif IS_MAC:
                    cmd = ["open", "-na", "Google Chrome", "--args", f"--profile-directory={account_id}", session_url]
                    subprocess.Popen(cmd)
                else:
                    subprocess.Popen([CHROME_EXE, f"--profile-directory={account_id}", session_url])

                self.send_json({"code": 0, "message": f"已成功为任务 [{task_id}] 重新唤起会话标签页，进入成片打捞模式！", "session_url": session_url})
            except Exception as e:
                self.send_json({"code": 1, "message": f"唤起失败: {e}"}, status=500)

        elif path == "/api/tasks/switch_window":
            # 瞬间激活特定任务对应的 Chrome 窗口与会话
            task_id = payload.get("task_id", "")
            account_id = payload.get("account_id", "")
            tasks_data = load_daily_tasks()
            all_tasks = tasks_data.get("tasks", [])
            target_task = next((t for t in all_tasks if t.get("id") == task_id), None)
            
            session_url = ""
            scene_title = ""
            if target_task:
                if not account_id:
                    account_id = target_task.get("assignedAccount", "")
                session_url = target_task.get("session_url", "")
                scene_title = target_task.get("title", "")
            
            # 查找账号名称
            accounts = load_accounts_quota()
            acc_obj = next((a for a in accounts if a.get("id") == account_id), None)
            acc_name = acc_obj.get("name", account_id) if acc_obj else account_id
            
            # 1. 注册切窗待办，供 Chrome 扩展 Background ServiceWorker 精准秒切 Tab 会话
            with PENDING_SWITCH_LOCK:
                PENDING_SWITCHES[task_id] = {
                    "task_id": task_id,
                    "account_id": account_id,
                    "session_url": session_url,
                    "time": time.time()
                }

            # 2. Windows 桌面原生穿透置顶 Chrome 窗口
            activated = False
            try:
                if IS_WIN:
                    activated = focus_chrome_window_native(task_id, scene_title, acc_name)
                    if not activated:
                        # 若当前所有窗口中均未找到该任务/账号，则平滑拉起该 Profile 窗口
                        if account_id:
                            # 关键：未绑定会话时带上 &vg_view=1，严禁触发重复自动填词/发图！
                            target_u = session_url if session_url else (f"https://www.doubao.com/chat/?vg_task={task_id}&vg_view=1" if task_id else "https://www.doubao.com/chat/")
                            ext_dir = str(ensure_extension_dir())
                            launcher_bat = LAUNCHER_BAT
                            bat_code = (
                                f'@echo off\r\nchcp 65001 >nul\r\n'
                                f'start "" "{CHROME_EXE}" --profile-directory="{account_id}" "{target_u}"\r\n'
                            )
                            launcher_bat.write_text(bat_code, encoding="utf-8")
                            run_launch_task()
                            activated = True
                self.send_json({"code": 0, "activated": activated, "session_url": session_url, "message": f"已切至任务 [{task_id}] 对应窗口与会话！"})
            except Exception as e:
                self.send_json({"code": 1, "message": f"切换窗口失败: {e}"}, status=500)

        elif path == "/api/tasks/pending_switch/ack":
            task_id = payload.get("task_id", "")
            with PENDING_SWITCH_LOCK:
                PENDING_SWITCHES.pop(task_id, None)
            self.send_json({"code": 0, "message": "切窗已完成回执"})

        elif path == "/api/cookies/upload":
            # 接收扩展上报的解密明文 Cookie 并持久化保存
            profile = payload.get("profile", "")
            cookies = payload.get("cookies", [])
            if not profile or not cookies:
                self.send_json({"code": 1, "message": "缺少 profile 或 cookies"}, status=400)
                return
            
            ref_dir = COOKIE_EXPORT_DIR
            by_profile_dir = ref_dir / "by_profile"
            by_profile_dir.mkdir(parents=True, exist_ok=True)
            
            # 过滤提取纯净 doubao.com cookies
            doubao_cookies = [c for c in cookies if "doubao.com" in c.get("domain", "")]
            if not doubao_cookies:
                doubao_cookies = cookies

            profile_file = by_profile_dir / f"{profile}.json"
            profile_file.write_text(json.dumps(doubao_cookies, ensure_ascii=False, indent=2), encoding="utf-8")
            
            summary_file = ref_dir / "doubao_cookies_all.json"
            all_data = {}
            if summary_file.exists():
                try:
                    all_data = json.loads(summary_file.read_text(encoding="utf-8"))
                except Exception:
                    all_data = {}
            all_data[profile] = doubao_cookies
            summary_file.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
            
            print(f"[Cookie Sync] ✅ 成功捕获并保存 {profile} 真实明文 Cookie: 共 {len(doubao_cookies)} 条")
            self.send_json({"code": 0, "message": f"成功保存 {profile} Cookie", "count": len(doubao_cookies)})

        elif path == "/api/task/bind_session":
            task_id = payload.get("task_id")
            session_url = payload.get("session_url")
            account_id = payload.get("account_id")
            if task_id and session_url:
                tasks_data = load_daily_tasks()
                for t in tasks_data.get("tasks", []):
                    if t.get("id") == task_id:
                        t["session_url"] = session_url
                        if account_id and not t.get("assignedAccount"):
                            t["assignedAccount"] = account_id
                        save_daily_tasks(tasks_data)
                        break
            self.send_json({"code": 0, "message": "会话已绑定"})

        elif path == "/api/task/report_video":
            # 扩展自动上报豆包解密出的无水印视频 URL (支持全局排他防重与多版本追加)
            task_id = payload.get("task_id")
            video_url = payload.get("video_url")
            account_id = payload.get("account_id")
            if not video_url:
                self.send_json({"code": 1, "message": "缺少视频URL"}, status=400)
                return

            tasks_data = load_daily_tasks()
            all_tasks = tasks_data.get("tasks", [])

            def extract_video_key(u: str) -> str:
                if not u:
                    return ""
                m = re.search(r'([a-f0-9]{32})', u)
                if m:
                    return m.group(1)
                return u.split('?')[0]

            new_vkey = extract_video_key(video_url)

            # 全局排他性检查：该视频资源指纹是否已被其他任务绑定
            for t in all_tasks:
                existing_urls = t.get("video_urls", [])
                t_keys = [extract_video_key(t.get("video_url"))] + [extract_video_key(eu) for eu in existing_urls]
                t_keys = [k for k in t_keys if k]
                if (new_vkey and new_vkey in t_keys) or (t.get("video_url") == video_url):
                    if task_id and t.get("id") != task_id:
                        print(f"[Video Hook] 视频已归属任务 [{t.get('id')}]，拒绝错绑至 [{task_id}]")
                        self.send_json({"code": 1, "message": f"视频已被任务 [{t.get('id')}] 绑定，拒绝错绑至 [{task_id}]", "owner_task_id": t.get("id")}, status=409)
                        return
                    else:
                        # 同一任务直链平滑刷新：更新最新带 token 的 url，但坚决保留原下载/成片状态
                        t["video_url"] = video_url
                        t["video_ready"] = True
                        save_daily_tasks(tasks_data, make_snapshot=False)
                        self.send_json({
                            "code": 0,
                            "message": "视频已绑定当前任务 (直链平滑刷新)",
                            "task_id": t.get("id"),
                            "downloaded": bool(t.get("downloaded"))
                        })
                        return

            if not task_id:
                self.send_json({"code": 1, "message": "上报视频必须提供明确的 task_id，严禁模糊猜测"}, status=400)
                return

            target_task = next((t for t in all_tasks if t.get("id") == task_id), None)

            if target_task:
                target_task["video_url"] = video_url
                target_task["video_ready"] = True
                # 资产持久化铁律：若任务之前已标记下载，或本地文件实际已存在于磁盘，严禁倒退为未下载！
                local_path = target_task.get("local_video_path")
                has_local = False
                if local_path:
                    try:
                        p_local = Path(os.path.normpath(local_path))
                        if p_local.exists() and p_local.stat().st_size > 100 * 1024:
                            has_local = True
                    except Exception:
                        pass

                # 进一步检查自定义目录和默认成片目录下是否已有该任务已下载文件
                if not has_local:
                    try:
                        settings = load_settings()
                        check_dirs = [Path(get_default_download_dir())]
                        if settings.get("download_dir"):
                            check_dirs.append(Path(settings["download_dir"]))
                        for cdir in check_dirs:
                            if cdir.exists():
                                for f in cdir.glob(f"{target_task['id']}_*.mp4"):
                                    if f.stat().st_size > 100 * 1024:
                                        has_local = True
                                        target_task["local_video_path"] = str(f).replace("\\", "/")
                                        break
                    except Exception:
                        pass

                if target_task.get("downloaded") or has_local:
                    target_task["downloaded"] = True
                    # 既然已经下载过，通知扩展立即关闭对应标签页，杜绝后续重发
                    with PENDING_SWITCH_LOCK:
                        PENDING_SWITCHES[target_task["id"]] = {
                            "task_id": target_task["id"],
                            "action": "close",
                            "session_url": target_task.get("session_url", ""),
                            "account_id": target_task.get("assignedAccount", ""),
                            "time": time.time()
                        }
                save_daily_tasks(tasks_data, make_snapshot=True)
                print(f"[Video Hook] 严格1对1绑定任务 [{target_task['id']}] 成片 -> {video_url[:60]}... (downloaded={target_task.get('downloaded')})")
                self.send_json({
                    "code": 0,
                    "message": "视频已成功关联任务",
                    "task_id": target_task["id"],
                    "video_url": video_url,
                    "downloaded": bool(target_task.get("downloaded"))
                })
            else:
                self.send_json({"code": 1, "message": "未找到适合关联的任务，已忽略以防错绑"}, status=404)

        elif path == "/api/settings":
            new_settings = load_settings()
            if "download_dir" in payload and payload["download_dir"]:
                new_settings["download_dir"] = str(payload["download_dir"]).strip().replace("\\", "/")
            save_settings(new_settings)
            self.send_json({"code": 0, "message": "配置已保存", "data": new_settings})

        elif path == "/api/settings/choose_download_dir":
            # 弹出系统文件资源管理器供用户选择成片目录
            cur_dir = payload.get("current_dir") or load_settings().get("download_dir", "")
            selected = prompt_choose_directory(cur_dir)
            if selected:
                new_settings = load_settings()
                new_settings["download_dir"] = selected
                save_settings(new_settings)
                self.send_json({"code": 0, "message": "已成功选择并更新目录", "path": selected, "data": new_settings})
            else:
                self.send_json({"code": 1, "message": "已取消选择目录"})

        elif path == "/api/tasks/download_videos":
            # 批量下载已选任务的无水印成片至用户指定目录 (或默认目录)
            task_ids = payload.get("task_ids", [])
            tasks_data = load_daily_tasks()
            all_tasks = tasks_data.get("tasks", [])

            if not task_ids:
                task_ids = [t["id"] for t in all_tasks if (t.get("video_url") or t.get("video_ready")) and not t.get("downloaded")]

            if not task_ids:
                self.send_json({"code": 1, "message": "未勾选或没有可下载的视频任务"}, status=400)
                return

            downloaded_list = []
            failed_list = []
            settings = load_settings()
            user_dl_dir = payload.get("download_dir") or payload.get("save_dir") or settings.get("download_dir")
            target_dir = None

            for tid in task_ids:
                t = next((item for item in all_tasks if item.get("id") == tid), None)
                if not t:
                    failed_list.append({"id": tid, "error": "任务不存在"})
                    continue

                v_url = t.get("video_url")
                if not v_url and t.get("video_urls"):
                    v_url = t.get("video_urls")[-1]

                if not v_url:
                    failed_list.append({"id": tid, "error": "尚未捕获到无水印视频链接"})
                    continue

                # 定位保存目录
                if user_dl_dir and str(user_dl_dir).strip():
                    target_dir = Path(os.path.normpath(str(user_dl_dir).strip()))
                else:
                    prod_dir = None
                    if t.get("path"):
                        p = Path(os.path.normpath(t["path"]))
                        for parent in p.parents:
                            if get_prod_ref_dir(parent) is not None or parent.parent == VG_ROOT or (parent.parent and parent.parent.name == "提示词及参考图"):
                                prod_dir = parent
                                break
                    if prod_dir:
                        target_dir = prod_dir / "成品" / "素材"
                    else:
                        target_dir = Path(get_default_download_dir())

                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    failed_list.append({"id": tid, "error": f"创建目录失败: {e}"})
                    continue

                clean_title = re.sub(r'[\\/*?:"<>|]', '_', t.get("title", "未命名")).strip()
                prod_title = re.sub(r'[\\/*?:"<>|]', '_', t.get("product", "")).strip()
                if prod_title and not clean_title.startswith(prod_title):
                    filename = f"{t['id']}_{prod_title}_{clean_title}.mp4"
                else:
                    filename = f"{t['id']}_{clean_title}.mp4"

                dest_file = target_dir / filename
                tmp_file = target_dir / f"{filename}.tmp"

                # 本地防重检测：如果本地已存在且大小 > 100KB，跳过网络下载
                if dest_file.exists() and dest_file.stat().st_size > 100 * 1024:
                    print(f"[Download] 本地已存在 {filename}，直接标记就绪跳过网络请求")
                    t["downloaded"] = True
                    t["local_video_path"] = str(dest_file).replace("\\", "/")
                    downloaded_list.append({
                        "id": t["id"],
                        "title": t["title"],
                        "filename": filename,
                        "file_path": str(dest_file).replace("\\", "/"),
                        "skipped": True
                    })
                    continue

                try:
                    import urllib.request
                    req = urllib.request.Request(
                        v_url,
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                            "Referer": "https://www.doubao.com/"
                        }
                    )
                    with urllib.request.urlopen(req, timeout=90) as resp, open(tmp_file, "wb") as out_f:
                        while True:
                            chunk = resp.read(256 * 1024)
                            if not chunk:
                                break
                            out_f.write(chunk)

                    if tmp_file.exists():
                        if dest_file.exists():
                            dest_file.unlink()
                        tmp_file.rename(dest_file)

                    t["downloaded"] = True
                    t["local_video_path"] = str(dest_file).replace("\\", "/")
                    downloaded_list.append({
                        "id": t["id"],
                        "title": t["title"],
                        "filename": filename,
                        "file_path": str(dest_file).replace("\\", "/"),
                        "skipped": False
                    })
                except Exception as e:
                    if tmp_file.exists():
                        try:
                            tmp_file.unlink()
                        except Exception:
                            pass
                    failed_list.append({"id": tid, "filename": filename, "error": f"下载网络异常: {e}"})

            if downloaded_list:
                save_daily_tasks(tasks_data, make_snapshot=True)
                with PENDING_SWITCH_LOCK:
                    for item in downloaded_list:
                        target_t = next((x for x in all_tasks if x.get("id") == item["id"]), {})
                        PENDING_SWITCHES[item["id"]] = {
                            "task_id": item["id"],
                            "action": "close",
                            "session_url": target_t.get("session_url", ""),
                            "account_id": target_t.get("assignedAccount", ""),
                            "time": time.time()
                        }

            self.send_json({
                "code": 0,
                "message": f"成功下载 {len(downloaded_list)} 条成片到 [{target_dir}]！" + (f"（{len(failed_list)}条失败）" if failed_list else ""),
                "data": {
                    "downloaded": downloaded_list,
                    "failed": failed_list,
                    "download_dir": str(target_dir).replace("\\", "/") if target_dir else ""
                }
            })

        elif path == "/api/vg/open_material_folder":
            # 打开成片目录：优先打开用户指定的 download_dir
            settings = load_settings()
            user_dl_dir = payload.get("download_dir") or payload.get("path") or settings.get("download_dir")
            target_dir = None
            if user_dl_dir and Path(user_dl_dir).exists():
                target_dir = Path(user_dl_dir)
            else:
                prod_name = payload.get("prod_name") or payload.get("product")
                if prod_name:
                    p = VG_ROOT / prod_name / "成品" / "素材"
                    if p.exists():
                        target_dir = p
                if not target_dir:
                    tasks_data = load_daily_tasks()
                    tasks = tasks_data.get("tasks", [])
                    for t in tasks:
                        if t.get("local_video_path") and Path(t["local_video_path"]).parent.exists():
                            target_dir = Path(t["local_video_path"]).parent
                            break
                if not target_dir:
                    def_dir = Path(get_default_download_dir())
                    def_dir.mkdir(parents=True, exist_ok=True)
                    target_dir = def_dir

            if target_dir and target_dir.exists():
                open_file_or_dir(target_dir)
                self.send_json({"code": 0, "message": f"已打开目录: {target_dir}", "path": str(target_dir).replace("\\", "/")})
            else:
                self.send_json({"code": 1, "message": "未定位到素材目录"}, status=404)

        else:
            self.send_json({"code": 404, "message": "未知接口"}, status=404)

def run():
    ensure_extension_dir()
    server_address = ("", PORT)
    httpd = ThreadingHTTPServer(server_address, WorkbenchHandler)
    ensure_daily_quota_reset()
    threading.Thread(target=daily_quota_worker, daemon=True).start()
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
