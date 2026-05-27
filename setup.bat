@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: Fix git CRLF conflict (core.autocrlf=true causes setup.bat to always appear modified)
git config core.autocrlf false >nul 2>&1

echo ============================================================
echo  AI Code Agent - デバッグ起動（ログ確認用）
echo  通常起動は start.bat をダブルクリックしてください
echo ============================================================
echo.

set "PY_EXE="
set "HAVE_WINGET=0"

winget --version >nul 2>&1
if not errorlevel 1 set "HAVE_WINGET=1"

:: --- Find / Install Python ---
call :find_python
if not defined PY_EXE (
    call :install_python
    call :find_python
)
if not defined PY_EXE (
    echo [ERROR] Python not found. Please install manually:
    echo         https://www.python.org/downloads/
    echo         Check "Add Python to PATH" during installation.
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('"%PY_EXE%" --version 2^>^&1') do echo [OK] %%v

:: --- Find / Install Git ---
call :find_git
if not defined GIT_FOUND (
    call :install_git
    call :find_git
    if not defined GIT_FOUND (
        echo [INFO] Git installed. Please close this window and run setup.bat again.
        pause & exit /b 0
    )
)
for /f "tokens=*" %%v in ('git --version 2^>^&1') do echo [OK] %%v

echo.

:: --- Create venv ---
if not exist "venv\Scripts\activate.bat" (
    echo [1/4] Creating virtual environment...
    "%PY_EXE%" -m venv venv
    if errorlevel 1 ( echo [ERROR] Failed to create venv. & pause & exit /b 1 )
) else (
    echo [1/4] Virtual environment already exists.
)

:: --- Find / Install Node.js ---
call :find_nodejs
if not defined NODE_FOUND (
    call :install_nodejs
    call :find_nodejs
    if not defined NODE_FOUND (
        echo [WARN] Node.js not found. MCP features (Playwright) will be disabled.
        echo        Install manually: https://nodejs.org/
    )
)
if defined NODE_FOUND (
    for /f "tokens=*" %%v in ('node --version 2^>^&1') do echo [OK] Node.js %%v
)

:: --- Install packages ---
echo [2/4] Installing packages...
call venv\Scripts\activate.bat
set PIP_DISABLE_PIP_VERSION_CHECK=1
python.exe -m pip install --upgrade pip --quiet
python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [ERROR] pip install failed. & pause & exit /b 1 )

:: --- Install Playwright Chromium for MCP ---
if defined NODE_FOUND (
    echo [2b] Installing Playwright Chromium for MCP...
    npx --yes @playwright/mcp install-browser chromium >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Playwright Chromium install failed. Run manually: npx @playwright/mcp install-browser chromium
    ) else (
        echo [OK] Playwright Chromium ready.
    )
)

:: --- Create .env ---
if not exist ".env" (
    echo [3/4] Creating .env...
    copy ".env.example" ".env" >nul
    echo       .env created. Please configure at http://localhost:8001/setup
) else (
    venv\Scripts\python.exe -c "open('.env', encoding='utf-8').read()" >nul 2>&1
    if errorlevel 1 (
        echo [3/4] .env encoding error detected - recreating from .env.example...
        copy ".env.example" ".env" >nul
        echo       .env recreated. Please reconfigure at http://localhost:8001/setup
    ) else (
        echo [3/4] .env already exists.
    )
)

:: --- Start server (auto-restart after config save) ---
echo [4/4] Starting server on port 8001...
echo.
echo   Chat UI : http://localhost:8001
echo   Setup   : http://localhost:8001/setup
echo.
echo   ※ 通常起動は start.bat をダブルクリック（黒窓なし・タスクトレイ常駐）
echo   ※ このウィンドウはデバッグ用（ログをここに表示）
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
:: Subroutines
:: =============================================================

:find_python
:: 1. python / py in PATH
python --version >nul 2>&1
if not errorlevel 1 ( set "PY_EXE=python" & goto :eof )
py --version >nul 2>&1
if not errorlevel 1 ( set "PY_EXE=py" & goto :eof )
:: 2. Check known install paths (versions 313~310)
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
echo [--] Python not found. Installing Python 3.12 via winget...
winget install -e --id Python.Python.3.12 --source winget --silent --accept-package-agreements --accept-source-agreements
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
    echo [ERROR] Git not found. Please install manually:
    echo         https://git-scm.com/download/win
    pause & exit /b 1
)
echo [--] Git not found. Installing Git via winget...
winget install -e --id Git.Git --source winget --silent --accept-package-agreements --accept-source-agreements
goto :eof

:find_nodejs
set "NODE_FOUND="
node --version >nul 2>&1
if not errorlevel 1 ( set "NODE_FOUND=1" & goto :eof )
:: Check common install paths
if exist "%ProgramFiles%\nodejs\node.exe" (
    set "PATH=%PATH%;%ProgramFiles%\nodejs"
    set "NODE_FOUND=1"
)
if exist "%LOCALAPPDATA%\Programs\nodejs\node.exe" (
    set "PATH=%PATH%;%LOCALAPPDATA%\Programs\nodejs"
    set "NODE_FOUND=1"
)
goto :eof

:install_nodejs
if %HAVE_WINGET%==0 (
    echo [WARN] winget not found. Install Node.js manually from https://nodejs.org/
    goto :eof
)
echo [--] Node.js not found. Installing Node.js LTS via winget...
winget install -e --id OpenJS.NodeJS.LTS --source winget --silent --accept-package-agreements --accept-source-agreements
goto :eof
