"""取りこぼし（キャッチアップ）e2e。過去の予定を作り pending 確認 → skip → 再質問されないことを検証。"""
import os
import time
import json
import urllib.request
from datetime import datetime, timedelta

B = f"http://localhost:{os.getenv('APP_PORT', '8000')}"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


name = "catchup-" + datetime.now().strftime("%H%M%S")
tid = call("POST", "/schedule/templates", {"name": name, "prompt": "test"})["id"]

# 30分前の予定（grace 2分より古く、12h窓内）→ 取りこぼし扱い
past = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
task_id = call("POST", "/schedule/tasks", {
    "name": "catchup-1回", "template_id": tid, "recurrence_type": "once", "run_at": past,
})["id"]
print(f"過去予定 run_at={past} task_id={task_id}")

print("=== 次tickで pending になるのを待機 ===")
pending_run = None
for i in range(8):
    time.sleep(5)
    pend = call("GET", "/schedule/runs?status=pending")["runs"]
    mine = [r for r in pend if r["task_id"] == task_id]
    if mine:
        pending_run = mine[0]
        print(f"  [{i*5+5}s] pending 検出 run_id={pending_run['id']} scheduled_at={pending_run['scheduled_at']}")
        break
    print(f"  [{i*5+5}s] まだ pending なし")

assert pending_run, "FAIL: 取りこぼしが pending にならなかった"

print("=== skip 決定 ===")
print(call("POST", f"/schedule/runs/{pending_run['id']}/decide", {"action": "skip"}))

print("=== 数tick待って再質問されないことを確認 ===")
time.sleep(35)
pend = call("GET", "/schedule/runs?status=pending")["runs"]
mine = [r for r in pend if r["task_id"] == task_id]
assert not mine, f"FAIL: skip 後も pending が再生成された: {mine}"
print("  OK: 再質問なし（skip フラグが効いている）")

print("=== 後片付け ===")
call("DELETE", f"/schedule/tasks/{task_id}")
call("DELETE", f"/schedule/templates/{tid}")
print("\nDONE")
