"""
scheduler.compute_due と schedule_db.claim_occurrence の単体テスト。

実行: WSL 上で
    cd ~/AI-Codeagent && python3 -m pytest tests/test_scheduler.py -q
pytest が無ければ：
    python3 tests/test_scheduler.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import scheduler  # noqa: E402


def D(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# compute_due
# ---------------------------------------------------------------------------

def test_once_in_range():
    task = {"recurrence_type": "once", "run_at": "2026-06-08T09:00:00"}
    due = scheduler.compute_due(task, D("2026-06-08T08:00:00"), D("2026-06-08T10:00:00"))
    assert due == [D("2026-06-08T09:00:00")]


def test_once_out_of_range():
    task = {"recurrence_type": "once", "run_at": "2026-06-08T09:00:00"}
    # 既に過ぎた範囲（since が run_at より後）
    due = scheduler.compute_due(task, D("2026-06-08T09:30:00"), D("2026-06-08T10:00:00"))
    assert due == []


def test_daily_single():
    task = {"recurrence_type": "daily", "time_of_day": "09:00"}
    due = scheduler.compute_due(task, D("2026-06-08T08:00:00"), D("2026-06-08T09:30:00"))
    assert due == [D("2026-06-08T09:00:00")]


def test_daily_spans_two_days():
    task = {"recurrence_type": "daily", "time_of_day": "09:00"}
    due = scheduler.compute_due(task, D("2026-06-07T10:00:00"), D("2026-06-08T09:30:00"))
    assert due == [D("2026-06-07T10:00:00") and D("2026-06-08T09:00:00")] or \
        due == [D("2026-06-08T09:00:00")]
    # 6/7 09:00 は since(6/7 10:00) より前なので含まれない。6/8 09:00 のみ。
    assert due == [D("2026-06-08T09:00:00")]


def test_weekly_matches_dow():
    # 2026-06-08 は月曜(weekday=0)
    task = {"recurrence_type": "weekly", "time_of_day": "09:00", "day_of_week": 0}
    due = scheduler.compute_due(task, D("2026-06-08T08:00:00"), D("2026-06-08T10:00:00"))
    assert due == [D("2026-06-08T09:00:00")]


def test_weekly_no_match_dow():
    # 火曜(1)を指定、対象範囲は月曜
    task = {"recurrence_type": "weekly", "time_of_day": "09:00", "day_of_week": 1}
    due = scheduler.compute_due(task, D("2026-06-08T08:00:00"), D("2026-06-08T10:00:00"))
    assert due == []


def test_daily_with_weekday_filter_skips_weekend():
    # 2026-06-13 は土曜(weekday=5), 06-14 は日曜(6), 06-15 は月曜(0)
    # 平日のみ(0..4)指定 → 土日は発火せず、月曜のみ
    task = {"recurrence_type": "daily", "time_of_day": "09:00",
            "days_of_week": "0,1,2,3,4"}
    due = scheduler.compute_due(task, D("2026-06-12T23:00:00"),
                                D("2026-06-15T09:30:00"))
    assert due == [D("2026-06-15T09:00:00")]


def test_daily_with_weekday_filter_matches():
    # 金曜(2026-06-12, weekday=4) は平日指定に含まれる
    task = {"recurrence_type": "daily", "time_of_day": "09:00",
            "days_of_week": "0,1,2,3,4"}
    due = scheduler.compute_due(task, D("2026-06-12T08:00:00"),
                                D("2026-06-12T10:00:00"))
    assert due == [D("2026-06-12T09:00:00")]


def test_daily_empty_filter_means_every_day():
    # days_of_week 空 → 従来通り毎日（土曜でも発火）
    task = {"recurrence_type": "daily", "time_of_day": "09:00", "days_of_week": ""}
    due = scheduler.compute_due(task, D("2026-06-13T08:00:00"),
                                D("2026-06-13T10:00:00"))
    assert due == [D("2026-06-13T09:00:00")]


def test_next_run_daily_weekday_filter():
    # 金曜 09:30 時点で平日のみ指定 → 次回は月曜(06-15) 09:00
    task = {"recurrence_type": "daily", "time_of_day": "09:00",
            "days_of_week": "0,1,2,3,4"}
    nr = scheduler.next_run(task, now=D("2026-06-12T09:30:00"))
    assert nr == D("2026-06-15T09:00:00")


def test_parse_dow_set():
    assert scheduler._parse_dow_set("0,1,2,3,4") == {0, 1, 2, 3, 4}
    assert scheduler._parse_dow_set("") is None
    assert scheduler._parse_dow_set(None) is None
    assert scheduler._parse_dow_set("0,7,9") == {0}  # 範囲外は無視


def test_hourly():
    task = {"recurrence_type": "hourly", "anchor_at": "2026-06-08T00:00:00"}
    due = scheduler.compute_due(task, D("2026-06-08T08:05:00"), D("2026-06-08T11:05:00"))
    assert due == [D("2026-06-08T09:00:00"), D("2026-06-08T10:00:00"),
                   D("2026-06-08T11:00:00")]


def test_interval_3h():
    task = {"recurrence_type": "interval", "anchor_at": "2026-06-08T00:00:00",
            "interval_hours": 3}
    due = scheduler.compute_due(task, D("2026-06-08T02:00:00"), D("2026-06-08T12:30:00"))
    assert due == [D("2026-06-08T03:00:00"), D("2026-06-08T06:00:00"),
                   D("2026-06-08T09:00:00"), D("2026-06-08T12:00:00")]


def test_interval_before_anchor():
    task = {"recurrence_type": "interval", "anchor_at": "2026-06-08T10:00:00",
            "interval_hours": 2}
    due = scheduler.compute_due(task, D("2026-06-08T08:00:00"), D("2026-06-08T12:30:00"))
    assert due == [D("2026-06-08T10:00:00"), D("2026-06-08T12:00:00")]


def test_interval_zero_guard():
    task = {"recurrence_type": "interval", "anchor_at": "2026-06-08T00:00:00",
            "interval_hours": 0}
    due = scheduler.compute_due(task, D("2026-06-08T00:00:00"), D("2026-06-08T12:00:00"))
    assert due == []


def test_boundary_exclusive_since_inclusive_now():
    # since はちょうど除外、now はちょうど含む
    task = {"recurrence_type": "daily", "time_of_day": "09:00"}
    assert scheduler.compute_due(task, D("2026-06-08T09:00:00"),
                                 D("2026-06-08T10:00:00")) == []
    assert scheduler.compute_due(task, D("2026-06-08T08:00:00"),
                                 D("2026-06-08T09:00:00")) == [D("2026-06-08T09:00:00")]


# ---------------------------------------------------------------------------
# claim_occurrence（冪等性）
# ---------------------------------------------------------------------------

def test_claim_occurrence_idempotent():
    # 一時DBに差し替え
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "schedule_test.db"
    from tools import schedule_db
    orig = schedule_db.DB_PATH
    schedule_db.DB_PATH = db_path
    try:
        schedule_db.init_db()
        tpl = schedule_db.create_template("t", "do something")
        tid = schedule_db.create_task("task1", tpl, "daily", time_of_day="09:00")
        iso = "2026-06-08T09:00:00"
        r1 = schedule_db.claim_occurrence(tid, iso, "executed")
        r2 = schedule_db.claim_occurrence(tid, iso, "executed")
        assert r1 is not None, "1回目は確保できる"
        assert r2 is None, "2回目は冪等で None"
        # 翌日は別occurrence
        r3 = schedule_db.claim_occurrence(tid, "2026-06-09T09:00:00", "executed")
        assert r3 is not None, "翌日の回は別occurrenceなので確保できる"
    finally:
        schedule_db.DB_PATH = orig


def test_decide_and_clear():
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "schedule_test2.db"
    from tools import schedule_db
    orig = schedule_db.DB_PATH
    schedule_db.DB_PATH = db_path
    try:
        schedule_db.init_db()
        tpl = schedule_db.create_template("t", "p")
        tid = schedule_db.create_task("task", tpl, "daily", time_of_day="09:00")
        iso = "2026-06-08T09:00:00"
        run_id = schedule_db.claim_occurrence(tid, iso, "pending")
        assert run_id is not None
        schedule_db.decide_run(run_id, "skipped")
        runs = schedule_db.list_runs(task_id=tid)
        assert runs[0]["status"] == "skipped"
        # clear で未実行に戻る
        schedule_db.clear_run(run_id)
        assert schedule_db.has_occurrence(tid, iso) is False
    finally:
        schedule_db.DB_PATH = orig


# ---------------------------------------------------------------------------
# 簡易ランナー（pytest 無し環境用）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in funcs:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            print(f"ERR   {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(funcs)} passed")
    sys.exit(0 if passed == len(funcs) else 1)
