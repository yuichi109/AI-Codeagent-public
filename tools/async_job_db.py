"""
SQLite-backed job store for async agent execution.

Tables:
  async_jobs   - job metadata (status, message, config, timestamps)
  async_chunks - streaming output chunks per job
"""
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from config import ALLOWED_WORK_DIR

DB_PATH = ALLOWED_WORK_DIR / "jobs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS async_jobs (
    job_id       TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'pending',
    message      TEXT NOT NULL,
    provider_json TEXT,
    max_turns    INTEGER DEFAULT 30,
    turn_count   INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS async_chunks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id   TEXT NOT NULL,
    seq      INTEGER NOT NULL,
    ctype    TEXT NOT NULL DEFAULT 'text',
    content  TEXT NOT NULL,
    ts       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_job_seq
    ON async_chunks (job_id, seq);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def create_job(message: str, provider_config: dict, max_turns: int = 30) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _connect() as conn:
        conn.execute(
            """INSERT INTO async_jobs
               (job_id, message, provider_json, max_turns, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (job_id, message, json.dumps(provider_config), max_turns,
             datetime.utcnow().isoformat()),
        )
    return job_id


def update_job(job_id: str, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [job_id]
    with _connect() as conn:
        conn.execute(f"UPDATE async_jobs SET {sets} WHERE job_id=?", vals)


def append_chunk(job_id: str, seq: int, content: str, ctype: str = "text"):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO async_chunks (job_id, seq, ctype, content, ts)
               VALUES (?, ?, ?, ?, ?)""",
            (job_id, seq, ctype, content, datetime.utcnow().isoformat()),
        )


def get_job(job_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM async_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        return dict(row) if row else None


def get_chunks(job_id: str, after_seq: int = -1) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT seq, ctype, content
               FROM async_chunks
               WHERE job_id=? AND seq>?
               ORDER BY seq""",
            (job_id, after_seq),
        ).fetchall()
        return [{"seq": r["seq"], "type": r["ctype"], "content": r["content"]}
                for r in rows]


def list_jobs(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT job_id, status, created_at, finished_at,
                      turn_count, max_turns,
                      substr(message, 1, 80) AS message_preview
               FROM async_jobs
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_jobs() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM async_jobs
               WHERE status='pending'
               ORDER BY created_at ASC""",
        ).fetchall()
        return [dict(r) for r in rows]


def delete_job(job_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM async_chunks WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM async_jobs WHERE job_id=?", (job_id,))


def purge_old_completed(keep: int = 20):
    """Keep only the newest `keep` completed/failed/cancelled jobs; delete the rest."""
    terminal = ("done", "failed", "cancelled")
    with _connect() as conn:
        rows = conn.execute(
            """SELECT job_id FROM async_jobs
               WHERE status IN ('done','failed','cancelled')
               ORDER BY created_at DESC""",
        ).fetchall()
        to_delete = [r["job_id"] for r in rows[keep:]]
        for jid in to_delete:
            conn.execute("DELETE FROM async_chunks WHERE job_id=?", (jid,))
            conn.execute("DELETE FROM async_jobs  WHERE job_id=?", (jid,))
    return len(to_delete)
