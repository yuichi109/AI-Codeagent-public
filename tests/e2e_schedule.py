"""スケジューラー e2e（実機API）。サーバー稼働中に実行する。"""
import os
import sys
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


print("=== 1. テンプレ作成 ===")
name = "e2eテスト-" + datetime.now().strftime("%H%M%S")
t = call("POST", "/schedule/templates", {"name": name, "prompt": "「e2e成功」とだけ一言で答えて"})
print(t)
tid = t["id"]

print("=== 2. 1分後に1回タスク登録 ===")
run_at = (datetime.now() + timedelta(seconds=70)).isoformat(timespec="seconds")
task = call("POST", "/schedule/tasks", {
    "name": "e2e-1回", "template_id": tid, "recurrence_type": "once", "run_at": run_at,
})
print("run_at=", run_at, "->", task)
task_id = task["id"]

print("=== 3. タスク一覧（next_run）===")
tasks = call("GET", "/schedule/tasks")
for x in tasks["tasks"]:
    if x["id"] == task_id:
        print("  next_run=", x["next_run"], "enabled=", x["enabled"])

print("=== 4. 発火を待機（最大100秒ポーリング）===")
fired = False
for i in range(20):
    time.sleep(5)
    runs = call("GET", f"/schedule/runs?task_id={task_id}")["runs"]
    if runs:
        r = runs[0]
        print(f"  [{i*5+5}s] run status={r['status']} job_id={r.get('job_id')}")
        if r["status"] == "executed" and r.get("job_id"):
            fired = True
            job_id = r["job_id"]
            break
    else:
        print(f"  [{i*5+5}s] まだ発火していない")

if not fired:
    print("FAIL: 発火しなかった")
    sys.exit(1)

print("=== 5. ジョブ結果確認 ===")
for i in range(20):
    time.sleep(3)
    job = call("GET", f"/async-agent/jobs/{job_id}")
    print(f"  [{i*3+3}s] job status={job['status']}")
    if job["status"] in ("done", "failed", "cancelled"):
        out = "".join(c["content"] for c in job.get("chunks", []))
        print("  出力(先頭200):", out[:200].replace("\n", " "))
        break

print("=== 6. once タスクが発火後に無効化されたか ===")
tasks = call("GET", "/schedule/tasks")
for x in tasks["tasks"]:
    if x["id"] == task_id:
        print("  enabled=", x["enabled"], "(False が期待値)")

print("=== 7. 後片付け ===")
print(call("DELETE", f"/schedule/tasks/{task_id}"))
print(call("DELETE", f"/schedule/templates/{tid}"))
print("\nDONE")
