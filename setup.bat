@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  AI Code Agent - Windows Setup
echo ============================================================
echo.

:: Python チェック
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python が見つかりません。
    echo         https://www.python.org/ からインストールしてください。
    pause
    exit /b 1
)

:: venv 作成
if not exist "venv\Scripts\activate.bat" (
    echo [1/4] 仮想環境を作成中...
    python -m venv venv
    if errorlevel 1 ( echo [ERROR] venv の作成に失敗しました。 & pause & exit /b 1 )
) else (
    echo [1/4] 仮想環境は作成済みです。
)

:: 依存パッケージインストール
echo [2/4] パッケージをインストール中...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [ERROR] pip install に失敗しました。 & pause & exit /b 1 )

:: .env 作成
if not exist ".env" (
    echo [3/4] .env を作成中...
    copy ".env.example" ".env" >nul
    echo       .env を作成しました。http://localhost:8001/setup で設定してください。
) else (
    echo [3/4] .env は作成済みです。
)

:: サーバー起動
echo [4/4] サーバーを起動中... (ポート 8001)
echo.
echo  チャット UI: http://localhost:8001
echo  設定画面  : http://localhost:8001/setup
echo.
echo  停止するには Ctrl+C を押してください。
echo ============================================================
echo.
venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8001

pause
