#!/bin/bash
# ========================================================
# VG 视频生产与账号矩阵工作台 (macOS 启动脚本)
# ========================================================

# 获取脚本所在目录
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "========================================================"
echo "🚀 正在启动 VG 生产工作台 (macOS)..."
echo "📂 当前目录: $DIR"
echo "========================================================"

# 查找可用 python3
PYTHON_CMD="python3"
if ! command -v python3 &> /dev/null; then
    if command -v python &> /dev/null; then
        PYTHON_CMD="python"
    else
        echo "❌ 错误: 未检测到 Python 环境，请先安装 Python 3 (https://www.python.org/ 或 brew install python)"
        exit 1
    fi
fi

# 检查服务端口 8765
PORT=8765
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "⚡ 端口 $PORT 已有服务在运行，准备直接打开网页..."
    open "http://127.0.0.1:$PORT"
    exit 0
fi

# 启动服务并在后台运行或前台交互
echo "🌐 启动本地服务: http://127.0.0.1:$PORT"
$PYTHON_CMD server.py
