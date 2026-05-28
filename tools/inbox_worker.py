"""
Obsidian inbox 監視ワーカー（機能B）

{vault}/AI-Codeagent/inbox/{hostname}_{suffix}/ を定期ポーリングし、
MD ファイルを検出したら processing/ に移動して AI に処理を委ねる。

フォルダ構成:
  inbox/{hostname}_{suffix}/       処理待ち（ユーザーが置く）
  processing/{hostname}_{suffix}/  作業中
  done/{hostname}_{suffix}/        完了済み（元リクエスト保存）
  results/{hostname}_{suffix}/     成果物
"""

import asyncio
import platform
import shutil
import socket
from datetime import datetime
from pathlib import Path

from config import OBSIDIAN_INBOX_ENABLED, OBSIDIAN_INBOX_POLL_SEC, OBSIDIAN_VAULT_PATH

_worker_task: asyncio.Task | None = None


def _get_host_suffix() -> str:
    hostname = socket.gethostname()
    suffix = "win" if platform.system() == "Windows" else "wsl"
    return f"{hostname}_{suffix}"


def _get_inbox_dirs() -> dict[str, Path] | None:
    if not OBSIDIAN_VAULT_PATH:
        return None
    base = Path(OBSIDIAN_VAULT_PATH) / "AI-Codeagent"
    suffix = _get_host_suffix()
    return {
        "inbox":      base / "inbox"      / suffix,
        "processing": base / "processing" / suffix,
        "done":       base / "done"       / suffix,
        "results":    base / "results"    / suffix,
    }


def ensure_inbox_dirs() -> dict[str, Path] | None:
    dirs = _get_inbox_dirs()
    if dirs is None:
        return None
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def scan_inbox() -> list[Path]:
    """inbox フォルダ内の .md ファイル一覧を返す。"""
    dirs = _get_inbox_dirs()
    if dirs is None:
        return []
    inbox = dirs["inbox"]
    if not inbox.exists():
        return []
    return sorted(inbox.glob("*.md"))


def accept_request(md_path: Path) -> Path | None:
    """inbox の MD を processing/ に移動して返す。既に消えていたら None。"""
    dirs = _get_inbox_dirs()
    if dirs is None or not md_path.exists():
        return None
    dirs["processing"].mkdir(parents=True, exist_ok=True)
    dst = dirs["processing"] / md_path.name
    try:
        md_path.rename(dst)
        return dst
    except Exception:
        return None


def complete_request(processing_path: Path, job_id: str) -> Path:
    """processing の MD を done/ に移動して results/{job_id}/ を作成・返す。"""
    dirs = _get_inbox_dirs()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    done_name = f"{timestamp}-{processing_path.name}"
    done_path = dirs["done"] / done_name
    dirs["done"].mkdir(parents=True, exist_ok=True)
    if processing_path.exists():
        shutil.move(str(processing_path), str(done_path))
    results_dir = dirs["results"] / datetime.now().strftime("%Y-%m-%d") / job_id
    results_dir.mkdir(parents=True, exist_ok=True)
    return results_dir


async def _poll_loop(process_fn):
    """ポーリングループ本体。process_fn(md_path) を呼び出す。"""
    print(f"[INFO] inbox ワーカー起動: ポーリング間隔 {OBSIDIAN_INBOX_POLL_SEC}秒", flush=True)
    ensure_inbox_dirs()
    while True:
        try:
            pending = scan_inbox()
            for md_path in pending:
                processing_path = accept_request(md_path)
                if processing_path is None:
                    continue
                print(f"[INFO] inbox 受理: {md_path.name}", flush=True)
                asyncio.create_task(process_fn(processing_path))
        except Exception as e:
            print(f"[WARN] inbox ポーリングエラー: {e}", flush=True)
        await asyncio.sleep(OBSIDIAN_INBOX_POLL_SEC)


def start_worker(process_fn) -> asyncio.Task | None:
    """ワーカーを起動してタスクを返す。OBSIDIAN_INBOX_ENABLED=false なら何もしない。"""
    global _worker_task
    if not OBSIDIAN_INBOX_ENABLED:
        return None
    if not OBSIDIAN_VAULT_PATH:
        print("[WARN] inbox ワーカー: OBSIDIAN_VAULT_PATH が未設定のためスキップ", flush=True)
        return None
    _worker_task = asyncio.create_task(_poll_loop(process_fn))
    return _worker_task


def stop_worker():
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
    _worker_task = None


def is_enabled() -> bool:
    return OBSIDIAN_INBOX_ENABLED and bool(OBSIDIAN_VAULT_PATH)


def get_status() -> dict:
    dirs = _get_inbox_dirs()
    if dirs is None:
        return {"enabled": False}
    pending = len(scan_inbox())
    processing = len(list(dirs["processing"].glob("*.md"))) if dirs["processing"].exists() else 0
    return {
        "enabled": is_enabled(),
        "host_suffix": _get_host_suffix(),
        "inbox_path": str(dirs["inbox"]),
        "pending": pending,
        "processing": processing,
        "poll_sec": OBSIDIAN_INBOX_POLL_SEC,
    }
