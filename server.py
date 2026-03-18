import json
import subprocess
import requests
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from openai import AzureOpenAI, OpenAI

from config import AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION, SEARXNG_ENABLED, GITLAB_PAT
from prompts import SYSTEM_PROMPT
from tools.file_tools import read_file, write_file, edit_file, list_files, glob_files, grep
from tools.command_tools import run_command
from tools.web_tools import web_search, web_fetch, web_research
from tools.code_tools import code_lint
from tools.todo_tools import todo_update, todo_read
from pydantic import BaseModel

# デフォルトのプロバイダー設定（.env のAzure設定）
_default_provider_config = {
    "type": "azure",
    "url": AZURE_OPENAI_ENDPOINT,
    "api_key": AZURE_OPENAI_API_KEY,
    "model": AZURE_OPENAI_DEPLOYMENT,
    "api_version": AZURE_OPENAI_API_VERSION,
}
# 現在アクティブなプロバイダー設定（ブラウザから変更可能）
_PROVIDER_CONFIG_FILE = Path(__file__).parent / ".provider_config.json"

def _load_provider_config():
    """起動時にファイルから設定を読み込む（なければデフォルト）"""
    if _PROVIDER_CONFIG_FILE.exists():
        try:
            saved = json.loads(_PROVIDER_CONFIG_FILE.read_text())
            # 必須キーが揃っているか確認
            if all(k in saved for k in ("type", "url", "api_key", "model", "api_version")):
                print(f"[provider] loaded from file: {saved['type']} / {saved['model']}")
                return saved
        except Exception:
            pass
    return dict(_default_provider_config)

def _save_provider_config(cfg: dict):
    """設定をファイルに保存（reload後も維持）"""
    try:
        _PROVIDER_CONFIG_FILE.write_text(json.dumps(cfg))
    except Exception as e:
        print(f"[provider] failed to save config: {e}")

_provider_config = _load_provider_config()


def _make_client():
    """現在の _provider_config に基づいてLLMクライアントを生成する"""
    if _provider_config["type"] == "azure":
        return AzureOpenAI(
            azure_endpoint=_provider_config["url"],
            api_key=_provider_config["api_key"],
            api_version=_provider_config["api_version"],
        )
    else:
        import httpx
        return OpenAI(
            base_url=_provider_config["url"].rstrip("/") + "/v1",
            api_key=_provider_config["api_key"] or "dummy",
            http_client=httpx.Client(trust_env=False),  # 社内プロキシをバイパス
        )

@asynccontextmanager
async def lifespan(app: FastAPI):
    # SearXNG を自動起動
    if SEARXNG_ENABLED:
        compose_file = Path(__file__).parent / "docker-compose.searxng.yml"
        if compose_file.exists():
            subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "up", "-d"],
                capture_output=True,
            )
    yield


app = FastAPI(lifespan=lifespan)

TOOL_REGISTRY = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_files": list_files,
    "glob_files": glob_files,
    "grep": grep,
    "run_command": run_command,
    "web_search": web_search,
    "web_fetch": web_fetch,
    "web_research": web_research,
    "code_lint": code_lint,
    "todo_update": todo_update,
    "todo_read": todo_read,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "ファイルの内容を読み取ります",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "読み取るファイルのパス (作業ディレクトリ相対)"},
                    "encoding": {"type": "string", "description": "文字エンコーディング (デフォルト: utf-8)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "ファイルにコンテンツを書き込みます",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "書き込むファイルパス"},
                    "content": {"type": "string", "description": "書き込む内容"},
                    "mode": {"type": "string", "enum": ["overwrite", "append"], "description": "書き込みモード"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "ファイル内の特定文字列を別の文字列に置換します。write_file より安全で効率的です。old_str は一意になるよう周辺の行を含めてください。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "編集するファイルのパス (作業ディレクトリ相対)"},
                    "old_str": {"type": "string", "description": "置換前の文字列 (ファイル内で一意になるよう十分な文脈を含めること)"},
                    "new_str": {"type": "string", "description": "置換後の文字列"},
                    "expected_replacements": {"type": "integer", "description": "置換が発生すべき回数 (デフォルト: 1)"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "ディレクトリ内のファイル一覧を取得します",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "ディレクトリパス (デフォルト: .)"},
                    "pattern": {"type": "string", "description": "globパターン (例: **/*.py)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "ホワイトリストに含まれる安全なコマンドを実行します",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "実行するコマンド (例: python script.py)"},
                    "work_dir": {"type": "string", "description": "作業ディレクトリ (省略可)"},
                    "description": {"type": "string", "description": "この実行の目的を日本語で一言説明 (例: GitLabへプッシュ、依存パッケージをインストール)"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "DuckDuckGoを使ってWebを検索します",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "検索クエリ"},
                    "max_results": {"type": "integer", "description": "最大結果数 (デフォルト: 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "指定URLのWebページのテキストコンテンツを取得します",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "取得するURL"},
                    "extract_text": {"type": "boolean", "description": "テキストのみ抽出するか"},
                    "max_chars": {"type": "integer", "description": "最大文字数 (デフォルト: 8000)"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_research",
            "description": "検索→上位ページを自動取得→まとめて返す高レベル調査ツール。複数ソースを比較して提案したいときに使う。web_searchより詳細な情報が得られる。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "調査クエリ"},
                    "max_sources": {"type": "integer", "description": "取得するソース数 (デフォルト: 3、最大: 5)"},
                    "max_chars_per_page": {"type": "integer", "description": "1ページあたりの最大文字数 (デフォルト: 3000)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_files",
            "description": "glob パターンでファイルパスを検索します。** を使うと再帰検索できます。例: **/*.py でPythonファイル全件取得。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob パターン (例: **/*.py, src/**/*.ts)"},
                    "path": {"type": "string", "description": "検索ベースディレクトリ (デフォルト: .)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "ファイル内容を正規表現で検索し、マッチした行をファイルパス・行番号付きで返します。関数の使用箇所やキーワード検索に使います。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "検索する正規表現パターン (例: def main, import os)"},
                    "path": {"type": "string", "description": "検索ベースディレクトリ (デフォルト: .)"},
                    "file_pattern": {"type": "string", "description": "対象ファイルのglobパターン (デフォルト: **/*、例: **/*.py)"},
                    "case_sensitive": {"type": "boolean", "description": "大文字小文字を区別するか (デフォルト: true)"},
                    "max_results": {"type": "integer", "description": "最大結果数 (デフォルト: 100)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_lint",
            "description": "コードの静的解析を実行してエラーや警告を検出します",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "lintするファイルパス"},
                    "code": {"type": "string", "description": "直接コードを渡す場合 (file_pathと排他)"},
                    "language": {"type": "string", "enum": ["python", "javascript", "typescript"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_update",
            "description": "作業タスクリストを作成・更新します。複数ステップの作業開始時にリストを作り、各ステップ完了時に status を更新してください。UIにリアルタイム表示されます。",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "タスクの配列",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "タスクの説明（命令形）例: 'server.py を編集する'"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"], "description": "タスクの状態"},
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_read",
            "description": "現在のタスクリストを読み取ります。作業の続きを再開する際や、残タスクを確認する際に使います。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


def execute_tool(name: str, arguments: dict) -> str:
    if name not in TOOL_REGISTRY:
        return json.dumps({"error": f"未知のツール: {name}"}, ensure_ascii=False)
    try:
        result = TOOL_REGISTRY[name](**arguments)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"ツール実行エラー: {type(e).__name__}: {e}"}, ensure_ascii=False)


class ChatRequest(BaseModel):
    message: str
    history: list = []
    images: list = []  # base64 画像リスト [{data: "base64...", mime: "image/png"}, ...]


# サーバー側の安全ネット: クライアントが多く送ってきても最新20件に制限
MAX_HISTORY_MESSAGES = 20
# ローリングサマリーの設定
SUMMARY_TRIGGER = 16   # 履歴がこの件数を超えたら圧縮
SUMMARY_KEEP_RECENT = 4  # 圧縮後に詳細を残す直近のメッセージ数


def _summarize_history(messages: list) -> str | None:
    """
    古い履歴メッセージをLLMで要約して文字列で返す。
    失敗時は None を返す（呼び出し元でフォールバック）。
    """
    # tool/tool_calls メッセージはテキストに変換して要約に含める
    lines = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""
        if role == "user":
            lines.append(f"ユーザー: {content}")
        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                names = ", ".join(
                    tc["function"]["name"] if isinstance(tc, dict) else tc.function.name
                    for tc in tool_calls
                )
                lines.append(f"アシスタント: [ツール呼び出し: {names}] {content or ''}")
            else:
                lines.append(f"アシスタント: {content}")
        elif role == "tool":
            # ツール結果は先頭100文字だけ含める
            lines.append(f"ツール結果: {str(content)[:100]}...")
    conversation_text = "\n".join(lines)
    try:
        client = _make_client()
        resp = client.chat.completions.create(
            model=_provider_config["model"],
            messages=[
                {"role": "system", "content": "あなたは会話履歴を簡潔に要約するアシスタントです。"},
                {"role": "user", "content":
                    f"以下の会話履歴を、重要な決定事項・実装済みの内容・現在の状態を中心に"
                    f"箇条書きで日本語200字以内にまとめてください。\n\n{conversation_text}"},
            ],
            stream=False,
        )
        return resp.choices[0].message.content
    except Exception:
        return None


def agent_stream(user_message: str, history: list, images: list = None):
    try:
        yield from _agent_stream_inner(user_message, history, images or [])
    except Exception as e:
        import traceback
        err = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        yield f"data: {json.dumps({'type': 'answer_chunk', 'content': f'エラー: {type(e).__name__}: {e}'})}\n\n"
        yield f"data: {json.dumps({'type': 'answer_done'})}\n\n"
        print(err)  # uvicornログに出力


def _convert_messages_for_local(messages: list) -> list:
    """
    ローカルモデル（LM Studio等）向けにメッセージを変換する。
    - role:tool → role:user に変換（Jinja テンプレートが tool ロールを処理できないモデル対応）
    - assistant メッセージの tool_calls フィールドを除去し、テキスト表現に変換
      （履歴に tool_calls が残っていると "No user query found in messages." エラーになるため）
    """
    result = []
    for msg in messages:
        if msg["role"] == "tool":
            result.append({
                "role": "user",
                "content": f"[Tool Result: {msg.get('tool_call_id', '')}]\n{msg['content']}",
            })
        elif msg["role"] == "assistant" and msg.get("tool_calls"):
            # tool_calls を除去してテキスト表現に変換
            tool_names = ", ".join(
                tc["function"]["name"] if isinstance(tc, dict) else tc.function.name
                for tc in msg["tool_calls"]
            )
            existing_content = msg.get("content") or ""
            content = f"{existing_content}[ツール呼び出し: {tool_names}]".strip()
            result.append({"role": "assistant", "content": content})
        else:
            result.append(msg)
    return result


def _sanitize_history(history: list) -> list:
    """
    トリミング後に先頭に残った孤立 tool メッセージを除去する。
    tool メッセージは直前に tool_calls を持つ assistant メッセージがないと
    Azure OpenAI が 400 エラーを返すため。
    """
    # tool_call_id の集合を収集
    valid_tool_call_ids = set()
    for msg in history:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tid:
                    valid_tool_call_ids.add(tid)
    # 対応する tool_calls がない tool メッセージを除去
    return [
        msg for msg in history
        if not (msg.get("role") == "tool" and msg.get("tool_call_id") not in valid_tool_call_ids)
    ]


def _agent_stream_inner(user_message: str, history: list, images: list = None):
    trimmed = history[-MAX_HISTORY_MESSAGES:] if len(history) > MAX_HISTORY_MESSAGES else history
    trimmed = _sanitize_history(trimmed)

    # ローリングサマリー: 古い部分を圧縮して文脈を維持
    compressed_history = None
    if len(trimmed) > SUMMARY_TRIGGER:
        old_part = trimmed[:-SUMMARY_KEEP_RECENT]
        recent_part = trimmed[-SUMMARY_KEEP_RECENT:]
        # recent_part の先頭が tool メッセージだと孤立する（直前の assistant+tool_calls が
        # old_part に吸収されるため）。tool または assistant (tool_calls あり) が先頭に来る間は
        # old_part から1件ずつ recent_part に移して境界を安全な位置に調整する。
        def _recent_head_unsafe(msgs):
            if not msgs:
                return False
            head = msgs[0]
            # tool メッセージが先頭 → 直前の assistant+tool_calls が必要
            if head.get("role") == "tool":
                return True
            # assistant+tool_calls が先頭 → その tool メッセージが直後に来るはずなので問題ないが
            # tool_calls だけ残って tool メッセージが old_part 側に切れるケースを防ぐ
            if head.get("role") == "assistant" and head.get("tool_calls"):
                return True
            return False

        while _recent_head_unsafe(recent_part) and old_part:
            recent_part = [old_part[-1]] + recent_part
            old_part = old_part[:-1]
        summary = _summarize_history(old_part)
        if summary:
            compressed_history = [
                {"role": "user", "content": f"[これまでの作業サマリー]\n{summary}"},
                {"role": "assistant", "content": "了解しました。続けます。"},
            ] + recent_part
            trimmed = compressed_history
    # 画像がある場合は content をリスト形式（vision API）にする
    if images:
        user_content = [{"type": "text", "text": user_message}]
        for img in images:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img['mime']};base64,{img['data']}"},
            })
    else:
        user_content = user_message
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + trimmed + [{"role": "user", "content": user_content}]
    turn_messages = []  # このターンで追加されたメッセージ (tool関連)

    # サマリー圧縮が発生した場合はクライアントに通知（localStorage 更新のため）
    if compressed_history is not None:
        yield f"data: {json.dumps({'type': 'history_compressed', 'messages': compressed_history})}\n\n"

    is_local = _provider_config["type"] == "openai_compatible"

    while True:
        # ローカルモデルは role:tool を Jinja テンプレートで処理できない場合があるため変換
        send_messages = _convert_messages_for_local(messages) if is_local else messages
        # ローカルモデルは tools を渡さない
        # 理由: Qwen3等が壊れた tool_calls を返して暴走するため
        # Phase 2（ハイブリッドモード）で delegate_to_azure により構造的に解決予定
        create_kwargs = dict(model=_provider_config["model"], messages=send_messages, stream=True)
        if not is_local:
            create_kwargs["tools"] = TOOLS
            create_kwargs["tool_choice"] = "auto"
            create_kwargs["stream_options"] = {"include_usage": True}  # ローカルLLMは未対応のため除外
        stream = _make_client().chat.completions.create(**create_kwargs)

        content_parts = []
        tool_calls_map = {}  # index -> {id, name, arguments}

        for chunk in stream:
            # トークン使用量（最終chunk）
            if chunk.usage:
                yield f"data: {json.dumps({'type': 'token_usage', 'prompt': chunk.usage.prompt_tokens, 'completion': chunk.usage.completion_tokens, 'total': chunk.usage.total_tokens})}\n\n"
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # テキストチャンクをリアルタイム送信
            if delta.content:
                content_parts.append(delta.content)
                yield f"data: {json.dumps({'type': 'answer_chunk', 'content': delta.content})}\n\n"

            # ツール呼び出しデルタを蓄積
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        tool_calls_map[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_map[idx]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_map[idx]["arguments"] += tc_delta.function.arguments

        # ツール呼び出しなし → 最終回答ストリーム完了
        if not tool_calls_map:
            final_answer = "".join(content_parts)
            turn_messages.append({"role": "assistant", "content": final_answer})
            yield f"data: {json.dumps({'type': 'history_messages', 'messages': turn_messages})}\n\n"
            yield f"data: {json.dumps({'type': 'answer_done'})}\n\n"
            break

        # ツール呼び出しあり → アシスタントメッセージを履歴に追加してツール実行
        tool_calls_list = [
            {
                "id": tool_calls_map[idx]["id"],
                "type": "function",
                "function": {
                    "name": tool_calls_map[idx]["name"],
                    "arguments": tool_calls_map[idx]["arguments"],
                },
            }
            for idx in sorted(tool_calls_map.keys())
        ]
        assistant_msg = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "tool_calls": tool_calls_list,
        }
        messages.append(assistant_msg)
        turn_messages.append(assistant_msg)

        for tc in tool_calls_list:
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"])

            yield f"data: {json.dumps({'type': 'tool_start', 'name': name, 'args': args})}\n\n"

            result = execute_tool(name, args)

            yield f"data: {json.dumps({'type': 'tool_result', 'result': result})}\n\n"

            # todo_update の場合はUIにタスクリストを即時反映
            if name == "todo_update":
                try:
                    result_data = json.loads(result)
                    if "todos" in result_data:
                        yield f"data: {json.dumps({'type': 'todo_update', 'todos': result_data['todos']})}\n\n"
                except Exception:
                    pass

            tool_msg = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            }
            messages.append(tool_msg)
            turn_messages.append(tool_msg)


@app.post("/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(
        agent_stream(req.message, req.history, req.images),
        media_type="text/event-stream",
    )


@app.get("/gitlab/projects")
async def gitlab_projects():
    if not GITLAB_PAT:
        return JSONResponse({"error": "GITLAB_PAT が設定されていません"}, status_code=400)
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            resp = await client.get(
                "https://gitlab.com/api/v4/projects",
                headers={"PRIVATE-TOKEN": GITLAB_PAT},
                params={"membership": "true", "order_by": "last_activity_at", "per_page": 50},
            )
        resp.raise_for_status()
        projects = [
            {
                "id": p["id"],
                "name": p["name"],
                "path_with_namespace": p["path_with_namespace"],
                "web_url": p["web_url"],
                "description": p.get("description") or "",
                "last_activity_at": p.get("last_activity_at", ""),
                "visibility": p.get("visibility", ""),
            }
            for p in resp.json()
        ]
        return JSONResponse(projects)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


class ProviderConfigRequest(BaseModel):
    url: str = ""
    api_key: str = ""
    model: str = ""


@app.get("/providers/current")
async def providers_current():
    return JSONResponse({
        "type": _provider_config["type"],
        "url": _provider_config["url"],
        "model": _provider_config["model"],
    })


@app.get("/providers/models")
async def providers_models(url: str, api_key: str = ""):
    """指定URLの /v1/models を叩いてモデル一覧を返す"""
    try:
        # URLを正規化: 末尾の /v1 を除去してから /v1/models を付ける
        base = url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        models_url = base + "/v1/models"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = requests.get(models_url, headers=headers, timeout=5, proxies={"http": None, "https": None})
        resp.raise_for_status()
        data = resp.json()
        # OpenAI互換の {"data": [{"id": "..."}, ...]} 形式
        if "data" in data:
            models = [m["id"] for m in data["data"]]
        else:
            models = list(data.keys())
        return JSONResponse({"models": models})
    except Exception as e:
        return JSONResponse({"error": f"接続失敗: {e}"}, status_code=400)


@app.post("/providers/config")
async def providers_config(req: ProviderConfigRequest):
    """プロバイダー設定を更新する。urlが空の場合はAzureデフォルトに戻す"""
    global _provider_config
    if not req.url:
        # Azureデフォルトにリセット
        _provider_config = dict(_default_provider_config)
    else:
        provider_type = "azure" if ".openai.azure.com" in req.url else "openai_compatible"
        _provider_config = {
            "type": provider_type,
            "url": req.url,
            "api_key": req.api_key,
            "model": req.model,
            "api_version": _default_provider_config["api_version"],  # Azure用（openai_compatibleでは未使用）
        }
    _save_provider_config(_provider_config)
    return JSONResponse({"status": "ok", "provider": {
        "type": _provider_config["type"],
        "url": _provider_config["url"],
        "model": _provider_config["model"],
    }})


@app.get("/")
async def index():
    return FileResponse("index.html")
