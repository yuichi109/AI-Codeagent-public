"""
SQLite-backed store for the scheduled-task feature (定時実行スケジューラー).

別DB (data/schedule.db) を使うことで既存の jobs.db / async_job_db.py には一切触れない。

Tables:
  task_templates   - 実行内容テンプレート（名前 + エージェントへの指示文）
  scheduled_tasks  - スケジュール定義（いつ実行するか）
  task_runs        - 実行記録 = occurrence（回ごと）のフラグ
"""
import sqlite3
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)

DB_PATH = _DATA_DIR / "schedule.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    prompt      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    template_id     INTEGER NOT NULL,
    recurrence_type TEXT NOT NULL,
    time_of_day     TEXT,
    day_of_week     INTEGER,
    interval_hours  INTEGER,
    run_at          TEXT,
    anchor_at       TEXT,
    workspace_scope TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL,
    scheduled_at TEXT NOT NULL,
    status       TEXT NOT NULL,
    job_id       TEXT,
    decided_at   TEXT,
    UNIQUE(task_id, scheduled_at)
);

CREATE INDEX IF NOT EXISTS idx_runs_task ON task_runs (task_id, scheduled_at);
"""

# 有効な繰り返し種別
RECURRENCE_TYPES = ("daily", "weekly", "once", "hourly", "interval")
# occurrence のステータス
RUN_STATUSES = ("pending", "executed", "skipped", "failed")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# task_templates（実行内容テンプレート）
# ---------------------------------------------------------------------------

def create_template(name: str, prompt: str) -> int:
    ts = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO task_templates (name, prompt, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (name, prompt, ts, ts),
        )
        return cur.lastrowid


def list_templates() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM task_templates ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]


def get_template(template_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM task_templates WHERE id=?", (template_id,)
        ).fetchone()
        return dict(row) if row else None


def get_template_by_name(name: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM task_templates WHERE name=?", (name,)
        ).fetchone()
        return dict(row) if row else None


def update_template(template_id: int, name: str | None = None,
                    prompt: str | None = None):
    fields = {}
    if name is not None:
        fields["name"] = name
    if prompt is not None:
        fields["prompt"] = prompt
    if not fields:
        return
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE task_templates SET {sets} WHERE id=?",
            list(fields.values()) + [template_id],
        )


def delete_template(template_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM task_templates WHERE id=?", (template_id,))


def template_in_use(template_id: int) -> int:
    """このテンプレを参照しているタスク数を返す（削除前チェック用）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM scheduled_tasks WHERE template_id=?",
            (template_id,),
        ).fetchone()
        return row["n"]


# ---------------------------------------------------------------------------
# scheduled_tasks（スケジュール定義）
# ---------------------------------------------------------------------------

_TASK_FIELDS = (
    "name", "template_id", "recurrence_type", "time_of_day", "day_of_week",
    "interval_hours", "run_at", "anchor_at", "workspace_scope", "enabled",
)


def create_task(name: str, template_id: int, recurrence_type: str,
                time_of_day: str | None = None, day_of_week: int | None = None,
                interval_hours: int | None = None, run_at: str | None = None,
                anchor_at: str | None = None, workspace_scope: str = "",
                enabled: bool = True) -> int:
    if recurrence_type not in RECURRENCE_TYPES:
        raise ValueError(f"不正な recurrence_type: {recurrence_type}")
    # interval系の起点が無ければ現在時刻を起点にする
    if recurrence_type in ("hourly", "interval") and not anchor_at:
        anchor_at = _now()
    ts = _now()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO scheduled_tasks
               (name, template_id, recurrence_type, time_of_day, day_of_week,
                interval_hours, run_at, anchor_at, workspace_scope, enabled,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, template_id, recurrence_type, time_of_day, day_of_week,
             interval_hours, run_at, anchor_at, workspace_scope,
             1 if enabled else 0, ts, ts),
        )
        return cur.lastrowid


def list_tasks(enabled_only: bool = False) -> list[dict]:
    sql = (
        "SELECT t.*, tpl.name AS template_name, tpl.prompt AS template_prompt "
        "FROM scheduled_tasks t "
        "LEFT JOIN task_templates tpl ON t.template_id = tpl.id "
    )
    if enabled_only:
        sql += "WHERE t.enabled=1 "
    sql += "ORDER BY t.created_at DESC"
    with _connect() as conn:
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]


def get_task(task_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT t.*, tpl.name AS template_name, tpl.prompt AS template_prompt "
            "FROM scheduled_tasks t "
            "LEFT JOIN task_templates tpl ON t.template_id = tpl.id "
            "WHERE t.id=?",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None


def update_task(task_id: int, **kwargs):
    fields = {k: v for k, v in kwargs.items() if k in _TASK_FIELDS}
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    if "recurrence_type" in fields and fields["recurrence_type"] not in RECURRENCE_TYPES:
        raise ValueError(f"不正な recurrence_type: {fields['recurrence_type']}")
    if not fields:
        return
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE scheduled_tasks SET {sets} WHERE id=?",
            list(fields.values()) + [task_id],
        )


def set_enabled(task_id: int, enabled: bool):
    with _connect() as conn:
        conn.execute(
            "UPDATE scheduled_tasks SET enabled=?, updated_at=? WHERE id=?",
            (1 if enabled else 0, _now(), task_id),
        )


def delete_task(task_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM task_runs WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,))


# ---------------------------------------------------------------------------
# task_runs（occurrence のフラグ）
# ---------------------------------------------------------------------------

def claim_occurrence(task_id: int, scheduled_at: str, status: str,
                     job_id: str | None = None) -> int | None:
    """
    その回（task_id × scheduled_at）の実行権を原子的に確保する。
    UNIQUE 制約 + INSERT OR IGNORE により、既に記録があれば None（=他が確保済み or 決定済み）。
    新規に確保できた場合のみ、その run_id（int）を返す。
    """
    if status not in RUN_STATUSES:
        raise ValueError(f"不正な status: {status}")
    decided = _now() if status in ("executed", "skipped") else None
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO task_runs "
            "(task_id, scheduled_at, status, job_id, decided_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, scheduled_at, status, job_id, decided),
        )
        return cur.lastrowid if cur.rowcount == 1 else None


def has_occurrence(task_id: int, scheduled_at: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM task_runs WHERE task_id=? AND scheduled_at=?",
            (task_id, scheduled_at),
        ).fetchone()
        return row is not None


def get_run(run_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM task_runs WHERE id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None


def set_run_job(run_id: int, job_id: str):
    with _connect() as conn:
        conn.execute(
            "UPDATE task_runs SET job_id=? WHERE id=?", (job_id, run_id)
        )


def decide_run(run_id: int, status: str, job_id: str | None = None):
    """pending な occurrence をユーザー決定（executed/skipped）で確定させる。"""
    if status not in ("executed", "skipped", "failed"):
        raise ValueError(f"不正な status: {status}")
    with _connect() as conn:
        if job_id is not None:
            conn.execute(
                "UPDATE task_runs SET status=?, job_id=?, decided_at=? WHERE id=?",
                (status, job_id, _now(), run_id),
            )
        else:
            conn.execute(
                "UPDATE task_runs SET status=?, decided_at=? WHERE id=?",
                (status, _now(), run_id),
            )


def list_runs(task_id: int | None = None, since: str | None = None,
              status: str | None = None, limit: int = 200) -> list[dict]:
    sql = (
        "SELECT r.*, t.name AS task_name "
        "FROM task_runs r "
        "LEFT JOIN scheduled_tasks t ON r.task_id = t.id "
        "WHERE 1=1 "
    )
    params: list = []
    if task_id is not None:
        sql += "AND r.task_id=? "
        params.append(task_id)
    if since is not None:
        sql += "AND r.scheduled_at >= ? "
        params.append(since)
    if status is not None:
        sql += "AND r.status=? "
        params.append(status)
    sql += "ORDER BY r.scheduled_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def list_pending_runs() -> list[dict]:
    """取りこぼし確認待ち（pending）の occurrence 一覧。"""
    return list_runs(status="pending")


def clear_run(run_id: int):
    """フラグの手動解除（実行記録を削除して未実行扱いに戻す）。"""
    with _connect() as conn:
        conn.execute("DELETE FROM task_runs WHERE id=?", (run_id,))


def purge_old_runs(keep_per_task: int = 50):
    """各タスクにつき新しい順に keep_per_task 件だけ残し、古い決定済み記録を削除する。"""
    with _connect() as conn:
        task_ids = [r["task_id"] for r in conn.execute(
            "SELECT DISTINCT task_id FROM task_runs"
        ).fetchall()]
        deleted = 0
        for tid in task_ids:
            rows = conn.execute(
                "SELECT id FROM task_runs WHERE task_id=? AND status!='pending' "
                "ORDER BY scheduled_at DESC",
                (tid,),
            ).fetchall()
            for r in rows[keep_per_task:]:
                conn.execute("DELETE FROM task_runs WHERE id=?", (r["id"],))
                deleted += 1
    return deleted
