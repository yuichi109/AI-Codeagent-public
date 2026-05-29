@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

git config core.autocrlf false >nul 2>&1

:: --- Node.js チェック（venv 有無に関わらず毎回）---
call :find_nodejs
if not defined NODE_FOUND (
    call :install_nodejs
    call :find_nodejs
    if not defined NODE_FOUND (
        echo [WARN] Node.js が見つかりません。MCP機能は無効になります。
    )
)

:: --- Playwright Chromium チェック（venv 有無に関わらず毎回）---
if defined NODE_FOUND (
    set "MCP_CHROME_FOUND=0"
    for /d %%D in ("%LOCALAPPDATA%\ms-playwright\chromium-*") do set "MCP_CHROME_FOUND=1"
    if "!MCP_CHROME_FOUND!"=="0" (
        echo [setup] Playwright Chromium をインストール中...
        npx @playwright/mcp@latest install-browser chromium
        if errorlevel 1 (
            echo [WARN] Playwright Chromium のインストールに失敗しました。後で手動実行: npx @playwright/mcp@latest install-browser chromium
        ) else (
            echo [OK] Playwright Chromium 準備完了。
        )
    )
)

:: --- venv が既にあれば即トレイ起動 ---
if exist "venv\Scripts\pythonw.exe" goto launch_tray

:: =============================================================
:: 初回セットアップ（venv がない場合のみ）
:: =============================================================
echo ============================================================
echo  AI Code Agent - 初回セットアップ
echo  （次回からはこのウィンドウは出ません）
echo ============================================================
echo.

set "PY_EXE="
set "HAVE_WINGET=0"

winget --version >nul 2>&1
if not errorlevel 1 set "HAVE_WINGET=1"

:: --- Python の検索・インストール ---
call :find_python
if not defined PY_EXE (
    call :install_python
    call :find_python
)
if not defined PY_EXE (
    echo [ERROR] Python が見つかりません。手動でインストールしてください:
    echo         https://www.python.org/downloads/
    echo         インストール時に "Add Python to PATH" にチェックを入れること。
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('"%PY_EXE%" --version 2^>^&1') do echo [OK] %%v

:: --- Git の検索・インストール ---
call :find_git
if not defined GIT_FOUND (
    call :install_git
    call :find_git
    if not defined GIT_FOUND (
        echo [INFO] Git をインストールしました。このウィンドウを閉じて start.bat を再実行してください。
        pause & exit /b 0
    )
)
for /f "tokens=*" %%v in ('git --version 2^>^&1') do echo [OK] %%v

echo.

:: --- venv 作成 ---
echo [1/4] 仮想環境を作成中...
"%PY_EXE%" -m venv venv
if errorlevel 1 ( echo [ERROR] venv の作成に失敗しました。 & pause & exit /b 1 )

:: --- パッケージインストール ---
echo [2/4] パッケージをインストール中（しばらくお待ちください）...
call venv\Scripts\activate.bat
set PIP_DISABLE_PIP_VERSION_CHECK=1
python.exe -m pip install --upgrade pip --quiet
python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [ERROR] パッケージのインストールに失敗しました。 & pause & exit /b 1 )



:: --- .env 作成 ---
if not exist ".env" (
    echo [3/4] 設定ファイルを作成中...
    copy ".env.example" ".env" >nul
    echo       .env を作成しました。ブラウザの設定画面で API キーを入力してください。
) else (
    echo [3/4] 設定ファイルは既に存在します。
)

echo.
echo [4/4] セットアップ完了。タスクトレイにアイコンが表示されます。
echo ============================================================
timeout /t 2 /nobreak >nul

:: =============================================================
:: トレイ起動
:: =============================================================
:launch_tray
start "" "venv\Scripts\pythonw.exe" "%~dp0tray.py"
exit /b 0

:: =============================================================
:: サブルーチン
:: =============================================================

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

:install_python
if %HAVE_WINGET%==0 goto :eof
echo [--] Python が見つかりません。Python 3.12 をインストール中...
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
    echo [ERROR] Git が見つかりません。手動でインストールしてください:
    echo         https://git-scm.com/download/win
    pause & exit /b 1
)
echo [--] Git が見つかりません。Git をインストール中...
winget install -e --id Git.Git --source winget --silent --accept-package-agreements --accept-source-agreements
goto :eof

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

:install_nodejs
if %HAVE_WINGET%==0 (
    echo [WARN] winget が見つかりません。Node.js を手動でインストールしてください: https://nodejs.org/
    goto :eof
)
echo [--] Node.js が見つかりません。Node.js LTS をインストール中...
winget install -e --id OpenJS.NodeJS.LTS --source winget --silent --accept-package-agreements --accept-source-agreements
goto :eof
