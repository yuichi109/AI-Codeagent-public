import os
import subprocess
import shutil
from pathlib import Path
from config import COMMAND_TIMEOUT_SECONDS
from tools.command_tools import _truncate_output

# PowerShell で危険な操作をブロックするキーワードリスト
BLOCKED_PS_KEYWORDS = [
    "Format-Volume",
    "Clear-Disk",
    "Initialize-Disk",
    "Remove-Partition",
    "Set-Disk",
    "Stop-Computer",
    "Restart-Computer",
    "Reset-ComputerMachinePassword",
    "Disable-WindowsOptionalFeature",
    "Uninstall-WindowsFeature",
]

# WSL2 上で powershell.exe が存在しうるフルパス候補
# shutil.which はサーバープロセスの PATH しか見ないため、フルパスで直接探す
_PS_CANDIDATES = [
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe",
    "/mnt/c/Windows/SysNative/WindowsPowerShell/v1.0/powershell.exe",
]


def _find_powershell() -> str | None:
    """使用可能な powershell.exe のパスを返す。見つからなければ None"""
    # PATH にある場合（インタラクティブシェルでは通る）
    found = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if found:
        return found
    # PATH にない場合はフルパスで探す
    for p in _PS_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def run_powershell(command: str, timeout_seconds: int = None) -> dict:
    """
    WSL2 から Windows の PowerShell コマンドを実行します。

    できること:
    - Windows ファイル操作（C:\\ ドライブ等）
    - Windows アプリの起動（explorer.exe, notepad.exe 等）
    - クリップボード操作（Get-Clipboard / Set-Clipboard）
    - Windows 通知（New-BurntToastNotification 等）
    - WinGet によるアプリインストール
    - レジストリ読み書き（Get-ItemProperty / Set-ItemProperty）
    - Windows サービス管理（Get-Service / Start-Service / Stop-Service）
    - システム情報取得（Get-ComputerInfo 等）

    Args:
        command: 実行する PowerShell コマンド（複数行 OK）
        timeout_seconds: タイムアウト秒数（デフォルト: COMMAND_TIMEOUT_SECONDS）

    Returns:
        dict: stdout, stderr, returncode, error
    """
    ps_exe = _find_powershell()
    if not ps_exe:
        return {
            "error": "powershell.exe が見つかりません。WSL2 環境で実行してください。",
            "stdout": "", "stderr": "", "returncode": -1,
        }

    # 危険コマンドのブロック
    command_lower = command.lower()
    for kw in BLOCKED_PS_KEYWORDS:
        if kw.lower() in command_lower:
            return {
                "error": f"'{kw}' は危険なため実行できません。",
                "stdout": "", "stderr": "", "returncode": -1,
            }

    effective_timeout = timeout_seconds if timeout_seconds is not None else COMMAND_TIMEOUT_SECONDS

    # UTF-8 出力を強制してから実行（cp932 UnicodeDecodeError 対策）
    utf8_prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8; "
    args = [
        ps_exe,
        "-NonInteractive",
        "-NoProfile",
        "-Command",
        utf8_prefix + command,
    ]

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=False,  # バイト列で受け取り Python 側で UTF-8 デコード
            timeout=effective_timeout,
            shell=False,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        return {
            "stdout": _truncate_output(stdout),
            "stderr": _truncate_output(stderr, 4000),
            "returncode": result.returncode,
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {
            "error": f"{effective_timeout}秒のタイムアウトを超えました",
            "stdout": "", "stderr": "", "returncode": -1,
        }
    except FileNotFoundError:
        return {
            "error": "powershell.exe が見つかりません。WSL2 環境で実行してください。",
            "stdout": "", "stderr": "", "returncode": -1,
        }
    except Exception as e:
        return {"error": f"実行エラー: {e}", "stdout": "", "stderr": "", "returncode": -1}
