"""
マルチエージェントシステム
- dispatch_task(): ディスパッチャーLLM → JSONタスク計画
- run_sub_agent(): 役割別agenticループ（ツールあり）
- generate_final_report(): 全成果物を集約して最終レポート生成
"""
import json
import time
import uuid
from pathlib import Path
from typing import Callable, Awaitable

import config

# 役割ごとに使えるツール名のリスト
AGENT_ALLOWED_TOOLS: dict[str, list[str]] = {
    "research":  ["web_search", "web_fetch", "web_research", "write_file", "read_file"],
    "design":    ["read_file", "write_file", "list_files", "glob_files"],
    "coding":    ["read_file", "write_file", "edit_file", "list_files", "glob_files", "grep", "run_command", "code_lint"],
    "infra":     ["read_file", "write_file", "run_command", "list_files"],
    "debug":     ["read_file", "write_file", "run_command", "list_files", "glob_files"],
    "security":  ["read_file", "list_files", "glob_files", "grep", "write_file"],
    "docs":      ["read_file", "write_file", "list_files"],
}


def _filter_tools(all_tools: list[dict], allowed_names: list[str]) -> list[dict]:
    """TOOLS リストから許可された名前のものだけ返す"""
    return [t for t in all_tools if t["function"]["name"] in allowed_names]


async def dispatch_task(
    user_message: str,
    async_client,
    model: str,
    job_dir: Path,
) -> dict:
    """
    ディスパッチャーLLMを呼んでJSONタスク計画を生成する。
    task.md に保存して返す。
    各タスクに preset_id / model を含めることでディスパッチャーがプロバイダーを上書きできる。
    """
    from prompts import get_agent_system_prompt

    response = await async_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": get_agent_system_prompt("dispatcher", str(job_dir))},
            {"role": "user",   "content": user_message},
        ],
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or "{}"
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        plan = {"roles": ["design", "coding", "debug"], "tasks": {}}

    # task.md に保存
    task_md = f"# タスク計画\n\n## 役割\n{', '.join(plan.get('roles', []))}\n\n"
    for role, task in plan.get("tasks", {}).items():
        task_md += f"## {role}\n{task.get('prompt', '')}\n\n"
    (job_dir / "task.md").write_text(task_md, encoding="utf-8")

    return plan


async def run_sub_agent(
    role: str,
    system_prompt: str,
    task_prompt: str,
    all_tools: list[dict],
    execute_tool_fn: Callable[[str, dict], Awaitable[str]],
    async_client,
    model: str,
    job_dir: Path,
    max_iterations: int | None = None,
    timeout_sec: int | None = None,
) -> str:
    """
    役割別agenticループ。
    完了またはタイムアウト時に status.md を更新して最終テキストを返す。
    """
    if max_iterations is None:
        max_iterations = config.MULTI_AGENT_MAX_ITERATIONS
    if timeout_sec is None:
        timeout_sec = config.MULTI_AGENT_TIMEOUT_SEC

    allowed_tools = _filter_tools(all_tools, AGENT_ALLOWED_TOOLS.get(role, []))

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": task_prompt},
    ]

    start_time = time.time()
    iteration = 0
    final_text = ""

    while iteration < max_iterations:
        elapsed = time.time() - start_time
        if elapsed > timeout_sec and iteration > 0:
            # タイムアウト: 現時点の成果提出を要求
            messages.append({
                "role": "user",
                "content": "時間制限に達しました。現時点での成果を status.md に記録して終了してください。",
            })

        iteration += 1
        create_kwargs: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if allowed_tools:
            create_kwargs["tools"] = allowed_tools

        response = await async_client.chat.completions.create(**create_kwargs)
        msg = response.choices[0].message

        # メッセージをそのまま追加（model_dump は openai v1 以降で使用可）
        messages.append(msg.model_dump(exclude_unset=True))

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = await execute_tool_fn(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })
        else:
            # ツール呼び出しなし = 完了
            final_text = msg.content or ""
            break

    # status.md に完了を記録
    _update_status(job_dir, role, "完了")
    return final_text


async def generate_final_report(
    async_client,
    model: str,
    job_dir: Path,
    out_dir: Path | None = None,
    user_request: str = "",
) -> str:
    """全成果物ファイルを読んで最終レポートを生成する。
    job_dir を成果物の探索元、out_dir を final-report.md の書き出し先にする
    （スコープ直下を成果物にしつつ制御ファイルは別ディレクトリに置く構成のため）。
    制御用ディレクトリ（.team / jobs 等）は探索から除外する。"""
    files_content = ""
    for path in sorted(job_dir.rglob("*.md")):
        if path.name == "final-report.md":
            continue
        rel = path.relative_to(job_dir)
        if any(seg in (".agent-jobs", ".team", "jobs", "__pycache__", ".git") for seg in rel.parts):
            continue
        try:
            files_content += f"\n\n## {rel}\n{path.read_text(encoding='utf-8')}"
        except Exception:
            pass

    if not files_content:
        return "成果物ファイルが見つかりませんでした。"

    sys_msg = (
        "あなたはプロジェクト完了報告書を書く専門家です。各エージェントの成果物を読んで、"
        "ユーザー向けに分かりやすい最終報告書を日本語でまとめてください。"
    )
    if user_request.strip():
        sys_msg += (
            "\n報告書は必ず**ユーザーの要望を起点**に書くこと。冒頭に必ず「## ご要望への対応」セクションを置き、"
            "要望を1項目ずつ箇条書きにし、各項目について『対応した/できていない/確認できない』を明示し、"
            "対応した場合は**どのファイルのどこを・どう変えて**満たしたかを具体的に書く。"
            "成果物から要望が満たされたと確認できない場合は、推測で『対応済み』と書かず正直に書くこと（嘘をつかない）。"
        )
        user_msg = (
            f"# ユーザーの要望（これにどう応えたかを最優先で報告すること）\n{user_request}\n\n"
            f"# 成果物\n{files_content}"
        )
    else:
        user_msg = f"以下の成果物をもとに最終報告書を書いてください:\n{files_content}"

    response = await async_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
        stream=False,
    )

    report = response.choices[0].message.content or "最終報告書の生成に失敗しました。"
    ((out_dir or job_dir) / "final-report.md").write_text(report, encoding="utf-8")
    return report


def new_job_id() -> str:
    return str(uuid.uuid4())[:8]


def _update_status(job_dir: Path, role: str, status: str) -> None:
    status_path = job_dir / "status.md"
    lines = []
    if status_path.exists():
        lines = status_path.read_text(encoding="utf-8").splitlines()

    # 既存エントリを更新、なければ追加
    prefix = f"- {role}:"
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{prefix} {status}"
            updated = True
            break
    if not updated:
        if not lines:
            lines.append("# ステータス\n")
        lines.append(f"{prefix} {status}")

    status_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
