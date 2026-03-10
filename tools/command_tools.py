import os
import shlex
import subprocess
from pathlib import Path
from config import ALLOWED_WORK_DIR, COMMAND_TIMEOUT_SECONDS

ALLOWED_COMMANDS = {
    "python", "python3", "pip", "pip3",
    "node", "npm", "npx",
    "ls", "cat", "head", "tail", "grep", "find", "echo", "pwd",
    "mkdir", "touch", "cp", "mv",
    "git",
    "curl", "wget",
    "ruff", "black", "flake8", "mypy",
    "go", "cargo", "rustc",
}


def run_command(command: str, work_dir: str = None) -> dict:
    try:
        args = shlex.split(command)
    except ValueError as e:
        return {"error": f"コマンドの解析に失敗: {e}", "stdout": "", "stderr": "", "returncode": -1}

    if not args:
        return {"error": "コマンドが空です", "stdout": "", "stderr": "", "returncode": -1}

    base_cmd = os.path.basename(args[0])
    if base_cmd not in ALLOWED_COMMANDS:
        return {
            "error": f"'{base_cmd}' は許可されていません。許可コマンド: {sorted(ALLOWED_COMMANDS)}",
            "stdout": "", "stderr": "", "returncode": -1,
        }

    # 作業ディレクトリの検証
    resolved_work_dir = Path(work_dir or ALLOWED_WORK_DIR).resolve()
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
