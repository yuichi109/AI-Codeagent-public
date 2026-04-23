@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  AI Code Agent - Windows Setup
echo ============================================================
echo.

:: ─── winget チェック ──────────────────────────────────────
set HAVE_WINGET=0
winget --version >nul 2>&1
if not errorlevel 1 set HAVE_WINGET=1

:: ─── Python チェック / 自動インストール ──────────────────
python --version >nul 2>&1
if errorlevel 1 (
    :: py ランチャーも試す
    py --version >nul 2>&1
    if not errorlevel 1 (
        doskey python=py $*
        set "PYTHON_CMD=py"
        goto python_ok
    )
    echo [--] Python が見つかりません。
    if %HAVE_WINGET%==1 (
        echo      winget で Python 3.12 をインストール中...
        winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        if errorlevel 1 (
            echo [ERROR] winget インストール失敗。手動でインストールしてください:
            echo         https://www.python.org/downloads/
            echo         ※ "Add Python to PATH" にチェックを入れてください。
            pause & exit /b 1
        )
        :: 既知のインストール先を PATH に追加してリトライ
        set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python312"
        set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python312\Scripts"
        set "PATH=%PATH%;C:\Program Files\Python312"
        set "PATH=%PATH%;C:\Program Files\Python312\Scripts"
        python --version >nul 2>&1
        if errorlevel 1 (
            echo [INFO] インストール完了。このウィンドウを閉じて setup.bat を再実行してください。
            pause & exit /b 0
        )
    ) else (
        echo [ERROR] winget が使えません。Python を手動でインストールしてください:
        echo         https://www.python.org/downloads/
        echo         ※ "Add Python to PATH" にチェックを入れてください。
        pause & exit /b 1
    )
)
:python_ok
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK] %%v

:: ─── Git チェック / 自動インストール ─────────────────────
git --version >nul 2>&1
if errorlevel 1 (
    echo [--] Git が見つかりません。
    if %HAVE_WINGET%==1 (
        echo      winget で Git をインストール中...
        winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements
        if errorlevel 1 (
            echo [ERROR] winget インストール失敗。手動でインストールしてください:
            echo         https://git-scm.com/download/win
            pause & exit /b 1
        )
        set "PATH=%PATH%;C:\Program Files\Git\cmd;C:\Program Files\Git\bin"
        git --version >nul 2>&1
        if errorlevel 1 (
            echo [INFO] インストール完了。このウィンドウを閉じて setup.bat を再実行してください。
            pause & exit /b 0
        )
    ) else (
        echo [ERROR] winget が使えません。Git を手動でインストールしてください:
        echo         https://git-scm.com/download/win
        pause & exit /b 1
    )
)
for /f "tokens=*" %%v in ('git --version 2^>^&1') do echo [OK] %%v

echo.

:: ─── venv 作成 ────────────────────────────────────────────
if not exist "venv\Scripts\activate.bat" (
    echo [1/4] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 ( echo [ERROR] venv の作成に失敗しました。 & pause & exit /b 1 )
) else (
    echo [1/4] Virtual environment already exists.
)

:: ─── パッケージインストール ───────────────────────────────
echo [2/4] Installing packages...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [ERROR] pip install に失敗しました。 & pause & exit /b 1 )

:: ─── .env 作成 ────────────────────────────────────────────
if not exist ".env" (
    echo [3/4] Creating .env...
    copy ".env.example" ".env" >nul
    echo       .env を作成しました。http://localhost:8001/setup で設定してください。
) else (
    venv\Scripts\python.exe -c "open('.env', encoding='utf-8').read()" >nul 2>&1
    if errorlevel 1 (
        echo [3/4] .env のエンコーディング異常を検出 - .env.example から再作成します...
        copy ".env.example" ".env" >nul
        echo       .env を再作成しました。http://localhost:8001/setup で再設定してください。
    ) else (
        echo [3/4] .env already exists.
    )
)

:: ─── サーバー起動（設定保存後に自動再起動）────────────────
echo [4/4] Starting server on port 8001...
echo.
echo   Chat UI : http://localhost:8001
echo   Setup   : http://localhost:8001/setup
echo.
echo   Press Ctrl+C to stop.
echo ============================================================
echo.

:start_server
venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8001
if errorlevel 1 goto end
echo Restarting server...
goto start_server

:end
pause
