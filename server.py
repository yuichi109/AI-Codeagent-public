import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from openai import AzureOpenAI

from config import AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION
from prompts import SYSTEM_PROMPT
from tools.file_tools import read_file, write_file, list_files
from tools.command_tools import run_command
from tools.web_tools import web_search, web_fetch
from tools.code_tools import code_lint
from pydantic import BaseModel

client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY,
    api_version=AZURE_OPENAI_API_VERSION,
)

app = FastAPI()

TOOL_REGISTRY = {
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    "run_command": run_command,
    "web_search": web_search,
    "web_fetch": web_fetch,
    "code_lint": code_lint,
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


# サーバー側の安全ネット: クライアントが多く送ってきても最新20件に制限
MAX_HISTORY_MESSAGES = 20


def agent_stream(user_message: str, history: list):
    trimmed = history[-MAX_HISTORY_MESSAGES:] if len(history) > MAX_HISTORY_MESSAGES else history
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + trimmed + [{"role": "user", "content": user_message}]

    while True:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            yield f"data: {json.dumps({'type': 'answer', 'content': msg.content})}\n\n"
            break

        messages.append(msg)
        for tool_call in msg.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            yield f"data: {json.dumps({'type': 'tool_start', 'name': name, 'args': args})}\n\n"

            result = execute_tool(name, args)

            yield f"data: {json.dumps({'type': 'tool_result', 'result': result})}\n\n"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })


@app.post("/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(
        agent_stream(req.message, req.history),
        media_type="text/event-stream",
    )


@app.get("/")
async def index():
    return FileResponse("index.html")
