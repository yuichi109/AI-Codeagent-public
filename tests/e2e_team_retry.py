"""チーム方式 Step3 ①② の決定論テスト（LLM不使用）。
差し戻し→最終トライ格上げ→status機械記録 の経路を、run_team_member をフェイク化して確認する。
実行: venv/bin/python tests/e2e_team_retry.py
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server
import tools.team_tools as team_tools
import tools.multi_agent_tools as ma_tools

JOB_ID = "ztest_retry"
JOB_DIR = server.ALLOWED_WORK_DIR / "jobs" / JOB_ID

used_models: list[str] = []


async def fake_run_team_member(*, member_name, role, system_prompt, task_prompt,
                               all_tools, base_executor, async_client, model,
                               job_dir, timeout_sec=None, **kw):
    """格上げモデル（strong-model）のときだけ成果ファイルを書く＝cheap では失敗が続く。"""
    used_models.append(model)
    if model == "strong-model":
        out = Path(job_dir) / "code" / "out.py"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("print('hello from escalated model')\n", encoding="utf-8")
    return "done"


async def fake_interpret(user_message, plan, client, model):
    return {"action": "execute", "notes": ""}


async def fake_final_report(client, model, job_dir):
    return "（テスト用ダミー報告書）"


def setup_job():
    if JOB_DIR.exists():
        shutil.rmtree(JOB_DIR)
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    plan = {
        "mode": "team",
        "max_parallel": 2,
        "tasks": {
            "t1": {
                "role": "coding",
                "prompt": "code/out.py を作成して hello を出力するコードを書く。",
                "depends_on": [],
                "files": ["code/out.py"],
                "timeout_sec": 60,
                "preset_id": "cheapprov",
                "model": "cheap-model",
            }
        },
    }
    (JOB_DIR / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (JOB_DIR / "original_task.txt").write_text("テスト", encoding="utf-8")


def patch():
    team_tools.run_team_member = fake_run_team_member
    ma_tools.generate_final_report = fake_final_report
    server._interpret_plan_response = fake_interpret
    server._make_async_client_for = lambda preset: object()
    server._load_ma_config = lambda *a, **k: {"dispatcher": {"preset_id": "strongprov", "model": "strong-model"}}


async def main():
    setup_job()
    patch()
    sse = ""
    async for chunk in server.team_agent_stream(
        "OK 実行して", agent_mode="balance", workspace_scope="", resume_job_id=JOB_ID
    ):
        # SSE の answer_chunk から本文を抜き出して連結
        for line in chunk.splitlines():
            if line.startswith("data: "):
                try:
                    ev = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "answer_chunk":
                    sse += ev["content"]

    print("===== SSE 出力 =====")
    print(sse)
    print("===== used_models =====", used_models)
    status = (JOB_DIR / "status.md").read_text(encoding="utf-8") if (JOB_DIR / "status.md").exists() else "(なし)"
    print("===== status.md =====")
    print(status)

    # ---- アサーション ----
    checks = {
        "差し戻し1回目あり": "差し戻し（1回目）" in sse,
        "差し戻し2回目あり": "差し戻し（2回目）" in sse,
        "最終トライで格上げ表示(⬆️)あり": "⬆️" in sse and "上位モデルで再試行" in sse,
        "最終的に完了 ✅": "完了 ✅" in sse,
        "格上げで strong-model が使われた": "strong-model" in used_models,
        "cheap で2回試行（計3トライ）": used_models[:2] == ["cheap-model", "cheap-model"],
        "status はリード形式のみ（二重化なし）": "- coding:" in status and "\ncoding: 完了\n" not in ("\n" + status + "\n"),
    }
    print("\n===== 判定 =====")
    ok = True
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
        ok = ok and v

    shutil.rmtree(JOB_DIR, ignore_errors=True)
    print("\n結果:", "ALL PASS ✅" if ok else "FAIL ❌")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
