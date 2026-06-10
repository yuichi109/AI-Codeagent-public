@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

git config core.autocrlf false >nul 2>&1

:: winget availability
set "HAVE_WINGET=0"
winget --version >nul 2>&1
if not errorlevel 1 set "HAVE_WINGET=1"

:: running elevated (administrator) ?
net session >nul 2>&1
if not errorlevel 1 ( set "IS_ADMIN=1" ) else ( set "IS_ADMIN=0" )

:: ============================================================
::  PHASE 1: prerequisite tools gate
::  Python / Git = required, Node.js = optional (MCP / Playwright)
::  PHASE 2 (the app) does not start until this passes
:: ============================================================
echo ============================================================
echo  AI Code Agent - checking required tools
echo ============================================================

call :ensure_prereqs
if errorlevel 1 (
    echo.
    echo [STOP] Required tools ^(Python / Git^) are missing. Aborted.
    echo        Install them as shown above, then run start.bat again.
    pause
    exit /b 1
)

:: ============================================================
::  PHASE 2: app setup and launch
:: ============================================================
if exist "venv\Scripts\python.exe" goto app_runtime

echo.
echo ============================================================
echo  AI Code Agent - first-time setup
echo  ^(this step is skipped from next time^)
echo ============================================================

:: --- create venv ---
echo [1/3] Creating virtual environment...
"%PY_EXE%" -m venv venv
if errorlevel 1 ( echo [ERROR] Failed to create venv. & pause & exit /b 1 )

:: --- install packages ---
echo [2/3] Installing packages ^(this may take a while^)...
call venv\Scripts\activate.bat
set PIP_DISABLE_PIP_VERSION_CHECK=1
python.exe -m pip install --upgrade pip --quiet
python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [ERROR] Failed to install packages. & pause & exit /b 1 )

:: --- create .env (interactive port) ---
if not exist ".env" (
    echo [3/3] Creating config file...
    copy ".env.example" ".env" >nul
    echo.
    echo       Set the server port number.
    echo       If you run the WSL edition on the same PC, keep the default 8001.
    set "APP_PORT_INPUT="
    set /p "APP_PORT_INPUT=      Port number [default 8001] : "
    if not defined APP_PORT_INPUT set "APP_PORT_INPUT=8001"
    echo !APP_PORT_INPUT!| findstr /r "^[0-9][0-9]*$" >nul || set "APP_PORT_INPUT=8001"
    powershell -NoProfile -Command "$c=Get-Content '.env' -Encoding UTF8; $c=$c -replace '^#?\s*APP_PORT=.*', 'APP_PORT=!APP_PORT_INPUT!'; [System.IO.File]::WriteAllLines((Resolve-Path '.env').Path, $c, (New-Object System.Text.UTF8Encoding($false)))"
    echo       .env created ^(port: !APP_PORT_INPUT!^). Enter API keys on the browser setup page.
) else (
    echo [3/3] Config file already exists.
)

echo.
echo Setup complete. A tray icon will appear.
echo ============================================================

:app_runtime
:: --- Playwright chromium (use venv python; does not freeze) ---
set "CHROMIUM_FOUND=0"
for /d %%D in ("%LOCALAPPDATA%\ms-playwright\chromium-*") do set "CHROMIUM_FOUND=1"
if "!CHROMIUM_FOUND!"=="1" goto playwright_skip
echo [setup] Installing Playwright chromium...
venv\Scripts\python.exe -m pip install playwright==1.60.0 --quiet
venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 (
    echo [WARN] Failed to install Playwright chromium.
) else (
    echo [OK] Playwright chromium ready.
)
:playwright_skip

:: --- launch tray ---
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" "%~dp0tray.py"
) else (
    start "" "venv\Scripts\python.exe" "%~dp0tray.py"
)
exit /b 0

:: ============================================================
::  Subroutines
:: ============================================================

:: --- ensure required tools (success=errorlevel 0 / fail=1) ---
:ensure_prereqs
call :detect_tools
if not defined MISSING goto prereq_ok

:: maybe installed in another session just now -> refresh PATH and re-check
call :refresh_path
call :detect_tools
if not defined MISSING goto prereq_ok

echo [--] Missing:!MISSING!

:: no winget -> manual guidance and fail
if "%HAVE_WINGET%"=="0" (
    call :prereq_manual_guide
    exit /b 1
)

:: build the missing-tools list (logical names) for the installer script
set "MISS_IDS="
if not defined PY_EXE    set "MISS_IDS=!MISS_IDS!,python"
if not defined GIT_FOUND set "MISS_IDS=!MISS_IDS!,git"
if not defined NODE_FOUND set "MISS_IDS=!MISS_IDS!,node"
set "MISS_IDS=!MISS_IDS:~1!"

echo.
echo     Installing the missing tools ^(winget, with direct-download fallback^).
if "%IS_ADMIN%"=="1" (
    echo     ^(running as administrator^)
    call :install_inline
) else (
    echo     Administrator approval is required. Click "Yes" on the UAC prompt.
    call :install_elevated
)

:: refresh PATH and re-check after install
call :refresh_path
call :detect_tools

:: required (Python / Git) still missing -> abort
if not defined PY_EXE (
    echo.
    echo [ERROR] Could not install Python.
    call :prereq_manual_guide
    exit /b 1
)
if not defined GIT_FOUND (
    echo.
    echo [ERROR] Could not install Git.
    call :prereq_manual_guide
    exit /b 1
)
:: Node.js is optional
if not defined NODE_FOUND echo [WARN] Node.js is still missing ^(MCP / Playwright disabled; the app still runs^).

:prereq_ok
for /f "tokens=*" %%v in ('"!PY_EXE!" --version 2^>^&1') do echo [OK] %%v
for /f "tokens=*" %%v in ('git --version 2^>^&1') do echo [OK] %%v
if defined NODE_FOUND for /f "tokens=*" %%v in ('node --version 2^>^&1') do echo [OK] Node.js %%v
exit /b 0

:: --- detect tools (sets PY_EXE / GIT_FOUND / NODE_FOUND / MISSING) ---
:detect_tools
set "PY_EXE="
set "GIT_FOUND="
set "NODE_FOUND="
set "MISSING="
call :find_python
call :find_git
call :find_nodejs
if not defined PY_EXE    set "MISSING=!MISSING! Python"
if not defined GIT_FOUND set "MISSING=!MISSING! Git"
if not defined NODE_FOUND set "MISSING=!MISSING! Node.js"
goto :eof

:: --- reflect machine PATH from registry into current session ---
:refresh_path
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine')"`) do set "SYS_PATH=%%p"
if defined SYS_PATH set "PATH=%PATH%;%SYS_PATH%"
goto :eof

:: --- elevated already: run the installer script in this session ---
:install_inline
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_prereqs.ps1" -Tools "!MISS_IDS!"
goto :eof

:: --- standard user: run the installer script elevated (one UAC prompt) ---
:install_elevated
powershell -NoProfile -Command "try { Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%~dp0scripts\install_prereqs.ps1','-Tools','!MISS_IDS!' } catch { Write-Host '[WARN] UAC was cancelled.' }"
goto :eof

:: --- manual install guidance ---
:prereq_manual_guide
echo.
echo   ----------------------------------------------------------
echo   Please install the required tools manually:
echo     Python 3.12 : https://www.python.org/downloads/  ^(enable "Add Python to PATH"^)
echo     Git         : https://git-scm.com/download/win
echo     Node.js LTS : https://nodejs.org/  ^(optional - for MCP / Playwright^)
echo.
echo   * If winget shows "1625 / blocked by policy", a standard user cannot
echo     install machine-wide packages. Install the tools with an administrator
echo     account first, then run this start.bat as a normal user.
echo   ----------------------------------------------------------
goto :eof

:: --- find Python ---
:find_python
python --version >nul 2>&1
if not errorlevel 1 ( set "PY_EXE=python" & goto :eof )
py --version >nul 2>&1
if not errorlevel 1 ( set "PY_EXE=py" & goto :eof )
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

:: --- find Git ---
:find_git
set "GIT_FOUND="
git --version >nul 2>&1
if not errorlevel 1 ( set "GIT_FOUND=1" & goto :eof )
if exist "C:\Program Files\Git\cmd\git.exe" (
    set "PATH=%PATH%;C:\Program Files\Git\cmd;C:\Program Files\Git\bin"
    set "GIT_FOUND=1"
)
goto :eof

:: --- find Node.js ---
:find_nodejs
set "NODE_FOUND="
node --version >nul 2>&1
if not errorlevel 1 ( set "NODE_FOUND=1" & goto :eof )
if exist "%ProgramFiles%\nodejs\node.exe" (
    set "PATH=%PATH%;%ProgramFiles%\nodejs"
    set "NODE_FOUND=1"
)
if exist "%LOCALAPPDATA%\Programs\nodejs\node.exe" (
    set "PATH=%PATH%;%LOCALAPPDATA%\Programs\nodejs"
    set "NODE_FOUND=1"
)
goto :eof
