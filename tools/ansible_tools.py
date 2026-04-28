import json
import os
import re
import subprocess
from pathlib import Path
from config import ALLOWED_WORK_DIR, COMMAND_TIMEOUT_SECONDS
from tools.command_tools import _truncate_output

CREDS_FILE = ALLOWED_WORK_DIR / ".azure_creds"


def _parse_creds(path: Path) -> dict:
    """KEY=VALUE 形式のファイルを解析して dict を返す。
    export KEY=VALUE、コメント行(#)、空行を処理する。
    """
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # export KEY=VALUE 形式を許容
        line = re.sub(r"^export\s+", "", line)
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # クォートを除去
        value = value.strip().strip('"').strip("'")
        if key:
            env[key] = value
    return env


def list_ansible_playbooks() -> str:
    """workspace 以下の .yml ファイルを再帰的に列挙してディレクトリ別にグループ化する。
    結果は ansible_chooser SSE イベントとして UI に送信される。
    このツールを呼び出した後は必ずターンを終了し、ユーザーの選択を待つこと。
    """
    playbooks = []
    for yml in sorted(ALLOWED_WORK_DIR.rglob("*.yml")):
        rel = yml.relative_to(ALLOWED_WORK_DIR)
        playbooks.append(str(rel))

    creds = _parse_creds(CREDS_FILE)
    creds_filled = bool(creds.get("AZURE_SUBSCRIPTION_ID") and creds.get("AZURE_CLIENT_ID") and creds.get("AZURE_SECRET") and creds.get("AZURE_TENANT"))
    return {
        "playbooks": playbooks,
        "creds_file": str(CREDS_FILE.relative_to(ALLOWED_WORK_DIR)),
        "creds_exists": CREDS_FILE.exists(),
        "creds_filled": creds_filled,
        "_ui_event": "ansible_chooser",
    }


def run_ansible_playbook(playbook: str) -> str:
    """指定したプレイブックを ansible-playbook で実行する。
    workspace/.azure_creds から環境変数を自動ロードする。
    playbook は workspace 相対パスで指定する (例: myproject/site.yml)。
    """
    # プレイブックパスを workspace 内に限定
    pb_path = (ALLOWED_WORK_DIR / playbook).resolve()
    if not str(pb_path).startswith(str(ALLOWED_WORK_DIR)):
        return json.dumps({"error": "workspace 外のプレイブックは実行できません"})
    if not pb_path.exists():
        return json.dumps({"error": f"プレイブックが見つかりません: {playbook}"})

    # クレデンシャル読み込み
    creds = _parse_creds(CREDS_FILE)
    if not creds:
        return json.dumps({
            "error": f"クレデンシャルファイルが見つからないか空です: {CREDS_FILE.relative_to(ALLOWED_WORK_DIR)}\n"
                     f"workspace/.azure_creds に KEY=VALUE 形式で記述してください。"
        })

    merged_env = {**os.environ, **creds}

    try:
        result = subprocess.run(
            ["ansible-playbook", str(pb_path)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(pb_path.parent),
            env=merged_env,
        )
        return json.dumps({
            "playbook": playbook,
            "returncode": result.returncode,
            "stdout": _truncate_output(result.stdout),
            "stderr": _truncate_output(result.stderr, 4000),
            "error": None,
        }, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "300秒のタイムアウトを超えました"})
    except FileNotFoundError:
        return json.dumps({"error": "ansible-playbook コマンドが見つかりません"})
    except Exception as e:
        return json.dumps({"error": f"実行エラー: {e}"})
