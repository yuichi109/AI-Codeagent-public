import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from config import ALLOWED_WORK_DIR
from tools.command_tools import BLOCKED_COMMANDS, _truncate_output

# job_id -> {process, stdout_path, stderr_path, command, description, start_time, stdout_f, stderr_f}
_JOBS: dict = {}


def _resolve_work_dir(work_dir: str | None) -> Path | None:
    if work_dir is None:
        return ALLOWED_WORK_DIR
    p = Path(work_dir)
    if not p.is_absolute():
        parts = p.parts
        if parts and parts[0] == ALLOWED_WORK_DIR.name:
            p = Path(*parts[1:]) if len(parts) > 1 else Path(".")
    resolved = (p if p.is_absolute() else ALLOWED_WORK_DIR / p).resolve()
    if not str(resolved).startswith(str(ALLOWED_WORK_DIR)):
        return None
    return resolved


def run_background(command: str, work_dir: str = None, description: str = "") -> dict:
    """
    コマンドをバックグラウンドで起動し、ジョブIDを返す。
    長時間かかる処理（ビルド・サーバー起動・ダウンロード等）に使う。
    結果は check_background(job_id) で確認できる。
    """
    try:
        args = shlex.split(command)
    except ValueError as e:
        return {"error": f"コマンドの解析に失敗: {e}"}

    if not args:
        return {"error": "コマンドが空です"}

    base_cmd = os.path.basename(args[0])
    if base_cmd in BLOCKED_COMMANDS:
        return {"error": f"'{base_cmd}' は危険なため実行できません"}

    args = [os.path.expanduser(a) for a in args]

    resolved_work_dir = _resolve_work_dir(work_dir)
    if resolved_work_dir is None:
        return {"error": "許可された作業ディレクトリ外へのアクセスは禁止されています"}

    job_id = uuid.uuid4().hex[:8]
    stdout_path = Path("/tmp") / f"bg_{job_id}.out"
    stderr_path = Path("/tmp") / f"bg_{job_id}.err"

    try:
        stdout_f = open(stdout_path, "wb")
        stderr_f = open(stderr_path, "wb")
        proc = subprocess.Popen(
            args,
            stdout=stdout_f,
            stderr=stderr_f,
            cwd=str(resolved_work_dir),
            env=os.environ.copy(),
        )
        _JOBS[job_id] = {
            "process": proc,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "stdout_f": stdout_f,
            "stderr_f": stderr_f,
            "command": command,
            "description": description,
            "start_time": time.time(),
        }
        return {
            "job_id": job_id,
            "pid": proc.pid,
            "command": command,
            "description": description or command,
            "status": "started",
            "message": f"バックグラウンドジョブ {job_id} を開始しました（PID: {proc.pid}）。check_background('{job_id}') で進捗を確認できます。",
        }
    except FileNotFoundError:
        return {"error": f"コマンド '{args[0]}' が見つかりません"}
    except Exception as e:
        return {"error": f"実行エラー: {e}"}


def check_background(job_id: str = None) -> dict:
    """
    バックグラウンドジョブの状態と出力を確認する。
    job_id を省略すると全ジョブの一覧を返す。
    完了・失敗したジョブは確認後に自動削除される。
    """
    if not _JOBS:
        return {"jobs": [], "total": 0, "message": "実行中のバックグラウンドジョブはありません"}

    if job_id is None:
        summary = []
        for jid, info in _JOBS.items():
            rc = info["process"].poll()
            elapsed = time.time() - info["start_time"]
            summary.append({
                "job_id": jid,
                "status": "running" if rc is None else ("done" if rc == 0 else "failed"),
                "returncode": rc,
                "command": info["command"],
                "description": info["description"],
                "elapsed_sec": round(elapsed, 1),
            })
        return {"jobs": summary, "total": len(summary)}

    if job_id not in _JOBS:
        return {"error": f"ジョブ '{job_id}' が見つかりません。check_background() で一覧を確認してください。"}

    info = _JOBS[job_id]
    proc = info["process"]
    rc = proc.poll()
    elapsed = time.time() - info["start_time"]

    try:
        stdout_text = info["stdout_path"].read_bytes().decode("utf-8", errors="replace")
    except Exception:
        stdout_text = ""
    try:
        stderr_text = info["stderr_path"].read_bytes().decode("utf-8", errors="replace")
    except Exception:
        stderr_text = ""

    status = "running" if rc is None else ("done" if rc == 0 else "failed")

    if rc is not None:
        # 完了済み → ファイルとジョブ記録を削除
        try:
            info["stdout_f"].close()
            info["stderr_f"].close()
            info["stdout_path"].unlink(missing_ok=True)
            info["stderr_path"].unlink(missing_ok=True)
        except Exception:
            pass
        del _JOBS[job_id]

    return {
        "job_id": job_id,
        "status": status,
        "returncode": rc,
        "elapsed_sec": round(elapsed, 1),
        "command": info["command"],
        "description": info["description"],
        "stdout": _truncate_output(stdout_text),
        "stderr": _truncate_output(stderr_text, 2000),
    }


def kill_background(job_id: str) -> dict:
    """バックグラウンドジョブを強制停止する"""
    if job_id not in _JOBS:
        return {"error": f"ジョブ '{job_id}' が見つかりません"}

    info = _JOBS[job_id]
    proc = info["process"]
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            info["stdout_f"].close()
            info["stderr_f"].close()
            info["stdout_path"].unlink(missing_ok=True)
            info["stderr_path"].unlink(missing_ok=True)
        except Exception:
            pass
        del _JOBS[job_id]
        return {"job_id": job_id, "status": "killed", "message": f"ジョブ {job_id} を停止しました"}
    except Exception as e:
        return {"error": f"停止エラー: {e}"}
