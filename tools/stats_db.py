"""
SQLite-backed store for usage/operation statistics (統計ダッシュボード).

別DB (data/stats.db) に隔離する設計で、既存の schedule.db / jobs.db には一切触れない。

最重要要件＝肥大化させない。生ログは一切ためず、保存は「日 × プロバイダー × モデル」ごとの
集計1行（ロールアップ）だけにする。行数は使用量と無関係（10モデル × 365日 × 5年 ≈ 18,000行）。
record_usage() は UPSERT で該当日の1行を増分更新するだけ＝呼ばれた回数に比例して行が増えない。

第1の軸（セッション74で合意）＝「モデル別利用割合」。requests（呼び出し回数）と
total_tokens の両方を持たせ、ダッシュボード側でどちらの割合でも出せるようにする。

Tables:
  usage_daily  - 日 × プロバイダー × モデル ごとの利用集計（永久・極小）
"""
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)

DB_PATH = _DATA_DIR / "stats.db"

# ハードキャップ: usage_daily の最大行数。超えたら古い日から自動トリムする。
# 1日あたり最大でもモデル数ぶんの行しか増えないため、通常はまず到達しない安全弁。
MAX_USAGE_ROWS = 50000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_daily (
    day               TEXT NOT NULL,   -- YYYY-MM-DD（ローカル日付）
    provider          TEXT NOT NULL,   -- azure / foundry / openai / openrouter / groq / local ...
    model             TEXT NOT NULL,   -- 実応答モデル名
    requests          INTEGER NOT NULL DEFAULT 0,  -- LLM 呼び出し回数
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (day, provider, model)
);

CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_daily (day);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


_initialized = False


def init_db():
    global _initialized
    with _connect() as conn:
        conn.executescript(_SCHEMA)
    _initialized = True


def _ensure_init():
    """記録前にスキーマ存在を保証する（別プロセス＝BGワーカーから呼ばれても安全に）。"""
    if not _initialized:
        init_db()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# 記録（ロールアップ UPSERT）
# ---------------------------------------------------------------------------

def record_usage(provider: str, model: str,
                 prompt_tokens: int = 0, completion_tokens: int = 0,
                 total_tokens: int = 0, day: str | None = None) -> None:
    """
    1回の LLM 呼び出しぶんの利用を、その日の集計行に足し込む（UPSERT 増分）。

    生ログは残さない＝呼ばれても行が増えるのは「その日に初めて使ったモデル」のときだけ。
    数値が壊れた入力（None など）でも落ちないようガードする。
    """
    provider = (provider or "unknown").strip() or "unknown"
    model = (model or "unknown").strip() or "unknown"
    p = int(prompt_tokens or 0)
    c = int(completion_tokens or 0)
    t = int(total_tokens or 0) or (p + c)
    d = day or _today()
    ts = _now()
    _ensure_init()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO usage_daily
                   (day, provider, model, requests,
                    prompt_tokens, completion_tokens, total_tokens, updated_at)
               VALUES (?, ?, ?, 1, ?, ?, ?, ?)
               ON CONFLICT(day, provider, model) DO UPDATE SET
                   requests          = requests + 1,
                   prompt_tokens     = prompt_tokens + excluded.prompt_tokens,
                   completion_tokens = completion_tokens + excluded.completion_tokens,
                   total_tokens      = total_tokens + excluded.total_tokens,
                   updated_at        = excluded.updated_at""",
            (d, provider, model, p, c, t, ts),
        )
        _trim(conn)


def _trim(conn: sqlite3.Connection):
    """行数ハードキャップ。MAX_USAGE_ROWS を超えたら古い日から削除する。"""
    n = conn.execute("SELECT COUNT(*) AS n FROM usage_daily").fetchone()["n"]
    if n <= MAX_USAGE_ROWS:
        return
    # 古い日の行から落とす（1日単位でまとめて消す）
    over = n - MAX_USAGE_ROWS
    rows = conn.execute(
        "SELECT day FROM usage_daily GROUP BY day ORDER BY day ASC"
    ).fetchall()
    removed = 0
    for r in rows:
        if removed >= over:
            break
        cur = conn.execute("DELETE FROM usage_daily WHERE day=?", (r["day"],))
        removed += cur.rowcount


# ---------------------------------------------------------------------------
# 集計（ダッシュボード/API 用）
# ---------------------------------------------------------------------------

def _since_str(days: int | None) -> str | None:
    if not days or days <= 0:
        return None
    return (date.today() - timedelta(days=days - 1)).isoformat()


def model_breakdown(days: int | None = 30) -> list[dict]:
    """
    モデル別の利用集計（第1の軸）。直近 days 日ぶん。days=None/0 で全期間。
    requests・total_tokens を降順で返す。割合はフロント側で算出する。
    """
    since = _since_str(days)
    sql = (
        "SELECT provider, model, "
        "SUM(requests) AS requests, "
        "SUM(prompt_tokens) AS prompt_tokens, "
        "SUM(completion_tokens) AS completion_tokens, "
        "SUM(total_tokens) AS total_tokens "
        "FROM usage_daily "
    )
    params: list = []
    if since:
        sql += "WHERE day >= ? "
        params.append(since)
    sql += "GROUP BY provider, model ORDER BY requests DESC, total_tokens DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def daily_series(days: int | None = 30) -> list[dict]:
    """日別の合計（推移グラフ用）。直近 days 日ぶん。"""
    since = _since_str(days)
    sql = (
        "SELECT day, "
        "SUM(requests) AS requests, "
        "SUM(total_tokens) AS total_tokens "
        "FROM usage_daily "
    )
    params: list = []
    if since:
        sql += "WHERE day >= ? "
        params.append(since)
    sql += "GROUP BY day ORDER BY day ASC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def totals(days: int | None = 30) -> dict:
    """期間合計（サマリーカード用）。"""
    since = _since_str(days)
    sql = (
        "SELECT "
        "COUNT(DISTINCT provider || '/' || model) AS models, "
        "SUM(requests) AS requests, "
        "SUM(prompt_tokens) AS prompt_tokens, "
        "SUM(completion_tokens) AS completion_tokens, "
        "SUM(total_tokens) AS total_tokens "
        "FROM usage_daily "
    )
    params: list = []
    if since:
        sql += "WHERE day >= ? "
        params.append(since)
    with _connect() as conn:
        row = conn.execute(sql, params).fetchone()
        d = dict(row) if row else {}
    # NULL（データ無し）を 0 に正規化
    for k in ("models", "requests", "prompt_tokens", "completion_tokens", "total_tokens"):
        d[k] = d.get(k) or 0
    return d
