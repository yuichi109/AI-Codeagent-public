"""
Background worker process for async agent jobs.

Polls jobs.db for pending jobs and runs them as asyncio tasks.
Designed to run as a separate process from the FastAPI server so that
HTTP connection drops do not kill running agent tasks.

Usage:
    python async_worker.py          # uses ASYNC_MAX_JOBS from .env
    python async_worker.py --jobs 3 # override concurrency
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is in sys.path when run directly
_project_root = Path(__file__).parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from config import ASYNC_MAX_JOBS
from tools.async_job_db import (
    init_db,
    reset_running_jobs,
    get_pending_jobs,
    get_job,
    update_job,
    append_chunk,
    purge_old_completed,
)
from agent_core import run_agent

# job_id -> asyncio.Task
_running: dict[str, "asyncio.Task[None]"] = {}

_POLL_INTERVAL = 2.0  # seconds between queue polls


async def _on_chunk(job_id: str, seq: int, ctype: str, content: str) -> None:
    """Callback: persist one output chunk to SQLite."""
    await asyncio.to_thread(append_chunk, job_id, seq, content, ctype)


async def _run_job(job: dict) -> None:
    job_id = job["job_id"]
    try:
        # Load provider config: prefer job-embedded config, fall back to .provider_config.json
        provider_config: dict = {}
        if job.get("provider_json"):
            try:
                provider_config = json.loads(job["provider_json"])
            except Exception:
                pass
        if not provider_config:
            cfg_file = _project_root / ".provider_config.json"
            if cfg_file.exists():
                try:
                    provider_config = json.loads(cfg_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

        if not provider_config:
            await asyncio.to_thread(
                update_job, job_id,
                status="failed",
                finished_at=datetime.utcnow().isoformat(),
                error="プロバイダー設定が見つかりません。/setup でセットアップしてください。",
            )
            return

        await asyncio.to_thread(
            update_job, job_id,
            status="running",
            started_at=datetime.utcnow().isoformat(),
        )

        await run_agent(
            job_id=job_id,
            message=job["message"],
            provider_config=provider_config,
            on_chunk=_on_chunk,
            max_turns=job.get("max_turns") or 30,
        )

        await asyncio.to_thread(
            update_job, job_id,
            status="done",
            finished_at=datetime.utcnow().isoformat(),
        )
        await asyncio.to_thread(purge_old_completed, 20)

    except asyncio.CancelledError:
        await asyncio.to_thread(
            update_job, job_id,
            status="cancelled",
            finished_at=datetime.utcnow().isoformat(),
        )

    except Exception as e:
        await asyncio.to_thread(
            update_job, job_id,
            status="failed",
            finished_at=datetime.utcnow().isoformat(),
            error=str(e)[:500],
        )
        print(f"[worker] job {job_id} failed: {e}", flush=True)

    finally:
        _running.pop(job_id, None)


async def _poll_loop(max_concurrent: int) -> None:
    print(f"[worker] started — max_concurrent={max_concurrent}", flush=True)

    while True:
        try:
            # Cancel tasks whose DB status is 'cancelling'
            for job_id, task in list(_running.items()):
                row = await asyncio.to_thread(get_job, job_id)
                if row and row.get("status") == "cancelling":
                    task.cancel()
                    print(f"[worker] cancelling job {job_id}", flush=True)

            # Pick up pending jobs up to concurrency limit
            slots = max_concurrent - len(_running)
            if slots > 0:
                pending = await asyncio.to_thread(get_pending_jobs)
                for job in pending:
                    if slots <= 0:
                        break
                    job_id = job["job_id"]
                    if job_id not in _running:
                        task = asyncio.create_task(_run_job(job))
                        _running[job_id] = task
                        print(f"[worker] dispatched job {job_id}", flush=True)
                        slots -= 1

        except Exception as e:
            print(f"[worker] poll error: {e}", flush=True)

        await asyncio.sleep(_POLL_INTERVAL)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Async agent background worker")
    parser.add_argument("--jobs", type=int, default=ASYNC_MAX_JOBS,
                        help=f"Max concurrent jobs (default: {ASYNC_MAX_JOBS} from .env)")
    args = parser.parse_args()

    init_db()
    n = reset_running_jobs()
    if n:
        print(f"[worker] {n} 件の中断ジョブ (running/cancelling) を failed にリセット", flush=True)
    asyncio.run(_poll_loop(max_concurrent=args.jobs))


if __name__ == "__main__":
    main()
