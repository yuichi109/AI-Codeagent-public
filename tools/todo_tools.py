import json
from pathlib import Path
from config import ALLOWED_WORK_DIR

# タスクリストの保存先（workspace 内の隠しファイル）
_TODO_FILE = ALLOWED_WORK_DIR / ".agent_todos.json"


def todo_update(todos: list) -> dict:
    """
    AIエージェントのタスクリストを更新します。
    タスク開始時・各ステップ完了時・全体完了時に呼び出してください。

    todos の各要素:
      - content: タスクの説明（命令形）例: "server.py を編集する"
      - status: "pending" | "in_progress" | "completed" | "failed"
    """
    try:
        # バリデーション
        valid_statuses = {"pending", "in_progress", "completed", "failed"}
        for i, t in enumerate(todos):
            if not isinstance(t, dict):
                return {"error": f"todos[{i}] はオブジェクトである必要があります"}
            if "content" not in t:
                return {"error": f"todos[{i}] に content が必要です"}
            if t.get("status", "pending") not in valid_statuses:
                return {"error": f"todos[{i}].status は pending/in_progress/completed/failed のいずれかである必要があります"}

        # status のデフォルト補完
        normalized = [
            {"content": t["content"], "status": t.get("status", "pending")}
            for t in todos
        ]

        _TODO_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TODO_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2))

        counts = {s: sum(1 for t in normalized if t["status"] == s) for s in valid_statuses}
        return {
            "updated": True,
            "total": len(normalized),
            "pending": counts["pending"],
            "in_progress": counts["in_progress"],
            "completed": counts["completed"],
            "failed": counts["failed"],
            "todos": normalized,
        }
    except Exception as e:
        return {"error": f"todo_update エラー: {e}"}


def todo_read() -> dict:
    """現在のタスクリストを読み取ります。"""
    try:
        if not _TODO_FILE.exists():
            return {"todos": [], "message": "タスクリストはまだ作成されていません"}
        data = json.loads(_TODO_FILE.read_text(encoding="utf-8"))
        counts = {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0}
        for t in data:
            s = t.get("status", "pending")
            if s in counts:
                counts[s] += 1
        return {
            "todos": data,
            "total": len(data),
            **counts,
        }
    except Exception as e:
        return {"error": f"todo_read エラー: {e}"}
