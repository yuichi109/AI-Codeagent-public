@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  AI Code Agent - Windows Setup
echo ============================================================
echo.

set "PY_EXE="
set "HAVE_WINGET=0"

winget --version >nul 2>&1
if not errorlevel 1 set "HAVE_WINGET=1"

:: ─── Python 検索 / インストール ──────────────────────────────
call :find_python
if not defined PY_EXE (
    call :install_python
    call :find_python
)
if not defined PY_EXE (
    echo [ERROR] Python が見つかりません。手動でインストールしてください:
    echo         https://www.python.org/downloads/
    echo         インストール時に "Add Python to PATH" にチェックを入れてください。
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('"%PY_EXE%" --version 2^>^&1') do echo [OK] %%v

:: ─── Git 検索 / インストール ─────────────────────────────────
call :find_git
if not defined GIT_FOUND (
    call :install_git
    call :find_git
    if not defined GIT_FOUND (
        echo [INFO] Git のインストール完了。このウィンドウを閉じて setup.bat を再実行してください。
        pause & exit /b 0
    )
)
for /f "tokens=*" %%v in ('git --version 2^>^&1') do echo [OK] %%v

echo.

:: ─── venv 作成 ────────────────────────────────────────────────
if not exist "venv\Scripts\activate.bat" (
    echo [1/4] Creating virtual environment...
    "%PY_EXE%" -m venv venv
    if errorlevel 1 ( echo [ERROR] venv の作成に失敗しました。 & pause & exit /b 1 )
) else (
    echo [1/4] Virtual environment already exists.
)

:: ─── パッケージインストール ───────────────────────────────────
echo [2/4] Installing packages...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [ERROR] pip install に失敗しました。 & pause & exit /b 1 )

:: ─── .env 作成 ────────────────────────────────────────────────
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

:: ─── サーバー起動（設定保存後に自動再起動）────────────────────
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
goto :eof

:: =============================================================
:: サブルーチン
:: =============================================================

:find_python
:: 1. PATH 上の python / py
python --version >nul 2>&1
if not errorlevel 1 ( set "PY_EXE=python" & goto :eof )
py --version >nul 2>&1
if not errorlevel 1 ( set "PY_EXE=py" & goto :eof )
:: 2. 既知のインストール先を直接確認（バージョン 313〜310）
for %%v in (313 312 311 310) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe" (
        set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe"
        set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python%%v;%LOCALAPPDATA%\Programs\Python\Python%%v\Scripts"
        goto :eof
    )
    if exist "C:\Program Files\Python%%v\python.exe" (
        set "PY_EXE=C:\Program Files\Python%%v\python.exe"
        set "PATH=%PATH%;C:\Program Files\Python%%v;C:\Program Files\Python%%v\Scripts"
        goto :eof
    )
)
goto :eof

:install_python
if %HAVE_WINGET%==0 goto :eof
echo [--] Python が見つかりません。winget で Python 3.12 をインストール中...
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
goto :eof

:find_git
set "GIT_FOUND="
git --version >nul 2>&1
if not errorlevel 1 ( set "GIT_FOUND=1" & goto :eof )
if exist "C:\Program Files\Git\cmd\git.exe" (
    set "PATH=%PATH%;C:\Program Files\Git\cmd;C:\Program Files\Git\bin"
    set "GIT_FOUND=1"
)
goto :eof

:install_git
if %HAVE_WINGET%==0 (
    echo [ERROR] Git が見つかりません。手動でインストールしてください:
    echo         https://git-scm.com/download/win
    pause & exit /b 1
)
echo [--] Git が見つかりません。winget で Git をインストール中...
winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements
goto :eof
