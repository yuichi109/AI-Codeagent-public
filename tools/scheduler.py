"""
定時実行スケジューラー本体。

server プロセス内の asyncio バックグラウンドタスクとして動く（別プロセスにしない）。
発火は既存のBG基盤（create_job）に乗せるだけ。occurrence 単位の冪等化により
万一ワーカーが二重でも「その回」は1回しか発火しない。

純関数 compute_due は単体テスト可能（DB非依存）。
"""
import asyncio
from datetime import datetime, timedelta

from tools import schedule_db

# 起動継続中に「今ちょうど予定時刻になった」と見なす猶予。
# これ以内なら自動発火、これより古ければ「取りこぼし」としてUI確認に回す。
ON_TIME_GRACE = timedelta(minutes=2)


# ---------------------------------------------------------------------------
# 純関数: occurrence 時刻の算出
# ---------------------------------------------------------------------------

def _parse(dt_str: str) -> datetime | None:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        return None


def _parse_hhmm(s: str) -> tuple[int, int] | None:
    try:
        hh, mm = s.split(":")
        return int(hh), int(mm)
    except (ValueError, AttributeError):
        return None


def _parse_dow_set(s) -> set[int] | None:
    """
    '0,1,2,3,4' のようなカンマ区切りを {0,1,2,3,4} に変換する（0=月..6=日）。
    空 / None / 不正なら None（＝曜日フィルタなし＝毎日）を返す。
    """
    if not s:
        return None
    out: set[int] = set()
    for part in str(s).split(","):
        part = part.strip()
        if part.isdigit():
            n = int(part)
            if 0 <= n <= 6:
                out.add(n)
    return out or None


def compute_due(task: dict, since: datetime, now: datetime) -> list[datetime]:
    """
    task の予定発火時刻のうち、(since, now] の範囲に入るものを昇順で返す純関数。

    recurrence_type:
      once     - run_at に1回
      daily    - 毎日 time_of_day。days_of_week('0,1,..'）があればその曜日のみ
      weekly   - 毎週 day_of_week 曜日の time_of_day（0=月..6=日）
      hourly   - anchor_at を起点に毎時
      interval - anchor_at を起点に interval_hours 時間ごと
    """
    rtype = task.get("recurrence_type")
    result: list[datetime] = []

    if rtype == "once":
        run_at = _parse(task.get("run_at"))
        if run_at and since < run_at <= now:
            result.append(run_at)
        return result

    if rtype in ("daily", "weekly"):
        hhmm = _parse_hhmm(task.get("time_of_day") or "")
        if not hhmm:
            return result
        hh, mm = hhmm
        dow = task.get("day_of_week")
        dset = _parse_dow_set(task.get("days_of_week"))  # daily の曜日フィルタ
        # since の日付から now の日付まで日単位で候補を作る
        day = since.date()
        end_day = now.date()
        while day <= end_day:
            cand = datetime(day.year, day.month, day.day, hh, mm)
            if rtype == "daily":
                ok_dow = (dset is None) or (cand.weekday() in dset)
            else:  # weekly
                ok_dow = (dow is not None and cand.weekday() == dow)
            if ok_dow and since < cand <= now:
                result.append(cand)
            day += timedelta(days=1)
        return result

    if rtype in ("hourly", "interval"):
        anchor = _parse(task.get("anchor_at"))
        if not anchor:
            return result
        step_h = 1 if rtype == "hourly" else (task.get("interval_hours") or 0)
        if step_h <= 0:
            return result
        step = timedelta(hours=step_h)
        # since 以降の最初の occurrence を求める
        if since < anchor:
            cand = anchor
        else:
            elapsed = (since - anchor).total_seconds()
            k = int(elapsed // step.total_seconds()) + 1
            cand = anchor + step * k
        # (since, now] を列挙（暴走防止に上限を設ける）
        guard = 0
        while cand <= now and guard < 100000:
            if cand > since:
                result.append(cand)
            cand += step
            guard += 1
        return result

    return result


def next_run(task: dict, now: datetime | None = None) -> datetime | None:
    """task の「次回予定時刻」を返す（UI表示用）。無ければ None。"""
    now = now or datetime.now()
    rtype = task.get("recurrence_type")

    if rtype == "once":
        run_at = _parse(task.get("run_at"))
        return run_at if run_at and run_at > now else None

    if rtype in ("daily", "weekly"):
        hhmm = _parse_hhmm(task.get("time_of_day") or "")
        if not hhmm:
            return None
        hh, mm = hhmm
        dow = task.get("day_of_week")
        dset = _parse_dow_set(task.get("days_of_week"))  # daily の曜日フィルタ
        for i in range(0, 8):  # 今日から1週間先まで探索
            d = (now + timedelta(days=i)).date()
            cand = datetime(d.year, d.month, d.day, hh, mm)
            if cand <= now:
                continue
            if rtype == "daily":
                if dset is None or cand.weekday() in dset:
                    return cand
            elif dow is not None and cand.weekday() == dow:
                return cand
        return None

    if rtype in ("hourly", "interval"):
        anchor = _parse(task.get("anchor_at"))
        if not anchor:
            return None
        step_h = 1 if rtype == "hourly" else (task.get("interval_hours") or 0)
        if step_h <= 0:
            return None
        step = timedelta(hours=step_h)
        if now < anchor:
            return anchor
        elapsed = (now - anchor).total_seconds()
        k = int(elapsed // step.total_seconds()) + 1
        return anchor + step * k

    return None


# ---------------------------------------------------------------------------
# 発火ループ
# ---------------------------------------------------------------------------

async def _tick(create_job_fn, catchup: timedelta, now: datetime) -> None:
    """
    1 tick 分の処理。create_job_fn(task) -> job_id を呼び出す。

    毎 tick で catchup 窓全体 (now-catchup, now] を走査する（last_tick に依存しない）。
    これにより「起動後に作られた過去予定のタスク」も取りこぼしとして拾える。
    重複発火は claim_occurrence の冪等性（UNIQUE 制約）で防ぐ。
    """
    tasks = await asyncio.to_thread(schedule_db.list_tasks, True)  # enabled_only
    window_start = now - catchup

    for task in tasks:
        try:
            occurrences = compute_due(task, window_start, now)
        except Exception as e:
            print(f"[scheduler] compute_due error task={task.get('id')}: {e}", flush=True)
            continue

        missed: list[datetime] = []
        for occ in occurrences:
            iso = occ.isoformat(timespec="seconds")
            if await asyncio.to_thread(schedule_db.has_occurrence, task["id"], iso):
                continue
            if (now - occ) <= ON_TIME_GRACE:
                # オンタイム → 自動発火
                await _fire(create_job_fn, task, iso, auto=True)
            else:
                missed.append(occ)

        # 取りこぼし: 最新1件だけ UI 確認(pending)、それ以外は静かに skipped
        if missed:
            missed.sort()
            latest = missed[-1]
            for occ in missed[:-1]:
                await asyncio.to_thread(
                    schedule_db.claim_occurrence, task["id"],
                    occ.isoformat(timespec="seconds"), "skipped",
                )
            iso_latest = latest.isoformat(timespec="seconds")
            run_id = await asyncio.to_thread(
                schedule_db.claim_occurrence, task["id"], iso_latest, "pending",
            )
            if run_id:
                print(f"[scheduler] 取りこぼし確認待ち task={task['id']} "
                      f"scheduled_at={iso_latest}", flush=True)


async def _fire(create_job_fn, task: dict, scheduled_at_iso: str,
                auto: bool) -> None:
    """occurrence を executed で確保し、成功したら BG ジョブを起こす。"""
    run_id = await asyncio.to_thread(
        schedule_db.claim_occurrence, task["id"], scheduled_at_iso, "executed",
    )
    if not run_id:
        return  # 既に他が確保済み（冪等）
    try:
        result = await asyncio.to_thread(create_job_fn, task)
        # create_job_fn は (job_id, 実行モデルラベル) を返す（旧来の str も許容）。
        job_id, actual_model = result if isinstance(result, tuple) else (result, None)
        await asyncio.to_thread(schedule_db.set_run_job, run_id, job_id)
        if actual_model:
            await asyncio.to_thread(schedule_db.set_run_actual_model, run_id, actual_model)
        print(f"[scheduler] 発火 task={task['id']} '{task.get('name')}' "
              f"job={job_id} model={actual_model} ({'auto' if auto else 'manual'})", flush=True)
    except Exception as e:
        await asyncio.to_thread(schedule_db.decide_run, run_id, "failed")
        print(f"[scheduler] 発火失敗 task={task['id']}: {e}", flush=True)


async def scheduler_loop(create_job_fn, *, tick_seconds: int = 30,
                         catchup_hours: int = 12) -> None:
    """
    定時スケジューラーのメインループ。
    create_job_fn(task) -> job_id : 発火時に呼ばれるコールバック（既存 create_job ラッパ）。
    """
    schedule_db.init_db()
    catchup = timedelta(hours=catchup_hours)
    print(f"[scheduler] 起動 tick={tick_seconds}s catchup={catchup_hours}h", flush=True)

    while True:
        try:
            now = datetime.now()
            await _tick(create_job_fn, catchup, now)
            # 古い決定済み記録の掃除（軽い間引き）
            await asyncio.to_thread(schedule_db.purge_old_runs, 50)
        except asyncio.CancelledError:
            print("[scheduler] 停止", flush=True)
            raise
        except Exception as e:
            print(f"[scheduler] loop error: {e}", flush=True)
        await asyncio.sleep(tick_seconds)
