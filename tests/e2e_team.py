"""チーム方式 e2e（Phase1 計画 → Phase2 実行）。
サーバー起動中に `venv/bin/python tests/e2e_team.py` で実行（実 LLM を呼ぶのでトークン消費あり）。"""
import json
import re
import sys
import httpx

BASE = "http://localhost:8000"
MSG = "電卓を作って。足し算・引き算・掛け算・割り算をそれぞれ別ファイルで実装して、最後にまとめてテストして。"


def stream_chat(payload):
    chunks = []
    with httpx.stream("POST", f"{BASE}/chat", json=payload, timeout=280) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            chunks.append(ev)
            if ev.get("type") == "answer_chunk":
                print(ev["content"], end="", flush=True)
            elif ev.get("type") == "plan_ready":
                print(f"\n[plan_ready job={ev['job_id']} roles={ev['roles']}]")
            elif ev.get("type") == "answer_done":
                print("\n[done]")
    return chunks


print("=== Phase1: 計画 ===")
p1 = stream_chat({"message": MSG, "multi_agent": True, "team_mode": True})
job_id = next((e["job_id"] for e in p1 if e.get("type") == "plan_ready"), None)
if not job_id:
    print("FAIL: plan_ready が来ませんでした")
    sys.exit(1)

print(f"\n\n=== Phase2: 実行 (job={job_id}) ===")
stream_chat({"message": "OK 実行して", "multi_agent": True, "team_mode": True, "resume_job_id": job_id})
print("\n\nDONE")
