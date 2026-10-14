@echo off
chcp 65001 >nul
title 正在打包 VG 视频工作台 EXE 单文件...
echo ===================================================
echo 📦 正在使用 PyInstaller 打包 VG 工作台为单文件 EXE
echo ===================================================
cd /d "%~dp0"

echo [1/3] 正在检查打包依赖...
python -c "import PyInstaller" 2>nul
if %errorlevel% neq 0 (
    echo 正在安装 pyinstaller...
    pip install pyinstaller
)

echo [2/3] 正在执行编译打包 (内置前端与插件模板)...
pyinstaller --noconsole --onefile --clean ^
    --name="VG工作台" ^
    --add-data "public;public" ^
    --add-data "vg_extension;vg_extension" ^
    --add-data "resources\vg_example;vg_example" ^
    server.py

if %errorlevel% neq 0 (
    echo.
    echo ❌ 打包失败，请检查上方错误提示！
    pause
    exit /b %errorlevel%
)

echo.
echo ===================================================
echo ✅ 打包完成！
echo 产物路径: dist\VG工作台.exe
echo.
echo 💡 使用说明：
echo 将 dist\VG工作台.exe 直接发送给员工即可！
echo 员工在任意 Windows 电脑上双击即可直接启动，自动探测注册表与 Chrome 资料，
echo 浏览器插件已全量内置。首次打开每个账号时，按工作台引导加载一次插件。
echo ===================================================
pause

