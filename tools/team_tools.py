"""
協調型マルチエージェント（チーム方式）の土台。
docs/multi-agent-team-design.md / [[project-multi-agent-team]] 参照。

パイプライン方式（tools/multi_agent_tools.py）は無改修で温存し、本モジュールで
「並列＋共有タスクリスト（self-claim/ロック/依存）＋mailbox（直接通信）」を提供する。

設計上のキモ:
- 新ツール（send_message/read_messages/claim_task/complete_task/list_tasks）は
  「呼び出し元 teammate 名」と「job_dir」を知らないと動かない**コンテキスト依存ツール**。
  グローバル登録（server.py/agent_core.py の TOOL_REGISTRY）はせず、teammate ごとに
  job_dir＋名前をクロージャで束ねた executor を作り run ループへ渡す（“専用便箋方式”）。
  → 同時並列で取り違えが起きない。チーム方式は本モジュール内で自己完結し
    [[project-dual-tool-registry]] の対象外（BG/定時から呼ばれない）。
"""
import json
import os
import time
from pathlib import Path
from typing import Awaitable, Callable

import config

# 役割 → teammate 名（mailbox の宛先・team.json のメンバー名に使う）
TEAM_MEMBER_NAMES: dict[str, str] = {
    "research": "researcher",
    "design":   "designer",
    "coding":   "coder",
    "infra":    "infra",
    "debug":    "debugger",
    "security": "security",
    "docs":     "docs",
}


# ----------------------------------------------------------------------------
# ファイルロック（同時 claim の競合防止）
# ----------------------------------------------------------------------------
def _acquire_lock(lock_path: Path, timeout: float = 10.0) -> int:
    """O_CREAT|O_EXCL でロックファイルを作る。取れるまで短くリトライ。"""
    deadline = time.time() + timeout
    while True:
        try:
            return os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() > deadline:
                # デッドロック回避: 古いロックを強制解放
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            time.sleep(0.05)


def _release_lock(fd: int, lock_path: Path) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        lock_path.unlink()
    except OSError:
        pass


# ----------------------------------------------------------------------------
# tasks.json（共有タスクリスト）
# ----------------------------------------------------------------------------
def _tasks_path(job_dir: Path) -> Path:
    return job_dir / "tasks.json"


def _read_tasks(job_dir: Path) -> list[dict]:
    p = _tasks_path(job_dir)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _write_tasks(job_dir: Path, tasks: list[dict]) -> None:
    _tasks_path(job_dir).write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def init_team_job(plan: dict, job_dir: Path) -> dict:
    """plan.json から tasks.json / team.json / mailbox ディレクトリを初期化する。"""
    tasks_in = plan.get("tasks", {})
    tasks: list[dict] = []
    for tid, t in tasks_in.items():
        tasks.append({
            "id": tid,
            "role": t.get("role", ""),
            "prompt": t.get("prompt", ""),
            "depends_on": t.get("depends_on", []),
            "files": t.get("files", []),
            "timeout_sec": t.get("timeout_sec"),
            "preset_id": t.get("preset_id"),
            "model": t.get("model"),
            "state": "pending",   # pending | in_progress | completed
            "owner": None,
            "summary": "",
        })
    _write_tasks(job_dir, tasks)

    # メンバー名簿（役割の集合から1役割1名）
    roles = []
    for t in tasks:
        if t["role"] and t["role"] not in roles:
            roles.append(t["role"])
    members = [{"name": TEAM_MEMBER_NAMES.get(r, r), "role": r} for r in roles]
    team = {"members": members, "max_parallel": plan.get("max_parallel", config.MULTI_AGENT_MAX_PARALLEL)}
    (job_dir / "team.json").write_text(json.dumps(team, ensure_ascii=False, indent=2), encoding="utf-8")

    (job_dir / "mailbox").mkdir(parents=True, exist_ok=True)
    return team


def _deps_satisfied(task: dict, by_id: dict[str, dict]) -> bool:
    return all(by_id.get(d, {}).get("state") == "completed" for d in task.get("depends_on", []))


def claim_task(job_dir: Path, member_name: str, role: str | None = None, task_id: str | None = None) -> dict:
    """次に実行可能なタスクを self-claim する（ロック下）。
    role=None なら役割を問わず取る（ワーカープール方式）。role 指定時はその役割のみ。
    返り値: 取れたタスク or {"none": True, "pending_remain": bool}
    """
    lock_path = _tasks_path(job_dir).with_suffix(".json.lock")
    fd = _acquire_lock(lock_path)
    try:
        tasks = _read_tasks(job_dir)
        by_id = {t["id"]: t for t in tasks}

        def _role_ok(t: dict) -> bool:
            return role is None or t.get("role") == role

        candidates = [
            t for t in tasks
            if t["state"] == "pending" and _role_ok(t) and _deps_satisfied(t, by_id)
        ]
        if task_id:
            candidates = [t for t in candidates if t["id"] == task_id]
        if not candidates:
            pending_remain = any(t["state"] == "pending" and _role_ok(t) for t in tasks)
            return {"none": True, "pending_remain": pending_remain}
        task = candidates[0]
        task["state"] = "in_progress"
        task["owner"] = member_name
        _write_tasks(job_dir, tasks)
        return dict(task)
    finally:
        _release_lock(fd, lock_path)


def verify_task_files(job_dir: Path, task: dict) -> list[str]:
    """タスクが宣言した files のうち、実際に存在しないものを返す（リードの検収用）。"""
    missing: list[str] = []
    for f in task.get("files", []):
        p = Path(f)
        if not p.is_absolute():
            p = job_dir / f
        if not p.exists():
            missing.append(f)
    return missing


def complete_task(job_dir: Path, task_id: str, summary: str = "") -> dict:
    lock_path = _tasks_path(job_dir).with_suffix(".json.lock")
    fd = _acquire_lock(lock_path)
    try:
        tasks = _read_tasks(job_dir)
        for t in tasks:
            if t["id"] == task_id:
                t["state"] = "completed"
                t["summary"] = summary
                _write_tasks(job_dir, tasks)
                return {"ok": True, "task_id": task_id}
        return {"ok": False, "error": f"タスク {task_id} が見つかりません"}
    finally:
        _release_lock(fd, lock_path)


def list_tasks(job_dir: Path) -> list[dict]:
    return [
        {"id": t["id"], "role": t.get("role"), "state": t["state"],
         "owner": t.get("owner"), "depends_on": t.get("depends_on", []),
         "summary": t.get("summary", "")}
        for t in _read_tasks(job_dir)
    ]


# ----------------------------------------------------------------------------
# mailbox（teammate 間メッセージング・宛先別 jsonl・既読オフセット）
# ----------------------------------------------------------------------------
def _mailbox_dir(job_dir: Path) -> Path:
    d = job_dir / "mailbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _count_messages(job_dir: Path) -> int:
    total = 0
    for f in _mailbox_dir(job_dir).glob("*.jsonl"):
        try:
            total += sum(1 for _ in f.open(encoding="utf-8"))
        except OSError:
            pass
    return total


def send_message(job_dir: Path, sender: str, to: str, subject: str, body: str) -> dict:
    """宛先 teammate の受信箱（mailbox/{to}.jsonl）に1行追記する。"""
    if not to:
        return {"ok": False, "error": "宛先(to)が空です"}
    if _count_messages(job_dir) >= config.MULTI_AGENT_MAX_MESSAGES:
        return {"ok": False, "error": f"このジョブのメッセージ総数が上限({config.MULTI_AGENT_MAX_MESSAGES})に達しました。これ以上の送信は控えてください。"}
    msg = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "from": sender, "to": to,
        "subject": subject or "", "body": body or "",
    }
    box = _mailbox_dir(job_dir) / f"{to}.jsonl"
    with box.open("a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return {"ok": True}


def read_messages(job_dir: Path, member_name: str) -> list[dict]:
    """自分宛の未読メッセージのみ返し、既読オフセットを更新する。"""
    box = _mailbox_dir(job_dir) / f"{member_name}.jsonl"
    if not box.exists():
        return []
    off_file = _mailbox_dir(job_dir) / f"{member_name}.offset"
    try:
        offset = int(off_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        offset = 0
    lines = box.read_text(encoding="utf-8").splitlines()
    unread = lines[offset:]
    out: list[dict] = []
    for ln in unread:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    off_file.write_text(str(len(lines)), encoding="utf-8")
    return out


# ----------------------------------------------------------------------------
# teammate 用ツール（クロージャで job_dir＋名前を束ねる＝“専用便箋方式”）
# ----------------------------------------------------------------------------
TEAM_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "チームの他メンバーにメッセージを送る。宛先名は team.json のメンバー名（例: designer/coder/debugger）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "宛先メンバー名"},
                    "subject": {"type": "string", "description": "件名（短く）"},
                    "body": {"type": "string", "description": "本文"},
                },
                "required": ["to", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_messages",
            "description": "自分宛の未読メッセージを取得する（引数なし）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "チームの共有タスクリストの現状を見る（引数なし）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def make_team_executor(
    base_executor: Callable[[str, dict], Awaitable[str]],
    job_dir: Path,
    member_name: str,
) -> Callable[[str, dict], Awaitable[str]]:
    """teammate 専用の executor。チームツールは job_dir＋自分の名前を埋めて処理し、
    それ以外は既存の execute_tool_async に委譲する。"""
    # teammate は job_dir を作業ディレクトリとみなす。相対パスは job_dir 基準に正す
    # （絶対パスはそのまま）。これで「code/x.py」が workspace ルートに落ちる事故を防ぐ。
    _PATH_KEYS = ("path", "file_path", "work_dir")

    def _rewrite_paths(args: dict) -> dict:
        out = dict(args)
        for k in _PATH_KEYS:
            v = out.get(k)
            if isinstance(v, str) and v and not os.path.isabs(v):
                out[k] = str(job_dir / v)
        return out

    async def _exec(name: str, args: dict) -> str:
        if name == "send_message":
            return json.dumps(
                send_message(job_dir, member_name, args.get("to", ""), args.get("subject", ""), args.get("body", "")),
                ensure_ascii=False,
            )
        if name == "read_messages":
            return json.dumps(read_messages(job_dir, member_name), ensure_ascii=False)
        if name == "list_tasks":
            return json.dumps(list_tasks(job_dir), ensure_ascii=False)
        return await base_executor(name, _rewrite_paths(args))
    return _exec


# ----------------------------------------------------------------------------
# teammate のエージェントループ（mailbox 自動読込みを最初から組み込む）
# ----------------------------------------------------------------------------
async def run_team_member(
    member_name: str,
    role: str,
    system_prompt: str,
    task_prompt: str,
    all_tools: list[dict],
    base_executor: Callable[[str, dict], Awaitable[str]],
    async_client,
    model: str,
    job_dir: Path,
    max_iterations: int | None = None,
    timeout_sec: int | None = None,
) -> str:
    """1タスクを実行する teammate のループ。ターン冒頭で自分宛 mailbox を機械的に注入する
    （弱いモデルが read_messages を呼び忘れても会話に取り込まれる）。"""
    from tools.multi_agent_tools import AGENT_ALLOWED_TOOLS, _filter_tools

    if max_iterations is None:
        max_iterations = config.MULTI_AGENT_MAX_ITERATIONS
    if timeout_sec is None:
        timeout_sec = config.MULTI_AGENT_TIMEOUT_SEC

    allowed = _filter_tools(all_tools, AGENT_ALLOWED_TOOLS.get(role, [])) + TEAM_TOOL_SCHEMAS
    executor = make_team_executor(base_executor, job_dir, member_name)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": task_prompt},
    ]

    start_time = time.time()
    iteration = 0
    final_text = ""
    used_any_tool = False
    nudged = False

    while iteration < max_iterations:
        # ターン冒頭: 自分宛の未読を機械的に注入（mailbox 自動配信の代替）
        unread = read_messages(job_dir, member_name)
        if unread:
            lines = [f"- {m.get('from')}「{m.get('subject','')}」: {m.get('body','')}" for m in unread]
            messages.append({
                "role": "user",
                "content": "📬 チームメンバーからの新着メッセージ:\n" + "\n".join(lines)
                + "\n（必要なら send_message で返信し、自分のタスクを続行してください）",
            })

        elapsed = time.time() - start_time
        if elapsed > timeout_sec and iteration > 0:
            messages.append({
                "role": "user",
                "content": "時間制限に達しました。現時点の成果をファイルに保存し、complete_task せず終了してよいので作業を締めてください。",
            })

        iteration += 1
        create_kwargs: dict = {"model": model, "messages": messages, "stream": False}
        if allowed:
            create_kwargs["tools"] = allowed

        response = await async_client.chat.completions.create(**create_kwargs)
        msg = response.choices[0].message
        messages.append(msg.model_dump(exclude_unset=True))

        if msg.tool_calls:
            used_any_tool = True
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = await executor(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })
        else:
            final_text = msg.content or ""
            # 弱いモデル対策: 一度もツールを使わずテキストだけで終わろうとしたら1回催促する
            # （「やります」と言うだけで実作業をしない典型パターンを救済）
            if not used_any_tool and not nudged and iteration < max_iterations:
                nudged = True
                messages.append({
                    "role": "user",
                    "content": "テキストの返答だけでは作業は完了になりません。"
                    "担当タスクを実際に遂行するため、必要なツール（write_file / run_command 等）を呼び出して成果をファイルに書き出してください。",
                })
                continue
            break

    return final_text


async def dispatch_team_task(user_message: str, async_client, model: str, job_dir: Path) -> dict:
    """チーム方式用のディスパッチャー。depends_on / files / role を持つ plan.json を生成する。"""
    from prompts import get_agent_system_prompt

    response = await async_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": get_agent_system_prompt("dispatcher_team", str(job_dir))},
            {"role": "user",   "content": user_message},
        ],
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or "{}"
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        plan = {"tasks": {}}
    plan["mode"] = "team"
    return plan
