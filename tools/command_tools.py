import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from config import ALLOWED_WORK_DIR, COMMAND_TIMEOUT_SECONDS

IS_WINDOWS = sys.platform == "win32"

# 危険コマンドのブラックリスト（ホワイトリスト廃止 → ブラックリスト方式に移行）
# rm -rf / や dd if=/dev/zero 等のシステム破壊コマンドのみ拒否
BLOCKED_COMMANDS = {
    "mkfs", "fdisk", "parted", "dd",
    "shutdown", "reboot", "halt", "poweroff",
    "init",
}

# Windows 固有の危険コマンド
BLOCKED_COMMANDS_WINDOWS = {
    "format", "diskpart",
}

LONG_RUNNING_CMDS = {"docker", "apt", "apt-get", "pip", "pip3", "npm", "yarn", "brew", "sudo", "ansible-galaxy", "ansible-playbook", "ansible"}


def _split_shell_chain(command: str) -> list[str]:
    """&& で連結されたコマンドを分割する（クォート内の && は無視）"""
    parts = []
    current = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
        elif (c == '&' and not in_single and not in_double
              and i + 1 < len(command) and command[i + 1] == '&'):
            parts.append(''.join(current).strip())
            current = []
            i += 2
            continue
        else:
            current.append(c)
        i += 1
    if current:
        parts.append(''.join(current).strip())
    return [p for p in parts if p]


def _is_permission_error(stderr: str) -> bool:
    """標準エラー出力から権限エラーを判定する"""
    keywords = [
        "permission denied",
        "could not open lock file",
        "unable to lock",
        "are you root",
        "operation not permitted",
        "E: Could not",
    ]
    stderr_lower = stderr.lower()
    return any(kw.lower() in stderr_lower for kw in keywords)


def _run_bash_sandboxed(args: list) -> dict:
    """
    bash スクリプトを実行する。
    Linux/WSL2: bubblewrap サンドボックス経由（ネットワーク遮断・FS 読み取り専用）
    Windows: サンドボックスなしで bash.exe を直接実行（Git for Windows / WSL bash）
    """
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

    if IS_WINDOWS:
        # Windows: bash.exe を探して直接実行（サンドボックスなし）
        bash_candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Windows\System32\bash.exe",  # WSL
        ]
        bash_exe = shutil.which("bash")
        if not bash_exe:
            for candidate in bash_candidates:
                if Path(candidate).exists():
                    bash_exe = candidate
                    break
        if not bash_exe:
            return {
                "error": "bash.exe が見つかりません。Git for Windows をインストールしてください。",
                "stdout": "", "stderr": "", "returncode": -1,
            }
        run_cmd = [bash_exe, str(script_path)]
        sandbox_label = "none (Windows)"
    else:
        # Linux/WSL2: bubblewrap サンドボックス
        if not shutil.which("bwrap"):
            return {
                "error": "bubblewrap がインストールされていません。'sudo apt install bubblewrap' を実行してください。",
                "stdout": "", "stderr": "", "returncode": -1,
            }
        run_cmd = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--bind", str(ALLOWED_WORK_DIR), str(ALLOWED_WORK_DIR),
            "--chdir", str(ALLOWED_WORK_DIR),
            "--unshare-net",
            "--new-session",
            "--die-with-parent",
            "bash", str(script_path),
        ]
        sandbox_label = "bubblewrap"

    try:
        result = subprocess.run(
            run_cmd,
            capture_output=True,
            text=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        return {
            "stdout": stdout[:8192],
            "stderr": stderr[:4096],
            "returncode": result.returncode,
            "error": None,
            "sandbox": sandbox_label,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"{COMMAND_TIMEOUT_SECONDS}秒のタイムアウトを超えました", "stdout": "", "stderr": "", "returncode": -1}
    except Exception as e:
        return {"error": f"スクリプト実行エラー: {e}", "stdout": "", "stderr": "", "returncode": -1}


def run_command(command: str, work_dir: str = None, description: str = "", env: dict = None, timeout_minutes: float = None) -> dict:
    # Windows は shell=True のため && はシェルが処理する。Linux のみ手動分割
    if not IS_WINDOWS and '&&' in command:
        sub_commands = _split_shell_chain(command)
        if len(sub_commands) > 1:
            combined_stdout = []
            combined_stderr = []
            for sub_cmd in sub_commands:
                result = run_command(sub_cmd, work_dir=work_dir, description=description, env=env, timeout_minutes=timeout_minutes)
                if result.get('stdout'):
                    combined_stdout.append(result['stdout'])
                if result.get('stderr'):
                    combined_stderr.append(result['stderr'])
                if result.get('error') or result.get('returncode', 0) != 0:
                    return {
                        'stdout': '\n'.join(combined_stdout),
                        'stderr': '\n'.join(combined_stderr),
                        'returncode': result.get('returncode', -1),
                        'error': result.get('error'),
                    }
            return {
                'stdout': '\n'.join(combined_stdout),
                'stderr': '\n'.join(combined_stderr),
                'returncode': 0,
                'error': None,
            }

    try:
        args = shlex.split(command)
    except ValueError as e:
        return {"error": f"コマンドの解析に失敗: {e}", "stdout": "", "stderr": "", "returncode": -1}

    if not args:
        return {"error": "コマンドが空です", "stdout": "", "stderr": "", "returncode": -1}

    # shell=False のためチルダ展開をここで行う
    args = [os.path.expanduser(a) for a in args]

    base_cmd = os.path.basename(args[0])

    # bash は bubblewrap サンドボックスで特別処理（ホワイトリストとは独立）
    if base_cmd == "bash":
        return _run_bash_sandboxed(args)

    all_blocked = BLOCKED_COMMANDS | (BLOCKED_COMMANDS_WINDOWS if IS_WINDOWS else set())
    if base_cmd in all_blocked:
        return {
            "error": f"'{base_cmd}' はシステム破壊の恐れがあるため実行できません。",
            "stdout": "", "stderr": "", "returncode": -1,
        }

    # 作業ディレクトリの検証
    # 相対パスは ALLOWED_WORK_DIR 基準で解決（Python プロセスの CWD ではない）
    # "workspace" や "workspace/foo" が渡された場合の二重パス防止
    if work_dir:
        p = Path(work_dir)
        if not p.is_absolute():
            parts = p.parts
            if parts and parts[0] == ALLOWED_WORK_DIR.name:
                p = Path(*parts[1:]) if len(parts) > 1 else Path(".")
        resolved_work_dir = (p if p.is_absolute() else ALLOWED_WORK_DIR / p).resolve()
    else:
        resolved_work_dir = ALLOWED_WORK_DIR
    if not str(resolved_work_dir).startswith(str(ALLOWED_WORK_DIR)):
        return {"error": "許可された作業ディレクトリ外へのアクセスは禁止されています", "stdout": "", "stderr": "", "returncode": -1}

    # タイムアウト決定: timeout_minutes > LONG_RUNNING_CMDS > デフォルト の優先順
    if timeout_minutes is not None:
        effective_timeout = int(timeout_minutes * 60) if timeout_minutes > 0 else None  # 0 = 無制限
    elif base_cmd in LONG_RUNNING_CMDS:
        effective_timeout = 300
    else:
        effective_timeout = COMMAND_TIMEOUT_SECONDS

    # 環境変数: 現在の環境をベースに env で指定された値をマージ
    merged_env = None
    if env:
        merged_env = {**os.environ, **{str(k): str(v) for k, v in env.items()}}

    try:
        result = subprocess.run(
            ["cmd", "/c", command] if IS_WINDOWS else args,
            capture_output=True,
            text=False,
            timeout=effective_timeout,
            cwd=str(resolved_work_dir),
            shell=False,
            env=merged_env,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")

        # 権限エラーで失敗した場合、AIにユーザー確認を促すヒントを付与する
        if (result.returncode != 0
                and args[0] != "sudo"
                and _is_permission_error(stderr)):
            return {
                "stdout": stdout[:8192],
                "stderr": stderr[:4096],
                "returncode": result.returncode,
                "error": None,
                "hint": f"権限エラーが発生しました。`sudo {command}` で再実行することで解決できる可能性があります。ユーザーに確認してから再実行してください。",
            }

        return {
            "stdout": stdout[:8192],
            "stderr": stderr[:4096],
            "returncode": result.returncode,
            "error": None,
        }
    except subprocess.TimeoutExpired:
        timeout_label = f"{effective_timeout // 60}分" if effective_timeout and effective_timeout >= 60 else f"{effective_timeout}秒"
        return {"error": f"{timeout_label}のタイムアウトを超えました（コマンド: {base_cmd}）。処理がまだ進行中の可能性があります。タイムアウトを延長して再実行しますか？", "stdout": "", "stderr": "", "returncode": -1}
    except FileNotFoundError:
        return {"error": f"コマンド '{args[0]}' が見つかりません", "stdout": "", "stderr": "", "returncode": -1}
    except Exception as e:
        return {"error": f"実行エラー: {e}", "stdout": "", "stderr": "", "returncode": -1}
