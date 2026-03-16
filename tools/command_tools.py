import os
import shlex
import shutil
import subprocess
from pathlib import Path
from config import ALLOWED_WORK_DIR, COMMAND_TIMEOUT_SECONDS

ALLOWED_COMMANDS = {
    "python", "python3", "pip", "pip3",
    "node", "npm", "npx",
    "ls", "cat", "head", "tail", "grep", "find", "echo", "pwd",
    "mkdir", "touch", "cp", "mv",
    "git",
    "docker",
    "curl", "wget",
    "ruff", "black", "flake8", "mypy",
    "go", "cargo", "rustc",
}


def _run_bash_sandboxed(args: list) -> dict:
    """
    bubblewrap (bwrap) を使ってシェルスクリプトをサンドボックス内で実行する。
    Claude Code と同じ方式 (Linux/WSL2)。

    セキュリティ境界:
      - ファイルシステム全体を読み取り専用にマウント
      - ALLOWED_WORK_DIR のみ書き込み可
      - ネットワーク遮断 (--unshare-net)
      - 新しいセッション・PID namespace で隔離
    """
    if not shutil.which("bwrap"):
        return {
            "error": "bubblewrap がインストールされていません。'sudo apt install bubblewrap' を実行してください。",
            "stdout": "", "stderr": "", "returncode": -1,
        }

    # bash script.sh の形式のみ許可（-c や複数引数は禁止）
    if len(args) != 2 or not args[1].endswith(".sh"):
        return {
            "error": "bash はスクリプトファイルのみ許可されています (例: bash script.sh)",
            "stdout": "", "stderr": "", "returncode": -1,
        }

    # スクリプトパスを ALLOWED_WORK_DIR 内に限定
    script_path = (ALLOWED_WORK_DIR / args[1]).resolve()
    if not str(script_path).startswith(str(ALLOWED_WORK_DIR)):
        return {
            "error": "スクリプトは作業ディレクトリ内のみ実行可能です",
            "stdout": "", "stderr": "", "returncode": -1,
        }

    if not script_path.exists():
        return {
            "error": f"スクリプトが見つかりません: {args[1]}",
            "stdout": "", "stderr": "", "returncode": -1,
        }

    bwrap_cmd = [
        "bwrap",
        "--ro-bind", "/", "/",                               # FS 全体を読み取り専用
        "--dev", "/dev",                                     # デバイスファイル
        "--proc", "/proc",                                   # プロセス情報
        "--tmpfs", "/tmp",                                   # 一時領域（書き込み可）
        "--bind", str(ALLOWED_WORK_DIR), str(ALLOWED_WORK_DIR),  # workspace のみ書き込み可
        "--chdir", str(ALLOWED_WORK_DIR),                   # 作業ディレクトリを workspace に
        "--unshare-net",                                     # ネットワーク遮断
        "--new-session",                                     # 新しいセッション
        "--die-with-parent",                                 # 親プロセス終了時に子も終了
        "bash", str(script_path),
    ]

    try:
        result = subprocess.run(
            bwrap_cmd,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        return {
            "stdout": result.stdout[:4096],
            "stderr": result.stderr[:2048],
            "returncode": result.returncode,
            "error": None,
            "sandbox": "bubblewrap",
        }
    except subprocess.TimeoutExpired:
        return {"error": f"{COMMAND_TIMEOUT_SECONDS}秒のタイムアウトを超えました", "stdout": "", "stderr": "", "returncode": -1}
    except Exception as e:
        return {"error": f"サンドボックス実行エラー: {e}", "stdout": "", "stderr": "", "returncode": -1}


def run_command(command: str, work_dir: str = None, description: str = "") -> dict:
    try:
        args = shlex.split(command)
    except ValueError as e:
        return {"error": f"コマンドの解析に失敗: {e}", "stdout": "", "stderr": "", "returncode": -1}

    if not args:
        return {"error": "コマンドが空です", "stdout": "", "stderr": "", "returncode": -1}

    base_cmd = os.path.basename(args[0])

    # bash は bubblewrap サンドボックスで特別処理（ホワイトリストとは独立）
    if base_cmd == "bash":
        return _run_bash_sandboxed(args)

    if base_cmd not in ALLOWED_COMMANDS:
        return {
            "error": f"'{base_cmd}' は許可されていません。許可コマンド: {sorted(ALLOWED_COMMANDS)}",
            "stdout": "", "stderr": "", "returncode": -1,
        }

    # 作業ディレクトリの検証
    # 相対パスは ALLOWED_WORK_DIR 基準で解決（Python プロセスの CWD ではない）
    if work_dir:
        p = Path(work_dir)
        resolved_work_dir = (p if p.is_absolute() else ALLOWED_WORK_DIR / p).resolve()
    else:
        resolved_work_dir = ALLOWED_WORK_DIR
    if not str(resolved_work_dir).startswith(str(ALLOWED_WORK_DIR)):
        return {"error": "許可された作業ディレクトリ外へのアクセスは禁止されています", "stdout": "", "stderr": "", "returncode": -1}

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            cwd=str(resolved_work_dir),
            shell=False,
        )
        return {
            "stdout": result.stdout[:4096],
            "stderr": result.stderr[:2048],
            "returncode": result.returncode,
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"{COMMAND_TIMEOUT_SECONDS}秒のタイムアウトを超えました", "stdout": "", "stderr": "", "returncode": -1}
    except FileNotFoundError:
        return {"error": f"コマンド '{args[0]}' が見つかりません", "stdout": "", "stderr": "", "returncode": -1}
    except Exception as e:
        return {"error": f"実行エラー: {e}", "stdout": "", "stderr": "", "returncode": -1}
