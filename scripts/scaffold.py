#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scaffold.py - VG 资产目录体系规范脚手架与校验器 (全平台自适应版: macOS / Windows / Linux)
用于自动创建符合规范的 VG 产品目录、脚本目录、变体目录及卡片式说明/索引文件，并自动联动更新上级索引。
"""

import os
import sys
import re
import json
import argparse
from pathlib import Path

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

def resolve_default_vg_root() -> Path:
    """自适应探测 VG 资产根目录 (macOS / Windows)"""
    env_root = os.environ.get("VG_ROOT") or os.environ.get("VG_ROOT_DIR")
    if env_root and Path(env_root).exists():
        return Path(env_root)
    
    # 1. 尝试当前工作台同级或上级 ../VG
    script_dir = Path(__file__).resolve().parent
    for rel in [script_dir.parent.parent / "VG", script_dir.parent / "VG", Path.cwd().parent / "VG", Path.cwd() / "VG"]:
        if rel.exists() and rel.is_dir():
            return rel
            
    # 2. macOS 优先桌面 VG 或 DeskBox
    if IS_MAC:
        mac_desktop = Path.home() / "Desktop" / "VG"
        if mac_desktop.exists() and mac_desktop.is_dir():
            return mac_desktop
        mac_deskbox = Path.home() / "DeskBox" / "其他" / "VG"
        if mac_deskbox.exists() and mac_deskbox.is_dir():
            return mac_deskbox
        return mac_desktop

    # 3. Windows 常见路径
    if IS_WIN:
        win_deskbox = Path(r"C:\Users\admin\DeskBox\其他\VG")
        if win_deskbox.exists() and win_deskbox.is_dir():
            return win_deskbox
        win_desktop = Path.home() / "Desktop" / "VG"
        if win_desktop.exists() and win_desktop.is_dir():
            return win_desktop
        return win_deskbox

    return Path.home() / "VG"

DEFAULT_VG_ROOT = resolve_default_vg_root()

def get_next_product_number(vg_root: Path) -> int:
    """扫描现有产品文件夹，获取下一个三位数序号"""
    max_num = 0
    if not vg_root.exists():
        return 1
    for item in vg_root.iterdir():
        if item.is_dir():
            match = re.match(r"^(\d{3})_", item.name)
            if match:
                num = int(match.group(1))
                if num > max_num:
                    max_num = num
    return max_num + 1

def update_vg_navigation(vg_root: Path, product_num_str: str, product_name: str, category: str = "车品内饰"):
    """在 000_VG总导航.md 中追加或更新新产品条目 (若不存在则自动初始化)"""
    nav_file = vg_root / "000_VG总导航.md"
    folder_name = f"{product_num_str}_{product_name}"
    
    if not nav_file.exists():
        initial_content = f"""# VG 视频资产总导航与索引

> 全局资产体系规范守门：一级产品目录 `0XX_产品名`，二级脚本目录，三级变体库。

---

## 📂 全部产品一览表

| 编号 | 文件夹名称 | 品类 | 索引状态 |
| :--- | :--- | :--- | :--- |
| **{product_num_str}** | **[{folder_name}]({folder_name}/)** | {category} | **已建立完整索引**，详见 [00_脚本索引.md]({folder_name}/00_脚本索引.md) |

---
"""
        try:
            nav_file.write_text(initial_content, encoding="utf-8")
            print(f"[OK] 已自动初始化 000_VG总导航.md: 追加 {folder_name}")
        except Exception as e:
            print(f"[WARN] 初始化 000_VG总导航.md 失败: {e}", file=sys.stderr)
        return

    content = nav_file.read_text(encoding="utf-8")
    # 检查是否已存在
    if folder_name in content:
        return
    
    # 在“## 📂 全部产品一览表”表格末尾追加行
    new_row = f"| **{product_num_str}** | **[{folder_name}]({folder_name}/)** | {category} | **已建立完整索引**，详见 [00_脚本索引.md]({folder_name}/00_脚本索引.md) |\n"
    
    table_pattern = r"(\|.*?编号.*?\|.*?文件夹名称.*?\|\n\|.*?\n)([\s\S]*?)(\n---|\Z)"
    match = re.search(table_pattern, content)
    if match:
        table_header = match.group(1)
        table_body = match.group(2).rstrip()
        tail = match.group(3)
        updated_content = content[:match.start()] + table_header + table_body + "\n" + new_row + tail
        nav_file.write_text(updated_content, encoding="utf-8")
        print(f"[OK] 已同步更新 000_VG总导航.md: 追加 {folder_name}")

def scaffold_product(vg_root: Path, product_name: str, category: str = "车品配件", desc: str = "产品卖点与定位"):
    """新建产品目录及标准全套骨架"""
    vg_root.mkdir(parents=True, exist_ok=True)
    next_num = get_next_product_number(vg_root)
    num_str = f"{next_num:03d}"
    folder_name = f"{num_str}_{product_name}"
    product_dir = vg_root / folder_name
    
    if product_dir.exists():
        print(f"[ERROR] 目标产品目录已存在: {product_dir}", file=sys.stderr)
        return False
    
    # 创建目录
    product_dir.mkdir(parents=True, exist_ok=True)
    (product_dir / "产品参考图").mkdir(exist_ok=True)
    (product_dir / "成品").mkdir(exist_ok=True)
    (product_dir / "实拍").mkdir(exist_ok=True)
    
    # 生成 00_脚本索引.md
    index_content = f"""# {num_str}_{product_name} 脚本索引

> 产品定位：{desc}  
> 根目录路径：`{product_dir.as_posix()}`  

---

## 脚本速览

（暂无脚本，创建脚本目录后将自动在此登记）

---

## 资源导航
- 产品参考图：[产品参考图/](产品参考图/)
- 实拍素材：[实拍/](实拍/)
- 成品视频：[成品/](成品/)
"""
    (product_dir / "00_脚本索引.md").write_text(index_content, encoding="utf-8")
    print(f"[OK] 成功创建产品目录: {product_dir}")
    print(f"  ├─ 产品参考图/")
    print(f"  ├─ 成品/")
    print(f"  ├─ 实拍/")
    print(f"  └─ 00_脚本索引.md")
    
    # 同步更新总导航
    update_vg_navigation(vg_root, num_str, product_name, category)
    return True

def update_product_index(product_dir: Path, script_name: str, desc: str, spec: str, main_prompt: str, variants_dir: str):
    """在产品的 00_脚本索引.md 中追加脚本卡片"""
    index_file = product_dir / "00_脚本索引.md"
    if not index_file.exists():
        return
    content = index_file.read_text(encoding="utf-8")
    if f"### 📁 [{script_name}]" in content:
        return
    
    card = f"""
### 📁 [{script_name}]({script_name}/)
- 【核心看点】{desc}
- 【规格形式】{spec}
- 【主提示词】[{main_prompt}]({script_name}/{main_prompt})
- 【变体场景】[{variants_dir}/]({script_name}/{variants_dir}/)
- 【对应成品】暂未关联

---
"""
    # 替换占位符或追加
    if "（暂无脚本，创建脚本目录后将自动在此登记）" in content:
        content = content.replace("（暂无脚本，创建脚本目录后将自动在此登记）", card.strip() + "\n")
    else:
        # 插入在 ## 资源导航 之前
        if "## 资源导航" in content:
            parts = content.split("## 资源导航")
            content = parts[0].rstrip() + "\n\n" + card.strip() + "\n\n---\n\n## 资源导航" + parts[1]
        else:
            content = content.rstrip() + "\n\n" + card.strip()
    
    index_file.write_text(content, encoding="utf-8")
    print(f"[OK] 已同步更新 {product_dir.name}/00_脚本索引.md")

def scaffold_script(product_dir: Path, script_name: str, desc: str = "核心视觉噱头与剧情反差", spec: str = "15秒一镜直出 / 9:16 / 写实手机UGC"):
    """在产品目录下新建脚本子目录及 00_脚本说明.md"""
    if not product_dir.exists():
        print(f"[ERROR] 产品目录不存在: {product_dir}", file=sys.stderr)
        return False
    
    script_dir = product_dir / script_name
    if script_dir.exists():
        print(f"[ERROR] 脚本目录已存在: {script_dir}", file=sys.stderr)
        return False
    
    script_dir.mkdir(parents=True, exist_ok=True)
    (script_dir / "迁移").mkdir(exist_ok=True)
    
    master_prompt_name = f"{script_name} V1.md"
    master_prompt_file = script_dir / master_prompt_name
    master_prompt_file.write_text(f"# {script_name} 母版提示词\n\n（请在此写入母版分镜提示词）\n", encoding="utf-8")
    
    readme_content = f"""# 脚本说明：{script_name}（{desc}）

- 【脚本定位】{spec}
- 【核心卖点】{desc}
- 【剧情脉络】
  1. 开场：前3秒黄金钩子
  2. 过程：核心动作与产品功能展示
  3. 结尾：效果呈现与CTA引导
- 【核心文件】
  - 主版本：[{master_prompt_name}]({master_prompt_name})
  - 迁移变体库：[迁移/](迁移/)
- 【对应成品视频】暂无
"""
    (script_dir / "00_脚本说明.md").write_text(readme_content, encoding="utf-8")
    print(f"[OK] 成功创建脚本目录: {script_dir}")
    print(f"  ├─ {master_prompt_name}")
    print(f"  ├─ 迁移/")
    print(f"  └─ 00_脚本说明.md")
    
    update_product_index(product_dir, script_name, desc, spec, master_prompt_name, "迁移")
    return True

def get_next_variant_index(variant_dir: Path, code: str, version: str) -> int:
    """扫描变体目录，查找下一个两位数序号"""
    max_idx = 0
    if not variant_dir.exists():
        return 1
    pattern = re.compile(rf"^{re.escape(code)}_{re.escape(version)}_(\d+)_", re.IGNORECASE)
    for f in variant_dir.glob("*.md"):
        m = pattern.match(f.name)
        if m:
            val = int(m.group(1))
            if val > max_idx:
                max_idx = val
    return max_idx + 1

def scaffold_variant(script_dir: Path, variant_subfolder: str, code: str, version: str, scene_name: str, content: str = ""):
    """在脚本目录下创建或写入变体提示词文件"""
    if not script_dir.exists():
        print(f"[ERROR] 脚本目录不存在: {script_dir}", file=sys.stderr)
        return False
    
    target_dir = script_dir / variant_subfolder
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # 防重检测：检查该场景名称是否已存在
    for f in target_dir.glob("*.md"):
        if scene_name in f.stem:
            print(f"[ERROR] 场景防重拦截：已存在同场景文件 {f.name}，严禁重复生成！", file=sys.stderr)
            return False
    
    next_idx = get_next_variant_index(target_dir, code, version)
    file_name = f"{code}_{version}_{next_idx:02d}_{scene_name}.md"
    target_file = target_dir / file_name
    
    if not content.strip():
        content = f"# {file_name}\n\n15s，9:16，写实手机 UGC 产品视频。场景：{scene_name}。\n"
    
    target_file.write_text(content, encoding="utf-8")
    print(f"[OK] 成功生成合规变体提示词文件: {target_file.name}")
    return True

def check_vg_tree(vg_root: Path) -> bool:
    """全面体检 VG 资产树完整性与合规度"""
    print(f"=== 开始 VG 资产目录体系健康体检 ===")
    print(f"根目录: {vg_root}\n")
    
    if not vg_root.exists():
        print(f"[ERROR] 根目录不存在: {vg_root}", file=sys.stderr)
        return False

    all_passed = True
    aux_dirs = {'产品参考图', '成品', '实拍', '未启用', '废', '废片', '电商图', '实拍图RAW', '素材', '产品信息', '口播', '_archive_废弃', '.ts'}
    
    # 1. 检查根目录杂项文件
    root_files = [f.name for f in vg_root.iterdir() if f.is_file()]
    for rf in root_files:
        if rf != "000_VG总导航.md":
            print(f"[WARN] 根目录下存在非标杂项文件: {rf}")
            all_passed = False
            
    # 2. 检查各产品目录
    product_dirs = [p for p in sorted(vg_root.iterdir()) if p.is_dir() and re.match(r"^\d{3}_", p.name)]
    print(f"发现产品目录数量: {len(product_dirs)} 款")
    
    prod_errors = 0
    script_count = 0
    missing_script_readmes = 0
    
    for p in product_dirs:
        missing_std = []
        for std in ['产品参考图', '成品', '实拍']:
            if not (p / std).is_dir():
                missing_std.append(std)
        if not (p / '00_脚本索引.md').is_file():
            missing_std.append('00_脚本索引.md')
            
        if missing_std:
            print(f"[FAIL] {p.name} 缺失标准项: {missing_std}")
            prod_errors += 1
            all_passed = False
            
        # 检查下属脚本目录
        for sub in sorted(p.iterdir()):
            if not sub.is_dir() or sub.name in aux_dirs:
                continue
            script_count += 1
            if not (sub / '00_脚本说明.md').is_file():
                print(f"[FAIL] 脚本目录缺失 00_脚本说明.md: {p.name}/{sub.name}")
                missing_script_readmes += 1
                all_passed = False
                
    print("\n--- 体检结果统计 ---")
    print(f"产品总数: {len(product_dirs)} | 产品骨架异常: {prod_errors}")
    print(f"脚本总数: {script_count} | 缺失说明文件: {missing_script_readmes}")
    if all_passed:
        print("[SUCCESS] 🎉 100% 通过！所有产品及下属脚本目录完全符合 VG 三级资产树标准！")
    else:
        print("[WARNING] 存在未通过项，请对照上方提示进行整改。")
    return all_passed

def main():
    root_parser = argparse.ArgumentParser(add_help=False)
    root_parser.add_argument("--root", default=str(DEFAULT_VG_ROOT), help="VG根目录路径")
    
    parser = argparse.ArgumentParser(description="VG 目录规范脚手架工具 (macOS / Windows / Linux)", parents=[root_parser])
    subparsers = parser.add_subparsers(dest="command")
    
    # check
    p_check = subparsers.add_parser("check", parents=[root_parser], help="体检资产树规范与索引完整性")
    
    # product
    p_prod = subparsers.add_parser("new-product", parents=[root_parser], help="新建产品目录")
    p_prod.add_argument("--name", required=True, help="产品中文名称，如'双色流光灯'")
    p_prod.add_argument("--category", default="车品配件", help="品类说明")
    p_prod.add_argument("--desc", default="核心卖点与定位", help="简要说明")
    
    # script
    p_script = subparsers.add_parser("new-script", parents=[root_parser], help="新建脚本目录")
    p_script.add_argument("--product", required=True, help="产品目录名或编号，如'012_脚底灯'或'012'")
    p_script.add_argument("--name", required=True, help="脚本目录名，如'直出脚本F'")
    p_script.add_argument("--desc", default="核心看点", help="脚本看点")
    p_script.add_argument("--spec", default="15秒一镜直出 / 9:16 / 写实手机UGC", help="规格形式")
    
    # variant
    p_var = subparsers.add_parser("new-variant", parents=[root_parser], help="新建变体提示词文件")
    p_var.add_argument("--product", required=True, help="产品目录")
    p_var.add_argument("--script", required=True, help="脚本目录")
    p_var.add_argument("--subdir", default="迁移", help="变体子目录名，如'迁移_美式暗景挂件玩偶'")
    p_var.add_argument("--code", required=True, help="脚本代号，如'C'")
    p_var.add_argument("--version", default="V2", help="版本号，如'V2'")
    p_var.add_argument("--scene", required=True, help="场景名，如'美式公路餐厅'")
    
    args = parser.parse_args()
    vg_root = Path(args.root)
    
    if args.command == "check":
        check_vg_tree(vg_root)
    elif args.command == "new-product":
        scaffold_product(vg_root, args.name, args.category, args.desc)
    elif args.command == "new-script":
        if not vg_root.exists():
            print(f"[ERROR] VG 根目录不存在: {vg_root}", file=sys.stderr)
            sys.exit(1)
        # 寻找匹配产品目录
        prod_dir = None
        for item in vg_root.iterdir():
            if item.is_dir() and (item.name == args.product or item.name.startswith(f"{args.product}_")):
                prod_dir = item
                break
        if not prod_dir:
            print(f"[ERROR] 找不到匹配的产品目录: {args.product}", file=sys.stderr)
            sys.exit(1)
        scaffold_script(prod_dir, args.name, args.desc, args.spec)
    elif args.command == "new-variant":
        if not vg_root.exists():
            print(f"[ERROR] VG 根目录不存在: {vg_root}", file=sys.stderr)
            sys.exit(1)
        # 寻找产品及脚本目录
        prod_dir = None
        for item in vg_root.iterdir():
            if item.is_dir() and (item.name == args.product or item.name.startswith(f"{args.product}_")):
                prod_dir = item
                break
        if not prod_dir:
            print(f"[ERROR] 找不到产品: {args.product}", file=sys.stderr)
            sys.exit(1)
        script_dir = prod_dir / args.script
        scaffold_variant(script_dir, args.subdir, args.code, args.version, args.scene)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
