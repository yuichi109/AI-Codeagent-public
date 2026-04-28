import os
import shlex
import shutil
import subprocess
from pathlib import Path
from config import ALLOWED_WORK_DIR, COMMAND_TIMEOUT_SECONDS

# 危険コマンドのブラックリスト（ホワイトリスト廃止 → ブラックリスト方式に移行）
# rm -rf / や dd if=/dev/zero 等のシステム破壊コマンドのみ拒否
BLOCKED_COMMANDS = {
    "mkfs", "fdisk", "parted", "dd",
    "shutdown", "reboot", "halt", "poweroff",
    "init",
}

LONG_RUNNING_CMDS = {"docker", "apt", "apt-get", "pip", "pip3", "npm", "yarn", "brew", "sudo", "ansible-galaxy", "ansible-playbook", "ansible"}


def _decode_output(data: bytes) -> str:
    """バイト列を UTF-8 → CP932 → errors=replace の順でデコードする。
    winget 等の Windows コマンドが CP932 を返す環境でも文字化けしない。"""
    if not data:
        return ""
    for enc in ("utf-8", "cp932"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _truncate_output(text: str, limit: int = 8000) -> str:
    """長い出力を先頭+末尾で切り詰める。エラーが末尾に出るコマンドに対応。"""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n...（中略: {len(text) - limit} 文字省略）...\n" + text[-half:]


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
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        return {
            "stdout": _truncate_output(_decode_output(result.stdout)),
            "stderr": _truncate_output(_decode_output(result.stderr), 4000),
            "returncode": result.returncode,
            "error": None,
            "sandbox": "bubblewrap",
        }
    except subprocess.TimeoutExpired:
        return {"error": f"{COMMAND_TIMEOUT_SECONDS}秒のタイムアウトを超えました", "stdout": "", "stderr": "", "returncode": -1}
    except Exception as e:
        return {"error": f"サンドボックス実行エラー: {e}", "stdout": "", "stderr": "", "returncode": -1}


def run_command(command: str, work_dir: str = None, description: str = "", env: dict = None, timeout_minutes: float = None) -> dict:
    # shell=False では && が使えないため、自動分割して順次実行する
    if '&&' in command:
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

    if base_cmd in BLOCKED_COMMANDS:
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
            args,
            capture_output=True,
            timeout=effective_timeout,
            cwd=str(resolved_work_dir),
            shell=False,
            env=merged_env,
        )

        stdout = _decode_output(result.stdout)
        stderr = _decode_output(result.stderr)

        # 権限エラーで失敗した場合、AIにユーザー確認を促すヒントを付与する
        if (result.returncode != 0
                and args[0] != "sudo"
                and _is_permission_error(stderr)):
            return {
                "stdout": _truncate_output(stdout),
                "stderr": _truncate_output(stderr, 4000),
                "returncode": result.returncode,
                "error": None,
                "hint": f"権限エラーが発生しました。`sudo {command}` で再実行することで解決できる可能性があります。ユーザーに確認してから再実行してください。",
            }

        return {
            "stdout": _truncate_output(stdout),
            "stderr": _truncate_output(stderr, 4000),
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
