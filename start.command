#!/bin/bash
# 切换到脚本所在当前目录
cd "$(dirname "$0")"

echo "=========================================="
echo "🚀 启动 VG 视频生产工作台 (macOS 双击启动)"
echo "=========================================="

# 检测 Python3
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "❌ 错误: 未检测到 Python 环境，请先安装 Python 3.9+"
    read -p "按回车键退出..."
    exit 1
fi

PORT=8765

# 检查端口占用并提示
if lsof -i :$PORT >/dev/null 2>&1; then
    echo "⚠️ 端口 $PORT 已被占用，可能是旧服务正在运行。"
    echo "🌐 正在尝试打开浏览器: http://127.0.0.1:$PORT"
    open "http://127.0.0.1:$PORT"
    exit 0
fi

# 启动并在 1 秒后自动打开浏览器
(sleep 1 && open "http://127.0.0.1:$PORT") &

echo "🌐 启动本地服务: http://127.0.0.1:$PORT"
$PYTHON_CMD server.py
