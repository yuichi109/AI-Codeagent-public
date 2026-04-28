@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\pythonw.exe" (
    echo 初回セットアップが必要です。setup.bat を実行してください。
    pause
    exit /b 1
)

:: pythonw.exe = コンソールウィンドウなしで実行
start "" "venv\Scripts\pythonw.exe" "%~dp0tray.py"
