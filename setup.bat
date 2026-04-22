@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  AI Code Agent - Windows Setup
echo ============================================================
echo.

:: Python check
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from https://www.python.org/
    pause
    exit /b 1
)

:: Create venv
if not exist "venv\Scripts\activate.bat" (
    echo [1/4] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 ( echo [ERROR] Failed to create venv. & pause & exit /b 1 )
) else (
    echo [1/4] Virtual environment already exists.
)

:: Install packages
echo [2/4] Installing packages...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [ERROR] pip install failed. & pause & exit /b 1 )

:: Create .env
if not exist ".env" (
    echo [3/4] Creating .env...
    copy ".env.example" ".env" >nul
    echo       .env created. Configure at http://localhost:8001/setup
) else (
    echo [3/4] .env already exists.
)

:: Start server
echo [4/4] Starting server on port 8001...
echo.
echo   Chat UI : http://localhost:8001
echo   Setup   : http://localhost:8001/setup
echo.
echo   Press Ctrl+C to stop.
echo ============================================================
echo.
venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8001

pause
