import os
import shlex
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from config import ALLOWED_WORK_DIR, ALLOWED_WORK_DIRS, COMMAND_TIMEOUT_SECONDS

_POSIX = os.name == "posix"

# 危険コマンドのブラックリスト（ホワイトリスト廃止 → ブラックリスト方式に移行）
# rm -rf / や dd if=/dev/zero 等のシステム破壊コマンドのみ拒否
BLOCKED_COMMANDS = {
    "mkfs", "fdisk", "parted", "dd",
    "shutdown", "reboot", "halt", "poweroff",
    "init",
}

LONG_RUNNING_CMDS = {"docker", "apt", "apt-get", "pip", "pip3", "npm", "yarn", "brew", "sudo", "ansible-galaxy", "ansible-playbook", "ansible"}

# 破壊的なファイル削除コマンド。対象パスが作業ディレクトリ外を指す場合はブロックする。
DESTRUCTIVE_FILE_CMDS = {"rm", "rmdir", "unlink", "shred", "srm"}


def _check_destructive_paths(args: list, resolved_work_dir: Path) -> str | None:
    """rm 等の破壊的コマンドの削除対象が許可ディレクトリ外を指していないか検証する。
    パストラバーサル（../）や作業ディレクトリ外の絶対パスを検出したらエラー文を返す。
    問題なければ None。"""
    base = os.path.basename(args[0])
    if base not in DESTRUCTIVE_FILE_CMDS:
        return None
    for raw in args[1:]:
        if raw.startswith("-"):
            continue  # -rf などのフラグはスキップ
        pp = Path(raw)
        target = pp.resolve() if pp.is_absolute() else (resolved_work_dir / pp).resolve()
        target_str = str(target)
        inside = any(
            target_str == str(d) or target_str.startswith(str(d) + os.sep)
            for d in ALLOWED_WORK_DIRS
        )
        if not inside:
            return (
                f"パストラバーサルを検出しました。'{raw}' は許可された作業ディレクトリ外を指しています。\n"
                f"作業ディレクトリ外のファイル/ディレクトリ削除は禁止されています。"
            )
    return None


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


def _kill_process_group(proc: subprocess.Popen) -> None:
    """プロセスグループ全体を kill する（バックグラウンドに逃げた孫プロセスも含む）。
    start_new_session=True で起動しているため、グループ kill は子の系統だけに当たり、
    本体（AI-Codeagent サーバー）には波及しない。"""
    if _POSIX:
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError):
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    else:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _capture_run(args: list, *, cwd: str = None, env: dict = None, timeout=None) -> tuple:
    """subprocess.run(capture_output=True) の代替。

    capture_output=True は内部で communicate()＝**出力パイプの EOF を待つ**ため、
    スクリプトがバックグラウンドでサーバー（uvicorn 等）を起動すると、その子が出力 fd を
    握り続けてパイプが閉じず、スクリプト本体が終了しても永久に固まる。

    ここでは出力を一時ファイルに流し、**起動したコマンド自身の終了 (proc.wait)** で完了を
    判定する。孫プロセスが fd を握っていても、直接の子が exit すれば即返る（＝固まらない）。
    タイムアウト時はプロセスグループごと kill する（孫まで巻き込む）。

    戻り値: (returncode, stdout, stderr, timed_out)
    """
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=out_f,
            stderr=err_f,
            cwd=cwd,
            env=env,
            shell=False,
            start_new_session=True,  # 子を独立セッション化（グループ kill が本体に波及しない）
        )
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(proc)
        out_f.seek(0)
        err_f.seek(0)
        stdout = out_f.read().decode("utf-8", errors="replace")
        stderr = err_f.read().decode("utf-8", errors="replace")
        return proc.returncode, stdout, stderr, timed_out


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

    # 許可ディレクトリすべてを書き込み可でバインド
    bind_args = []
    for allowed_dir in ALLOWED_WORK_DIRS:
        bind_args += ["--bind", str(allowed_dir), str(allowed_dir)]

    bwrap_cmd = [
        "bwrap",
        "--ro-bind", "/", "/",       # FS 全体を読み取り専用
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        *bind_args,                  # 許可ディレクトリのみ書き込み可
        "--chdir", str(ALLOWED_WORK_DIR),
        "--unshare-net",
        "--new-session",
        "--die-with-parent",
        "bash", str(script_path),
    ]

    try:
        rc, stdout, stderr, timed_out = _capture_run(
            bwrap_cmd,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        if timed_out:
            return {"error": f"{COMMAND_TIMEOUT_SECONDS}秒のタイムアウトを超えました", "stdout": "", "stderr": "", "returncode": -1}
        return {
            "stdout": _truncate_output(stdout),
            "stderr": _truncate_output(stderr, 4000),
            "returncode": rc,
            "error": None,
            "sandbox": "bubblewrap",
        }
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
    if work_dir:
        p = Path(work_dir)
        if p.is_absolute():
            resolved_work_dir = p.resolve()
        else:
            # 相対パス: "workspace" や "workspace/foo" の二重パス防止
            parts = p.parts
            if parts and parts[0] == ALLOWED_WORK_DIR.name:
                p = Path(*parts[1:]) if len(parts) > 1 else Path(".")
            resolved_work_dir = (ALLOWED_WORK_DIR / p).resolve()
    else:
        resolved_work_dir = ALLOWED_WORK_DIR

    resolved_str = str(resolved_work_dir)
    if not any(resolved_str.startswith(str(d)) for d in ALLOWED_WORK_DIRS):
        dirs = ", ".join(str(d) for d in ALLOWED_WORK_DIRS)
        return {"error": f"許可された作業ディレクトリ外へのアクセスは禁止されています\n許可: {dirs}", "stdout": "", "stderr": "", "returncode": -1}

    # rm/rmdir 等で作業ディレクトリ外（../ や絶対パス）を削除しようとする操作をブロック
    destructive_err = _check_destructive_paths(args, resolved_work_dir)
    if destructive_err:
        return {"error": destructive_err, "stdout": "", "stderr": "", "returncode": -1}

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
        rc, stdout, stderr, timed_out = _capture_run(
            args,
            cwd=str(resolved_work_dir),
            env=merged_env,
            timeout=effective_timeout,
        )

        if timed_out:
            timeout_label = f"{effective_timeout // 60}分" if effective_timeout and effective_timeout >= 60 else f"{effective_timeout}秒"
            return {"error": f"{timeout_label}のタイムアウトを超えました（コマンド: {base_cmd}）。処理がまだ進行中の可能性があります。タイムアウトを延長して再実行しますか？", "stdout": "", "stderr": "", "returncode": -1}

        # 権限エラーで失敗した場合、AIにユーザー確認を促すヒントを付与する
        if (rc != 0
                and args[0] != "sudo"
                and _is_permission_error(stderr)):
            return {
                "stdout": _truncate_output(stdout),
                "stderr": _truncate_output(stderr, 4000),
                "returncode": rc,
                "error": None,
                "hint": f"権限エラーが発生しました。`sudo {command}` で再実行することで解決できる可能性があります。ユーザーに確認してから再実行してください。",
            }

        return {
            "stdout": _truncate_output(stdout),
            "stderr": _truncate_output(stderr, 4000),
            "returncode": rc,
            "error": None,
        }
    except FileNotFoundError:
        return {"error": f"コマンド '{args[0]}' が見つかりません", "stdout": "", "stderr": "", "returncode": -1}
    except Exception as e:
        return {"error": f"実行エラー: {e}", "stdout": "", "stderr": "", "returncode": -1}
