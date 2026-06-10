@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

git config core.autocrlf false >nul 2>&1

:: winget の有無
set "HAVE_WINGET=0"
winget --version >nul 2>&1
if not errorlevel 1 set "HAVE_WINGET=1"

:: 管理者として実行中かどうか
net session >nul 2>&1
if not errorlevel 1 ( set "IS_ADMIN=1" ) else ( set "IS_ADMIN=0" )

:: ============================================================
::  フェーズ1: 必須ツールの事前チェック＆インストール
::  Python / Git = 必須、Node.js = 任意（MCP / Playwright 用）
::  ここが通るまでフェーズ2（アプリ本体）へは進まない
:: ============================================================
echo ============================================================
echo  AI Code Agent - 必須ツールのチェック
echo ============================================================

call :ensure_prereqs
if errorlevel 1 (
    echo.
    echo [STOP] 必須ツール（Python / Git）が揃わないため中断しました。
    echo        上の案内に従って導入してから、もう一度 start.bat を実行してください。
    pause
    exit /b 1
)

:: ============================================================
::  フェーズ2: アプリのセットアップ＆起動
:: ============================================================
if exist "venv\Scripts\python.exe" goto app_runtime

echo.
echo ============================================================
echo  AI Code Agent - 初回セットアップ
echo  （次回からこの工程は省略されます）
echo ============================================================

:: --- venv 作成 ---
echo [1/3] 仮想環境を作成中...
"%PY_EXE%" -m venv venv
if errorlevel 1 ( echo [ERROR] venv の作成に失敗しました。 & pause & exit /b 1 )

:: --- パッケージインストール ---
echo [2/3] パッケージをインストール中（しばらくお待ちください）...
call venv\Scripts\activate.bat
set PIP_DISABLE_PIP_VERSION_CHECK=1
python.exe -m pip install --upgrade pip --quiet
python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [ERROR] パッケージのインストールに失敗しました。 & pause & exit /b 1 )

:: --- .env 作成（ポート対話入力）---
if not exist ".env" (
    echo [3/3] 設定ファイルを作成中...
    copy ".env.example" ".env" >nul
    echo.
    echo       サーバーのポート番号を設定します。
    echo       WSL版と同じPCで同時に使う場合は既定の 8001 のままにしてください。
    set "APP_PORT_INPUT="
    set /p "APP_PORT_INPUT=      ポート番号 [既定 8001] : "
    if not defined APP_PORT_INPUT set "APP_PORT_INPUT=8001"
    echo !APP_PORT_INPUT!| findstr /r "^[0-9][0-9]*$" >nul || set "APP_PORT_INPUT=8001"
    powershell -NoProfile -Command "$c=Get-Content '.env' -Encoding UTF8; $c=$c -replace '^#?\s*APP_PORT=.*', 'APP_PORT=!APP_PORT_INPUT!'; [System.IO.File]::WriteAllLines((Resolve-Path '.env').Path, $c, (New-Object System.Text.UTF8Encoding($false)))"
    echo       .env を作成しました（ポート: !APP_PORT_INPUT!）。ブラウザの設定画面で API キーを入力してください。
) else (
    echo [3/3] 設定ファイルは既に存在します。
)

echo.
echo セットアップ完了。タスクトレイにアイコンが表示されます。
echo ============================================================

:app_runtime
:: --- Playwright chromium インストール（venv の python を使う・固まらない）---
set "CHROMIUM_FOUND=0"
for /d %%D in ("%LOCALAPPDATA%\ms-playwright\chromium-*") do set "CHROMIUM_FOUND=1"
if "!CHROMIUM_FOUND!"=="1" goto playwright_skip
echo [setup] Playwright chromium をインストール中...
venv\Scripts\python.exe -m pip install playwright==1.60.0 --quiet
venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 (
    echo [WARN] Playwright chromium のインストールに失敗しました。
) else (
    echo [OK] Playwright chromium 準備完了。
)
:playwright_skip

:: --- トレイ起動 ---
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" "%~dp0tray.py"
) else (
    start "" "venv\Scripts\python.exe" "%~dp0tray.py"
)
exit /b 0

:: ============================================================
::  サブルーチン
:: ============================================================

:: --- 必須ツールを揃える（成功=errorlevel 0 / 失敗=1）---
:ensure_prereqs
call :detect_tools
if not defined MISSING goto prereq_ok

:: 別セッションで導入直後の可能性 → PATH をレジストリから再取得して再判定
call :refresh_path
call :detect_tools
if not defined MISSING goto prereq_ok

echo [--] 未導入:!MISSING!

:: winget が無ければ手動案内して失敗
if "%HAVE_WINGET%"=="0" (
    call :prereq_manual_guide
    exit /b 1
)

:: 不足分のみ winget ID を組み立て
set "PS_IDS="
if not defined PY_EXE    set "PS_IDS=!PS_IDS!,'Python.Python.3.12'"
if not defined GIT_FOUND set "PS_IDS=!PS_IDS!,'Git.Git'"
if not defined NODE_FOUND set "PS_IDS=!PS_IDS!,'OpenJS.NodeJS.LTS'"
set "PS_IDS=!PS_IDS:~1!"

echo.
echo     不足しているツールを winget でインストールします。
if "%IS_ADMIN%"=="1" (
    echo     （管理者として実行中）
    call :install_inline
) else (
    echo     インストールには管理者の許可が必要です。UAC が表示されたら「はい」を押してください。
    call :install_elevated
)

:: 導入後 PATH を更新して再判定
call :refresh_path
call :detect_tools

:: 必須（Python / Git）が依然欠ける場合は中断
if not defined PY_EXE (
    echo.
    echo [ERROR] Python を導入できませんでした。
    call :prereq_manual_guide
    exit /b 1
)
if not defined GIT_FOUND (
    echo.
    echo [ERROR] Git を導入できませんでした。
    call :prereq_manual_guide
    exit /b 1
)
:: Node.js は任意
if not defined NODE_FOUND echo [WARN] Node.js は未導入です（MCP / Playwright 機能は無効。本体は動作します）。

:prereq_ok
for /f "tokens=*" %%v in ('"!PY_EXE!" --version 2^>^&1') do echo [OK] %%v
for /f "tokens=*" %%v in ('git --version 2^>^&1') do echo [OK] %%v
if defined NODE_FOUND for /f "tokens=*" %%v in ('node --version 2^>^&1') do echo [OK] Node.js %%v
exit /b 0

:: --- ツール検出（PY_EXE / GIT_FOUND / NODE_FOUND / MISSING を設定）---
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

:: --- マシン PATH をレジストリから現在のセッションへ反映 ---
:refresh_path
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine')"`) do set "SYS_PATH=%%p"
if defined SYS_PATH set "PATH=%PATH%;%SYS_PATH%"
goto :eof

:: --- 管理者で実行中: その場で winget インストール（不足分のみ）---
:install_inline
if not defined PY_EXE    winget install -e --id Python.Python.3.12 --source winget --silent --accept-package-agreements --accept-source-agreements
if not defined GIT_FOUND winget install -e --id Git.Git --source winget --silent --accept-package-agreements --accept-source-agreements
if not defined NODE_FOUND winget install -e --id OpenJS.NodeJS.LTS --source winget --silent --accept-package-agreements --accept-source-agreements
goto :eof

:: --- 標準ユーザー: インストール部分だけ昇格して実行（UAC は1回）---
:install_elevated
set "PS1=%TEMP%\aica_prereq_%RANDOM%.ps1"
> "%PS1%" echo $ErrorActionPreference='Continue'
>>"%PS1%" echo $ids = @(!PS_IDS!)
>>"%PS1%" echo if(-not (Get-Command winget -ErrorAction SilentlyContinue)){ Write-Host '[ERROR] winget not found in elevated session. Install Python/Git/Node.js manually.'; Read-Host 'Press Enter to close'; exit }
>>"%PS1%" echo foreach($id in $ids){
>>"%PS1%" echo   Write-Host ('[install] ' + $id)
>>"%PS1%" echo   winget install -e --id $id --source winget --accept-package-agreements --accept-source-agreements
>>"%PS1%" echo }
>>"%PS1%" echo Read-Host 'Done. Press Enter to close'
powershell -NoProfile -Command "try { Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%PS1%' } catch { Write-Host '[WARN] UAC がキャンセルされました。' }"
del "%PS1%" >nul 2>&1
goto :eof

:: --- 手動インストールの案内 ---
:prereq_manual_guide
echo.
echo   ----------------------------------------------------------
echo   必須ツールを手動でインストールしてください:
echo     Python 3.12 : https://www.python.org/downloads/  （「Add Python to PATH」を有効に）
echo     Git         : https://git-scm.com/download/win
echo     Node.js LTS : https://nodejs.org/  （任意・MCP / Playwright 用）
echo.
echo   ※ winget で「1625 / 組織のポリシー」と出る場合、標準ユーザーのままでは
echo      インストールできません。管理者アカウントで上記を導入してから、
echo      この start.bat を一般ユーザーで実行してください。
echo   ----------------------------------------------------------
goto :eof

:: --- Python 検索 ---
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

:: --- Git 検索 ---
:find_git
set "GIT_FOUND="
git --version >nul 2>&1
if not errorlevel 1 ( set "GIT_FOUND=1" & goto :eof )
if exist "C:\Program Files\Git\cmd\git.exe" (
    set "PATH=%PATH%;C:\Program Files\Git\cmd;C:\Program Files\Git\bin"
    set "GIT_FOUND=1"
)
goto :eof

:: --- Node.js 検索 ---
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
