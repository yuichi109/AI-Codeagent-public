import asyncio
import json
import shutil
import subprocess
import sys
import requests
from datetime import datetime
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, UploadFile, File as FastAPIFile
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import AzureOpenAI, OpenAI, AsyncAzureOpenAI, AsyncOpenAI

from config import AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENTS, SEARXNG_ENABLED, GITLAB_PAT, GITLAB_USER, ALLOWED_WORK_DIR, FOUNDRY_ENDPOINT, FOUNDRY_API_KEY, FOUNDRY_MODEL, FOUNDRY_MODELS, FOUNDRY_API_VERSION, FOUNDRY_INSTANCES, GEMINI_API_KEY, GEMINI_MODELS, OPENAI_API_KEY, OPENAI_MODEL, OPENAI_MODELS, RESPONSES_API_ENABLED, RESPONSES_API_MODEL, WEB_RESEARCH_PROVIDER, OBSIDIAN_VAULT_PATH, APP_VERSION, ASYNC_MAX_JOBS
from tools.async_job_db import init_db as _init_async_db, create_job as _create_async_job, get_job as _get_async_job, get_chunks as _get_async_chunks, list_jobs as _list_async_jobs, update_job as _update_async_job, delete_job as _delete_async_job
from tools.inbox_worker import start_worker, stop_worker, get_status as inbox_get_status, scan_inbox as inbox_scan_now, ensure_inbox_dirs, get_stale_drafts
from prompts import get_system_prompt

# Gemini デフォルトモデル一覧（GEMINI_MODELS 未設定時のフォールバック）
_GEMINI_DEFAULT_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

# OpenAI デフォルトモデル一覧（OPENAI_MODELS 未設定時のフォールバック）
_OPENAI_DEFAULT_MODELS = [
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-4.5",
    "gpt-4o",
    "gpt-4o-mini",
    "o3",
    "o4-mini",
]
from tools.file_tools import read_file, write_file, edit_file, list_files, glob_files, grep
from tools.command_tools import run_command, BLOCKED_COMMANDS, LONG_RUNNING_CMDS, _split_shell_chain, _truncate_output, _run_bash_sandboxed, _is_permission_error
from tools.web_tools import web_search, web_fetch, web_research
from tools.code_tools import code_lint
from tools.todo_tools import todo_update, todo_read
from tools.workspace_tools import protected_list_read, protected_list_update, protected_list_replace, workspace_cleanup_preview, workspace_backup, archive_workspace
from tools.manim_tools import render_manim
from tools.pdf_tools import read_pdf, write_pdf
from tools.office_tools import (
    read_docx, write_docx, edit_docx,
    read_xlsx, write_xlsx, edit_xlsx,
    read_pptx, write_pptx, edit_pptx,
)
from tools.ansible_tools import list_ansible_playbooks, run_ansible_playbook
from tools.windows_tools import run_powershell
from tools.winrm_tools import winrm_command
from tools.host_info_tools import gather_host_info
from tools.background_tools import run_background, check_background, kill_background
from tools.responses_tools import call_responses_api
from tools.rag_tools import rag_save, rag_search, rag_update_status, rag_list
# codebase_rag_tools は肥大化問題が未解決のため TOOL_REGISTRY から除外中（機能は無効）
# 詳細: docs/changelog.md「コードベースRAG 一時無効化」参照
from tools.image_tools import generate_image, edit_image, watermark_image, apply_auto_watermark, IMAGE_MODELS_BY_PROVIDER
from tools.mcp_client import MCPClientManager
from tools.notify_tools import send_email_notification
from pydantic import BaseModel

mcp_manager = MCPClientManager()

# デフォルトのプロバイダー設定（.env のAzure設定）
_default_provider_config = {
    "type": "azure",
    "url": AZURE_OPENAI_ENDPOINT,
    "api_key": AZURE_OPENAI_API_KEY,
    "model": AZURE_OPENAI_DEPLOYMENT,
    "api_version": AZURE_OPENAI_API_VERSION,
    "tools_enabled": True,
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
                # tools_enabled は旧ファイルにない場合でも補完（後方互換）
                if "tools_enabled" not in saved:
                    saved["tools_enabled"] = saved["type"] == "azure"
                print(f"[provider] loaded from file: {saved['type']} / {saved['model']} / tools={saved['tools_enabled']}")
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
    if not _provider_config.get("url") and not _provider_config.get("api_key") and _provider_config["type"] in ("azure", "foundry"):
        raise ValueError("LLMプロバイダーが未設定です。/setup でセットアップを完了してください。")
    if _provider_config["type"] == "gemini" and not _provider_config.get("api_key"):
        raise ValueError("Gemini APIキーが未設定です。/setup でセットアップを完了してください。")
    if _provider_config["type"] in ("azure", "foundry"):
        return AzureOpenAI(
            azure_endpoint=_provider_config["url"],
            api_key=_provider_config["api_key"],
            api_version=_provider_config["api_version"] or (FOUNDRY_API_VERSION if _provider_config["type"] == "foundry" else AZURE_OPENAI_API_VERSION),
            http_client=httpx.Client(trust_env=False),  # 社内プロキシをバイパス
        )
    elif _provider_config["type"] == "gemini":
        # Google Gemini (OpenAI互換エンドポイント経由)
        return OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=_provider_config["api_key"],
            http_client=httpx.Client(trust_env=False),
        )
    elif _provider_config["type"] == "openai":
        # 本家 OpenAI (api.openai.com)
        return OpenAI(
            api_key=_provider_config["api_key"],
            http_client=httpx.Client(trust_env=False),
        )
    else:
        # "openai_compatible" (ローカルLLM等) は OpenAI互換クライアント
        return OpenAI(
            base_url=_provider_config["url"].rstrip("/") + "/v1",
            api_key=_provider_config["api_key"] or "dummy",
            http_client=httpx.Client(trust_env=False),  # 社内プロキシをバイパス
        )

def _make_async_client():
    """_make_client の非同期版"""
    if not _provider_config.get("url") and not _provider_config.get("api_key") and _provider_config["type"] in ("azure", "foundry"):
        raise ValueError("LLMプロバイダーが未設定です。")
    if _provider_config["type"] in ("azure", "foundry"):
        return AsyncAzureOpenAI(
            azure_endpoint=_provider_config["url"],
            api_key=_provider_config["api_key"],
            api_version=_provider_config["api_version"] or (FOUNDRY_API_VERSION if _provider_config["type"] == "foundry" else AZURE_OPENAI_API_VERSION),
            http_client=httpx.AsyncClient(trust_env=False),
        )
    elif _provider_config["type"] == "gemini":
        return AsyncOpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=_provider_config["api_key"],
            http_client=httpx.AsyncClient(trust_env=False),
        )
    elif _provider_config["type"] == "openai":
        return AsyncOpenAI(
            api_key=_provider_config["api_key"],
            http_client=httpx.AsyncClient(trust_env=False),
        )
    else:
        return AsyncOpenAI(
            base_url=_provider_config["url"].rstrip("/") + "/v1",
            api_key=_provider_config["api_key"] or "dummy",
            http_client=httpx.AsyncClient(trust_env=False),
        )


async def _inbox_process(md_path):
    """inbox MD を読み込んでエージェントに処理させ、results/ に書き出す。"""
    import re
    from tools.inbox_worker import complete_request
    try:
        raw = md_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[WARN] inbox 読み込みエラー: {e}", flush=True)
        complete_request(md_path, "error")
        return

    # frontmatter パース（--- ... --- ブロック）
    fm: dict = {}
    body = raw
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", raw, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()
        body = fm_match.group(2).strip()

    if not body:
        print(f"[WARN] inbox: {md_path.name} の本文が空のためスキップ", flush=True)
        complete_request(md_path, "skipped")
        return

    job_id = datetime.now().strftime("%H%M%S")
    print(f"[INFO] inbox 処理開始: {md_path.name} (job={job_id})", flush=True)

    answer_chunks = []
    try:
        async for chunk in _agent_stream_inner(
            user_message=body,
            history=[],
            bypass_approval=True,
        ):
            if chunk.startswith("data: "):
                try:
                    data = json.loads(chunk[6:])
                    if data.get("type") == "answer_chunk":
                        answer_chunks.append(data.get("content", ""))
                except Exception:
                    pass
    except Exception as e:
        print(f"[WARN] inbox エージェントエラー: {e}", flush=True)
        answer_chunks = [f"エラー: {e}"]

    answer = "".join(answer_chunks)

    results_dir = complete_request(md_path, job_id)
    result_file = results_dir / "result.md"
    result_file.write_text(
        f"# 実行結果\n\n**リクエスト:** {md_path.name}\n\n---\n\n{answer}\n",
        encoding="utf-8",
    )
    print(f"[INFO] inbox 完了: {result_file}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時に一時ディレクトリを自動削除
    import shutil
    for tmp_name in ["_gp_tmp"]:
        tmp_path = ALLOWED_WORK_DIR / tmp_name
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
            print(f"[INFO] 起動時クリーンアップ: {tmp_path} を削除しました")

    # SearXNG を自動起動
    if SEARXNG_ENABLED:
        compose_file = Path(__file__).parent / "docker-compose.searxng.yml"
        if compose_file.exists():
            result = subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "up", "-d"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"[WARN] SearXNG 起動失敗: {result.stderr.strip() or result.stdout.strip()}")

    ensure_inbox_dirs()
    start_worker(_inbox_process)

    # 非同期ジョブ DB 初期化 + ワーカープロセス起動
    global _async_worker_proc
    _init_async_db()
    _async_worker_proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "async_worker.py"),
         "--jobs", str(ASYNC_MAX_JOBS)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8",
    )
    print(f"[INFO] async_worker 起動 PID={_async_worker_proc.pid}", flush=True)

    # MCP クライアント起動・動的ツール登録
    try:
        await mcp_manager.start()
        mcp_schemas = mcp_manager.get_tool_schemas()
        mcp_registry = mcp_manager.get_tool_registry()
        if mcp_registry:
            TOOL_REGISTRY.update(mcp_registry)
            TOOLS.extend(mcp_schemas)
            print(f"[INFO] MCP: {len(mcp_registry)} ツールを登録しました: {list(mcp_registry.keys())}", flush=True)
    except Exception as e:
        print(f"[WARN] MCP 起動エラー: {e}")

    yield

    # inbox ワーカー停止
    stop_worker()

    # 非同期ジョブワーカー停止
    try:
        _async_worker_proc.terminate()
        _async_worker_proc.wait(timeout=5)
    except Exception:
        pass

    # MCP クライアント停止（anyio cancel scope との干渉を抑制）
    try:
        await mcp_manager.stop()
    except BaseException:
        pass


_async_worker_proc: subprocess.Popen | None = None  # set in lifespan

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


def show_mermaid_batch_refine_dialog(path: str, _workspace_scope: str = "") -> dict:
    """MDファイル内のMermaidブロックを抽出し、一括清書ダイアログをUIに表示します。
    ユーザーが清書プロンプトを入力後、ブラウザ側で各図を自動的に清書・差し替えします。"""
    import re as _re
    from config import ALLOWED_WORK_DIR
    target = (ALLOWED_WORK_DIR / path).resolve()
    if not str(target).startswith(str(ALLOWED_WORK_DIR)):
        return {"error": "作業ディレクトリ外のファイルへのアクセスは禁止"}
    if not target.exists():
        return {"error": f"ファイルが見つかりません: {path}"}
    content = target.read_text(encoding="utf-8")
    blocks = []
    for i, m in enumerate(_re.finditer(r'```mermaid\n(.*?)\n```', content, _re.DOTALL)):
        blocks.append({"index": i, "code": m.group(1).strip()})
    if not blocks:
        return {"message": "Mermaidブロックが見つかりませんでした", "count": 0}
    return {
        "file_path": path,
        "workspace_scope": _workspace_scope,
        "blocks": blocks,
        "count": len(blocks),
        "trigger_ui": "mermaid_batch_refine",
    }


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
    "protected_list_read": protected_list_read,
    "workspace_backup": workspace_backup,
    "archive_workspace": archive_workspace,
    "protected_list_update": protected_list_update,
    "protected_list_replace": protected_list_replace,
    "workspace_cleanup_preview": workspace_cleanup_preview,
    "render_manim": render_manim,
    "read_pdf": read_pdf,
    "write_pdf": write_pdf,
    "read_docx": read_docx,
    "write_docx": write_docx,
    "edit_docx": edit_docx,
    "read_xlsx": read_xlsx,
    "write_xlsx": write_xlsx,
    "edit_xlsx": edit_xlsx,
    "read_pptx": read_pptx,
    "write_pptx": write_pptx,
    "edit_pptx": edit_pptx,
    "list_ansible_playbooks": list_ansible_playbooks,
    "run_ansible_playbook": run_ansible_playbook,
    "run_powershell": run_powershell,
    "winrm_command": winrm_command,
    "gather_host_info": gather_host_info,
    "run_background": run_background,
    "check_background": check_background,
    "kill_background": kill_background,
    "rag_save": rag_save,
    "rag_search": rag_search,
    "rag_update_status": rag_update_status,
    "rag_list": rag_list,
    # codebase_index / codebase_search / codebase_clear は肥大化問題が未解決のため無効
    "generate_image": generate_image,
    "edit_image": edit_image,
    "watermark_image": watermark_image,
    "show_mermaid_batch_refine_dialog": show_mermaid_batch_refine_dialog,
}

if RESPONSES_API_ENABLED:
    TOOL_REGISTRY["call_responses_api"] = call_responses_api

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
            "description": "ディレクトリ内のファイル一覧をツリー形式で取得します。パラメータは path と pattern の2つのみです。--depth などのオプションは存在しません。例: list_files() でワークスペース全体、list_files(path='src') でsrcディレクトリ、list_files(pattern='**/*.py') でPythonファイルのみ",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "ディレクトリパス。省略すると workspace ルートを表示。例: '.' または 'src'"},
                    "pattern": {"type": "string", "description": "globパターン。省略すると全ファイル。例: '**/*.py' でPythonファイルのみ"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "任意のコマンドを実行します（mkfs/fdisk/dd/shutdown/reboot等のシステム破壊コマンドのみ禁止）",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "実行するコマンド (例: python script.py)"},
                    "work_dir": {"type": "string", "description": "作業ディレクトリ (省略可)"},
                    "description": {"type": "string", "description": "この実行の目的を日本語で一言説明 (例: GitLabへプッシュ、依存パッケージをインストール)"},
                    "env": {"type": "object", "description": "追加・上書きする環境変数 (例: {\"AZURE_SUBSCRIPTION_ID\": \"xxx\", \"no_proxy\": \"*.azure.com\"})。現在の環境にマージされます。"},
                    "timeout_minutes": {"type": "number", "description": "タイムアウト時間（分）。省略時はデフォルト（通常30秒、apt/docker等は5分）。0で無制限。Ansible・長時間処理は明示的に指定すること（例: 30）。"},
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
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "failed"], "description": "タスクの状態"},
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
    {
        "type": "function",
        "function": {
            "name": "workspace_backup",
            "description": "ワークスペースの内容を ~/Backups/YYYYMMDD.tar.gz にバックアップします。「バックアップして」と言われたらこのツールを使ってください。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_workspace",
            "description": "現在の作業ディレクトリを Obsidian vault の archives フォルダにコピーして蓄積します。「アーカイブして」と言われたらこのツールを使ってください。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": "アーカイブする作業ディレクトリ名（例: HOGE）。現在の作業ディレクトリ名を指定する。",
                    },
                },
                "required": ["scope"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "protected_list_read",
            "description": "ワークスペースの保護リストを読み取ります。削除から保護するファイル・ディレクトリの一覧を返します。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "protected_list_update",
            "description": "ワークスペースの保護リストにパスを追加します（既存エントリは保持）。「〇〇を保護リストに追加して」と言われたらこちらを使ってください。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "保護するパスのリスト（workspace直下の名前。例: ['myproject/', 'important.txt', 'data/']）",
                    },
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "protected_list_replace",
            "description": "ワークスペースの保護リストを完全に置き換えます。既存エントリをすべて削除して新しいリストで上書きします。「保護リストをこれだけにして」と明示的に言われた場合のみ使ってください。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "新しい保護リスト（workspace直下の名前。例: ['myproject/', 'important.txt']）",
                    },
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_cleanup_preview",
            "description": "ワークスペースを掃除する前の確認リストを生成します。保護リストにないファイル・ディレクトリを一覧します。実際の削除はユーザーがUIで確認した後に行われます。「ワークスペースを掃除して」と言われたらこのツールを呼んでください。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_manim",
            "description": "Manim コードをレンダリングして最終フレームの PNG 画像を返します。生成した画像はUIに自動表示され、LLMが視覚的にフィードバックを得て改善できます。Manim アニメーションを作成・修正する際は必ずこのツールで確認してください。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Manim Python コード（直接渡す場合）"},
                    "file_path": {"type": "string", "description": "workspace 内の .py ファイルパス（code と排他）"},
                    "scene_name": {"type": "string", "description": "レンダリングするシーン名（省略時は自動検出）"},
                    "quality": {"type": "string", "enum": ["l", "m", "h"], "description": "画質: l=低/高速(デフォルト), m=中, h=高"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_pdf",
            "description": "PDF ファイルのテキストを抽出します。仕様書・マニュアル・レポートなど PDF を読み取るときに使います。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "workspace 相対パス (.pdf)"},
                    "pages": {"type": "string", "description": "抽出するページ範囲 (例: \"1\", \"1-3\", \"2,4,6\")。省略時は全ページ"},
                    "extract_tables": {"type": "boolean", "description": "テーブルを Markdown 形式で抽出するか (デフォルト: false)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_pdf",
            "description": "Markdown 風テキストから PDF ファイルを生成します。調査結果・レポート・議事録などをPDF出力するときに使います。日本語対応。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "出力先 workspace 相対パス (.pdf)"},
                    "content": {"type": "string", "description": "Markdown 風テキスト。# 見出し1 / ## 見出し2 / ### 見出し3 / - 箇条書き / | テーブル 対応。"},
                    "title": {"type": "string", "description": "PDF タイトル（表紙見出し、省略可）"},
                    "font_size": {"type": "integer", "description": "本文フォントサイズ（デフォルト: 11）"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_docx",
            "description": "Word ファイル (.docx) のテキストを読み取ります。ドラッグアンドドロップでアップロードされた Word 文書の内容確認・PDF変換などに使います。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "workspace 相対パス (.docx)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_docx",
            "description": "Word ファイル (.docx) を新規作成・上書きします。Markdown 風テキスト（# 見出し等）を Word 文書に変換します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "出力先 workspace 相対パス (.docx)"},
                    "content": {"type": "string", "description": "Markdown 風テキスト"},
                    "title": {"type": "string", "description": "ドキュメントタイトル（省略可）"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_docx",
            "description": "Word ファイル内の特定テキストを置換します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "workspace 相対パス (.docx)"},
                    "old_text": {"type": "string", "description": "置換前のテキスト"},
                    "new_text": {"type": "string", "description": "置換後のテキスト"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_xlsx",
            "description": "Excel ファイル (.xlsx) のデータを読み取ります。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "workspace 相対パス (.xlsx)"},
                    "sheet": {"type": "string", "description": "シート名（省略時は最初のシート）"},
                    "max_rows": {"type": "integer", "description": "最大読み込み行数（デフォルト: 200）"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_xlsx",
            "description": "Excel ファイル (.xlsx) を新規作成・上書きします。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "出力先 workspace 相対パス (.xlsx)"},
                    "data": {"type": "array", "items": {"type": "array", "items": {}}, "description": "行データのリスト（例: [[\"Alice\", 30], [\"Bob\", 25]]）"},
                    "sheet": {"type": "string", "description": "シート名（デフォルト: Sheet1）"},
                    "headers": {"type": "array", "items": {"type": "string"}, "description": "ヘッダー行（省略可）"},
                },
                "required": ["path", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_xlsx",
            "description": "Excel ファイルの特定セルを編集します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "workspace 相対パス (.xlsx)"},
                    "sheet": {"type": "string", "description": "シート名（省略時は最初のシート）"},
                    "cell": {"type": "string", "description": "セルアドレス（例: \"B3\"）"},
                    "row": {"type": "integer", "description": "行番号（1始まり）"},
                    "col": {"type": "integer", "description": "列番号（1始まり）"},
                    "value": {"type": "string", "description": "設定する値"},
                },
                "required": ["path", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_pptx",
            "description": "PowerPoint ファイル (.pptx) のテキストを読み取ります。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "workspace 相対パス (.pptx)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_pptx",
            "description": "PowerPoint ファイル (.pptx) を新規作成・上書きします。テキスト・画像・テキスト+画像の3種類のスライドレイアウトに対応。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "出力先 workspace 相対パス (.pptx)"},
                    "slides": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "スライドのリスト。各要素: {\"title\": \"タイトル\", \"content\": \"本文（改行区切り）\", \"image_path\": \"workspace相対パス\"}。image_path のみ→画像スライド（中央配置）、content+image_path→左テキスト・右画像、content のみ→テキストスライド。image_path は必ず workspace 相対パスで指定すること（例: GRAAA/AI_Output_Images/generated_xxx.png）。",
                    },
                    "title": {"type": "string", "description": "プレゼンテーション全体のタイトル（省略可）"},
                },
                "required": ["path", "slides"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_pptx",
            "description": "PowerPoint の特定スライドのテキストを置換します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "workspace 相対パス (.pptx)"},
                    "slide_number": {"type": "integer", "description": "スライド番号（1始まり）"},
                    "old_text": {"type": "string", "description": "置換前のテキスト"},
                    "new_text": {"type": "string", "description": "置換後のテキスト"},
                },
                "required": ["path", "slide_number", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_ansible_playbooks",
            "description": "workspace 以下の Ansible プレイブック (.yml) を再帰的に列挙してUIにチェックボックスで表示する。ユーザーが /ansible を実行したときに呼ぶ。このツールを呼んだ後は必ずターンを終了してユーザーの選択を待つこと。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_ansible_playbook",
            "description": "指定したプレイブックを実行する。workspace/.azure_creds から環境変数（Azureクレデンシャル等）を自動ロードする。ユーザーがチェックボックスでプレイブックを選択して「実行」を押したときに呼ぶ。",
            "parameters": {
                "type": "object",
                "properties": {
                    "playbook": {"type": "string", "description": "実行するプレイブックの workspace 相対パス (例: myproject/site.yml)"},
                },
                "required": ["playbook"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_powershell",
            "description": "WSL2 から Windows の PowerShell コマンドを実行します。Windowsファイル操作・アプリ起動・クリップボード・通知・WinGet・レジストリ・サービス管理・システム情報取得など Windows 固有の操作に使います。Linux コマンドで代替できる場合は run_command を使ってください。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "実行する PowerShell コマンド（例: Get-ComputerInfo | Select-Object WindowsProductName,TotalPhysicalMemory）。複数行・複数コマンドも可。",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "タイムアウト秒数（デフォルト: 30秒）。WinGet など時間がかかる操作は大きく設定する（例: 120）。",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gather_host_info",
            "description": "Windows / Linux ホストの情報を一括収集して構造化データで返す。設計書・仕様書作成の前に必ずこのツールで情報収集すること。個別コマンドを何度も実行するより確実で抜け漏れがない。収集項目: ホスト名・OS・CPU・メモリ・ディスク・NIC・DNS・GW・オープンポート・インストール済みソフト/パッケージ・実行中サービス・ユーザー（Windows: スケジュールタスク・Windows Update履歴も含む / Linux: cron・ルーティングも含む）",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "対象ホストの IP またはホスト名"},
                    "os_type": {"type": "string", "description": "'windows' / 'linux' / 'auto'。auto を指定するとポートスキャンでOS自動判定する（5985=Windows / 22=Linux）"},
                    "username": {"type": "string", "description": "ユーザー名"},
                    "password": {"type": "string", "description": "パスワード（Windows必須 / Linux パスワード認証時）"},
                    "key_file": {"type": "string", "description": "SSH 秘密鍵ファイルパス（Linux鍵認証時。workspace相対パス可）"},
                    "port": {"type": "integer", "description": "ポート番号（省略時: Windows=5985, Linux=22）"},
                    "use_ssl": {"type": "boolean", "description": "Windows HTTPS(5986) を使う場合 True"},
                    "timeout_seconds": {"type": "integer", "description": "タイムアウト秒数（デフォルト 60）"},
                },
                "required": ["host", "os_type", "username"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "winrm_command",
            "description": "WinRM 経由でリモート Windows に PowerShell コマンドを実行する。TrustedHosts 設定不要・ドメイン未参加環境でも動作する。IP 直指定で複数の異なる環境を管理するときに使う。ローカル Windows の操作は run_powershell を使うこと。",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "接続先の IP アドレスまたはホスト名（例: 10.49.89.160）",
                    },
                    "command": {
                        "type": "string",
                        "description": "実行する PowerShell コマンド（例: Get-InstalledModule | Select Name,Version）",
                    },
                    "username": {
                        "type": "string",
                        "description": "ユーザー名（例: Administrator、DOMAIN\\\\user）",
                    },
                    "password": {
                        "type": "string",
                        "description": "パスワード",
                    },
                    "port": {
                        "type": "integer",
                        "description": "ポート番号。HTTP=5985（デフォルト）、HTTPS=5986",
                    },
                    "use_ssl": {
                        "type": "boolean",
                        "description": "True にすると HTTPS(5986) で接続する。証明書検証はスキップする。",
                    },
                    "transport": {
                        "type": "string",
                        "description": "認証方式: ntlm（デフォルト・ほとんどの環境で動作）/ kerberos / basic / credssp",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "タイムアウト秒数（デフォルト: 30）",
                    },
                },
                "required": ["host", "command", "username", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_background",
            "description": "コマンドをバックグラウンド（非同期）で起動し、即座にジョブIDを返す。ビルド・サーバー起動・長時間処理に使う。進捗は check_background(job_id) で確認する。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "バックグラウンドで実行するコマンド"},
                    "work_dir": {"type": "string", "description": "作業ディレクトリ（workspace 相対パス）"},
                    "description": {"type": "string", "description": "ジョブの説明（一覧表示時に使う）"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_background",
            "description": "バックグラウンドジョブの状態と stdout/stderr を確認する。job_id 省略で全ジョブ一覧を返す。完了・失敗したジョブは確認後に自動削除される。",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "確認するジョブID。省略すると全ジョブ一覧を返す。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kill_background",
            "description": "実行中のバックグラウンドジョブを強制停止する",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "停止するジョブID"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_save",
            "description": (
                "知見をRAGデータベースに保存します。"
                "タスク完了・エラー解決・問題発見時にユーザーへ「記録しますか？」と確認してから呼び出してください。"
                "record_type: success=動いた手順・解決策 / prohibited=絶対やってはいけない操作 / caution=間違えやすい・ハマりやすい罠"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "記録する内容の要約（何をしたか・何がダメか・なぜかを含める）"},
                    "record_type": {"type": "string", "enum": ["success", "prohibited", "caution"], "description": "記録の種類"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "検索用タグ（例: ['ansible', 'gitlab', 'proxy']）"},
                },
                "required": ["summary", "record_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": (
                "RAGデータベースから関連する知見を検索します。"
                "タスク開始前に prohibited で禁止事項、caution で注意点、success で参考手順を確認してください。"
                "record_type を省略すると全タイプ横断検索します。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "検索クエリ（自然言語でOK）"},
                    "record_type": {"type": "string", "enum": ["success", "prohibited", "caution"], "description": "絞り込む種類（省略可）"},
                    "n_results": {"type": "integer", "description": "取得件数（デフォルト5）", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_update_status",
            "description": (
                "RAGデータベースの記録を deprecated（無効）に変更します。"
                "古くなった・仕様変更で無効になった記録を発見したとき、またはユーザーが「あれ古い」と言ったときに使います。"
                "削除はせず deprecated として残します（履歴保持のため）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "更新する記録のID"},
                    "new_status": {"type": "string", "enum": ["active", "deprecated"], "description": "新しいステータス"},
                    "reason": {"type": "string", "description": "変更理由（省略可）"},
                },
                "required": ["record_id", "new_status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_list",
            "description": "/rag-review スキルで記録一覧を表示するときに使います。ユーザーに見せて古いものを整理するため。",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_type": {"type": "string", "enum": ["success", "prohibited", "caution"], "description": "絞り込む種類（省略可）"},
                    "status": {"type": "string", "enum": ["active", "deprecated", "all"], "description": "ステータスフィルタ（デフォルト: active）", "default": "active"},
                },
                "required": [],
            },
        },
    },
    # codebase_index / codebase_search / codebase_clear のスキーマは肥大化問題が未解決のため除外
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "テキストプロンプトから画像を生成します。「〇〇の画像を作って」「〇〇を描いて」などの依頼に使います。セットアップ画面で設定したプロバイダー/モデルを使用します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "生成したい画像の詳細な説明（英語推奨）"},
                    "size": {"type": "string", "enum": ["1024x1024", "1024x1536", "1536x1024"], "description": "画像サイズ。ユーザーが明示的に指定した場合のみ設定し、それ以外は必ず省略すること"},
                    "quality": {"type": "string", "enum": ["low", "medium", "high"], "description": "画質。ユーザーが明示的に指定した場合のみ設定し、それ以外は必ず省略すること"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_image",
            "description": "ワークスペース内の画像を編集・清書します（img2img）。ユーザーが下絵や手書きラフをアップロードした場合に使います。OpenAI または Gemini が必要です。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "ワークスペース内の元画像ファイルパス"},
                    "prompt": {"type": "string", "description": "編集内容の指示（例: 'Clean up this sketch into a professional illustration'）"},
                    "size": {"type": "string", "enum": ["1024x1024", "1024x1536", "1536x1024"], "description": "出力サイズ。ユーザーが明示的に指定した場合のみ設定し、それ以外は必ず省略すること"},
                },
                "required": ["image_path", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watermark_image",
            "description": "ワークスペース内の画像にテキストウォーターマーク（透かし）を追加します。生成画像にAI生成表示や著作権表記を入れたい場合に使います。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "ワークスペース内の対象画像ファイルパス"},
                    "text": {"type": "string", "description": "ウォーターマークとして表示するテキスト（例: 'AI Generated', '© 2025'）"},
                    "position": {"type": "string", "enum": ["topleft", "topright", "bottomleft", "bottomright", "center"], "description": "表示位置（デフォルト: bottomright）"},
                    "opacity": {"type": "number", "description": "不透明度 0.0〜1.0（デフォルト: 0.6）"},
                },
                "required": ["image_path", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_mermaid_batch_refine_dialog",
            "description": "MDファイル内のすべてのMermaid図を一括で清書するダイアログをUIに表示します。ユーザーが「このMDファイルの図を清書して」と依頼した場合に使います。ダイアログで清書プロンプトを入力後、全図が自動的に清書されてMDファイル内の各Mermaidブロックが画像参照に差し替えられます。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "対象MDファイルのパス（作業ディレクトリ相対）"},
                },
                "required": ["path"],
            },
        },
    },
]

if RESPONSES_API_ENABLED:
    TOOLS.append({
        "type": "function",
        "function": {
            "name": "call_responses_api",
            "description": f"コード生成特化モデル（{RESPONSES_API_MODEL or 'Responses API'}）を呼び出してコードを生成します。write_file / edit_file でコードを保存する前に必ずこのツールでコードを生成してください。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "生成してほしいコードの詳細な指示（言語・ファイルパス・既存コードの文脈・要件を含める）",
                    },
                },
                "required": ["prompt"],
            },
        },
    })

# Deep Research が設定されている場合、ツール説明を動的に更新してAIが確実にweb_researchを使うよう誘導
if WEB_RESEARCH_PROVIDER.startswith("deep-research"):
    _dr_label = WEB_RESEARCH_PROVIDER.replace("deep-research-", "").upper()
    for _tool in TOOLS:
        _fn = _tool.get("function", {})
        if _fn.get("name") == "web_research":
            _fn["description"] = (
                f"【Deep Research ({_dr_label}) 使用中】OpenAI Deep Researchを使った高精度Web調査。"
                f"Web調査・情報収集が必要な場合は必ずこのツールを使うこと。"
                f"web_searchより大幅に精度・網羅性が高く、詳細なレポートを返す。"
                f"【重要】1ターンにつき必ず1回のみ呼び出すこと。複数クエリを同時に呼び出すと高額課金になるため禁止。"
            )
        elif _fn.get("name") == "web_search":
            _fn["description"] = (
                "シンプルなWeb検索（DuckDuckGo/Tavily）。"
                "現在はDeep Researchが設定されているため、Web調査にはweb_researchを使うこと。"
                "このツールはURLが既にわかっていてweb_fetchを使う直前の簡易確認など、限定的な用途のみ。"
            )


def _get_error_hint(tool_name: str, error_type: str, error_msg: str, args: dict) -> str:
    """エラー種別に応じた自己修正ヒントを返す"""
    hints = []
    if "No module named" in error_msg or error_type == "ModuleNotFoundError":
        import re
        m = re.search(r"No module named '([^']+)'", error_msg)
        pkg = m.group(1).split(".")[0] if m else "該当パッケージ"
        hints.append(f"run_command('pip install {pkg}') でインストールしてから再実行する")
    if error_type == "FileNotFoundError" or "No such file" in error_msg:
        path = args.get("path") or args.get("file_path") or ""
        name_part = Path(path).name if path else ""
        hints.append(
            f"glob_files('**/{name_part}') または list_files() で正しいパスを確認してから再実行する"
            if name_part else "list_files() でファイル一覧を確認してから再実行する"
        )
    if error_type == "SyntaxError" or "invalid syntax" in error_msg:
        path = args.get("path") or args.get("file_path") or ""
        hints.append(
            f"read_file('{path}') でファイルを確認し、edit_file で構文エラーを修正してから再実行する"
            if path else "ファイルを read_file で確認し、構文エラーを修正してから再実行する"
        )
    if "expected_replacements" in error_msg or "occurrences" in error_msg or "一致" in error_msg:
        path = args.get("path") or ""
        hints.append(
            f"read_file('{path}') で実際のファイル内容を確認し、old_str を正確に一致させてから edit_file を再試行する"
            if path else "read_file でファイル内容を確認し old_str を修正してから再試行する"
        )
    if error_type == "PermissionError":
        hints.append("権限エラー: 別のパスを使うか sudo を検討する")
    if error_type == "TimeoutError" or "timeout" in error_msg.lower():
        hints.append("タイムアウト: docker ps -a 等で現在の状態を確認してから判断する（即リトライ禁止）")
    if not hints:
        hints.append("エラーメッセージを精読して原因を特定し、修正してから別アプローチを試みる")
    return " → ".join(hints)


import threading
_ra_called_flag = threading.local()


def _inject_responses_api(tool_name: str, arguments: dict) -> None:
    """write_file / edit_file の content を Responses API で生成したコードで上書きする"""
    from tools.responses_tools import call_responses_api
    content_key = "content" if tool_name == "write_file" else "new_content"
    existing = arguments.get(content_key, "")
    if not existing:
        return
    path = arguments.get("path", "")
    prompt = (
        f"次のコードを生成してください。ファイルパス: {path}\n\n"
        f"要件（エージェントが用意したドラフト）:\n{existing}\n\n"
        "完全なコードのみを出力し、説明文は不要です。"
    )
    generated = call_responses_api(prompt)
    if generated and not generated.startswith("[ERROR]"):
        # マークダウンのコードフェンスを除去
        import re
        generated = re.sub(r"^```[^\n]*\n?", "", generated.strip())
        generated = re.sub(r"\n?```$", "", generated)
        arguments[content_key] = generated


def execute_tool(name: str, arguments: dict) -> str:
    if name not in TOOL_REGISTRY:
        return json.dumps({"error": f"未知のツール: {name}"}, ensure_ascii=False)
    # Responses API が有効な場合、AIが call_responses_api をスキップして write_file/edit_file を直接呼んだ時のみ自動注入
    if RESPONSES_API_ENABLED and name == "call_responses_api":
        _ra_called_flag.called = True
    if RESPONSES_API_ENABLED and name in ("write_file", "edit_file"):
        if not getattr(_ra_called_flag, "called", False):
            _inject_responses_api(name, arguments)
        _ra_called_flag.called = False
    try:
        result = TOOL_REGISTRY[name](**arguments)
        # 検索系ツールで結果が空の場合、LLMがハルシネーションしないよう明示的な警告を付与
        # 検索バックエンドをログに出力
        if name in ("web_search", "web_research") and isinstance(result, dict):
            backend = result.get("search_backend", WEB_RESEARCH_PROVIDER if name == "web_research" else "tavily/ddgs")
            item_count = len(result.get("results") or result.get("sources") or [])
            has_rpt = bool(result.get("report"))
            print(f"[{name}] backend={backend} items={item_count} report={'yes' if has_rpt else 'no'}", flush=True)
        # Deep Research の場合は report フィールドがあれば結果ありとみなす
        if name in ("web_search", "web_research") and isinstance(result, dict):
            has_report = bool(result.get("report"))
            items = result.get("results") or result.get("sources") or []
            if not has_report and (not items or "error" in result):
                result["_warning"] = (
                    "【重要】検索結果が見つかりませんでした。"
                    "この情報に基づいた回答はできません。"
                    "「情報が見つかりませんでした」と正直にユーザーに伝えてください。推測や作り話は絶対にしないこと。"
                )
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        hint = _get_error_hint(name, error_type, error_msg, arguments)
        return json.dumps({
            "error": f"ツール実行エラー: {error_type}: {error_msg}",
            "error_type": error_type,
            "hint": hint,
        }, ensure_ascii=False)


async def execute_tool_async(name: str, arguments: dict) -> str:
    """execute_tool をスレッドプールで非同期実行するラッパー"""
    # MCP ツール（サーバーID__ツール名 形式）は async callable なので直接 await する
    if "__" in name and name in TOOL_REGISTRY:
        try:
            return await asyncio.wait_for(
                TOOL_REGISTRY[name](**arguments),
                timeout=60,
            )
        except asyncio.TimeoutError:
            return json.dumps({"error": f"MCPツールがタイムアウトしました: {name}"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"MCPツールエラー: {e}"}, ensure_ascii=False)

    # ツール引数で指定されたタイムアウトがあればそれに合わせて待つ（+10秒のマージン）
    if name in ("generate_image", "edit_image", "watermark_image"):
        _HIGH_RES = {"1536x1024", "1024x1536", "1792x1024", "1024x1792"}
        timeout = 600 if arguments.get("size") in _HIGH_RES else 300
    elif name in ("web_research", "web_search") and WEB_RESEARCH_PROVIDER.startswith("deep-research"):
        timeout = 3600  # Deep Research は最大1時間待つ（OpenAI公式推奨値）
    elif name == "web_research":
        timeout = 60
    elif name == "run_powershell" and "timeout_seconds" in arguments:
        timeout = int(arguments["timeout_seconds"]) + 10
    elif name == "run_command" and "timeout_minutes" in arguments:
        timeout = int(arguments["timeout_minutes"] * 60) + 10
    else:
        timeout = 20
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(execute_tool, name, arguments),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        is_dr = name in ("web_research", "web_search") and WEB_RESEARCH_PROVIDER.startswith("deep-research")
        msg = {"error": f"ツールがタイムアウトしました ({timeout}秒): {name}"}
        if is_dr:
            msg["note"] = "Deep Research がタイムアウトしました。再試行・別クエリでの再実行は絶対にしないこと。ユーザーに「タイムアウトしました」とだけ報告してください。"
        return json.dumps(msg, ensure_ascii=False)


async def _stream_command(arguments: dict):
    """run_command をストリーミングで実行する async generator。
    {'type': 'line', 'line': str} を逐次 yield し、最後に {'type': 'result', 'result': str} を yield する。
    bash / ブロックコマンド / && チェーンも適切に処理する。
    """
    import shlex as _shlex

    command = arguments.get("command", "")
    work_dir_str = arguments.get("work_dir")
    timeout_minutes = arguments.get("timeout_minutes")
    env_extra = arguments.get("env")

    # && チェーン: サブコマンドごとにストリーミング
    if "&&" in command:
        sub_commands = _split_shell_chain(command)
        if len(sub_commands) > 1:
            combined_stdout, combined_stderr = [], []
            for sub_cmd in sub_commands:
                sub_result_str = None
                async for chunk in _stream_command({**arguments, "command": sub_cmd}):
                    if chunk["type"] == "line":
                        combined_stdout.append(chunk["line"] + "\n")
                        yield chunk
                    elif chunk["type"] == "result":
                        sub_result_str = chunk["result"]
                if sub_result_str:
                    try:
                        sub = json.loads(sub_result_str)
                        if sub.get("error") or sub.get("returncode", 0) != 0:
                            yield {"type": "result", "result": sub_result_str}
                            return
                        if sub.get("stderr"):
                            combined_stderr.append(sub["stderr"])
                    except Exception:
                        pass
            yield {"type": "result", "result": json.dumps({
                "stdout": _truncate_output("".join(combined_stdout)),
                "stderr": _truncate_output("".join(combined_stderr), 4000),
                "returncode": 0, "error": None,
            })}
            return

    try:
        args = _shlex.split(command)
    except ValueError as e:
        yield {"type": "result", "result": json.dumps({"error": f"コマンド解析失敗: {e}", "stdout": "", "stderr": "", "returncode": -1})}
        return

    if not args:
        yield {"type": "result", "result": json.dumps({"error": "コマンドが空です", "stdout": "", "stderr": "", "returncode": -1})}
        return

    import os as _os
    args = [_os.path.expanduser(a) for a in args]
    base_cmd = _os.path.basename(args[0])

    # bash はサンドボックス実行（同期・非ストリーミング）
    if base_cmd == "bash":
        result = await asyncio.to_thread(execute_tool, "run_command", arguments)
        yield {"type": "result", "result": result}
        return

    if base_cmd in BLOCKED_COMMANDS:
        yield {"type": "result", "result": json.dumps({"error": f"'{base_cmd}' はシステム破壊の恐れがあるため実行できません", "stdout": "", "stderr": "", "returncode": -1})}
        return

    # work_dir 解決
    if work_dir_str:
        p = Path(work_dir_str)
        if not p.is_absolute():
            parts = p.parts
            if parts and parts[0] == ALLOWED_WORK_DIR.name:
                p = Path(*parts[1:]) if len(parts) > 1 else Path(".")
        resolved_work_dir = (p if p.is_absolute() else ALLOWED_WORK_DIR / p).resolve()
    else:
        resolved_work_dir = ALLOWED_WORK_DIR
    if not str(resolved_work_dir).startswith(str(ALLOWED_WORK_DIR)):
        yield {"type": "result", "result": json.dumps({"error": "許可された作業ディレクトリ外へのアクセスは禁止されています", "stdout": "", "stderr": "", "returncode": -1})}
        return

    # タイムアウト
    from config import COMMAND_TIMEOUT_SECONDS as _CTO
    if timeout_minutes is not None:
        effective_timeout = int(timeout_minutes * 60) if timeout_minutes > 0 else None
    elif base_cmd in LONG_RUNNING_CMDS:
        effective_timeout = 300
    else:
        effective_timeout = _CTO

    merged_env = None
    if env_extra:
        merged_env = {**_os.environ, **{str(k): str(v) for k, v in env_extra.items()}}

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(resolved_work_dir),
            env=merged_env,
        )
    except FileNotFoundError:
        yield {"type": "result", "result": json.dumps({"error": f"コマンド '{args[0]}' が見つかりません", "stdout": "", "stderr": "", "returncode": -1})}
        return
    except Exception as e:
        yield {"type": "result", "result": json.dumps({"error": f"実行エラー: {e}", "stdout": "", "stderr": "", "returncode": -1})}
        return

    stdout_lines = []
    timed_out = False
    stderr_task = asyncio.create_task(proc.stderr.read())

    try:
        loop = asyncio.get_event_loop()
        deadline = (loop.time() + effective_timeout) if effective_timeout else None
        async for line_bytes in proc.stdout:
            line = line_bytes.decode("utf-8", errors="replace")
            stdout_lines.append(line)
            yield {"type": "line", "line": line.rstrip()}
            if deadline and loop.time() > deadline:
                timed_out = True
                proc.kill()
                break
    except Exception as e:
        proc.kill()
        yield {"type": "result", "result": json.dumps({"error": str(e), "stdout": _truncate_output("".join(stdout_lines)), "stderr": "", "returncode": -1})}
        return

    await proc.wait()
    stderr_bytes = await stderr_task
    stderr_str = stderr_bytes.decode("utf-8", errors="replace")

    if timed_out:
        label = f"{effective_timeout // 60}分" if effective_timeout >= 60 else f"{effective_timeout}秒"
        yield {"type": "result", "result": json.dumps({
            "error": f"{label}のタイムアウトを超えました（コマンド: {base_cmd}）。タイムアウトを延長して再実行しますか？",
            "stdout": _truncate_output("".join(stdout_lines)),
            "stderr": _truncate_output(stderr_str, 4000),
            "returncode": -1,
        })}
        return

    full_stdout = _truncate_output("".join(stdout_lines))
    full_stderr = _truncate_output(stderr_str, 4000)
    result = {"stdout": full_stdout, "stderr": full_stderr, "returncode": proc.returncode, "error": None}
    if proc.returncode != 0 and args[0] != "sudo" and _is_permission_error(stderr_str):
        result["hint"] = f"権限エラーが発生しました。`sudo {command}` で再実行することで解決できる可能性があります。ユーザーに確認してから再実行してください。"
    yield {"type": "result", "result": json.dumps(result)}


class ChatRequest(BaseModel):
    message: str
    history: list = []
    images: list = []  # base64 画像リスト [{data: "base64...", mime: "image/png"}, ...]
    bypass_approval: bool = False
    no_think: bool = False
    workspace_scope: str = ""  # 空文字 = 制限なし（workspace全体）
    multi_agent: bool = False
    agent_mode: str = "balance"  # "quality" | "balance" | "economy"
    resume_job_id: str = ""     # 計画確認後の再開ジョブID


class MermaidCheckRequest(BaseModel):
    code: str          # Mermaidコード
    image: str         # base64 PNG（data:...プレフィックスなし）
    user_note: str = ""  # ユーザーからの指摘（「問題なし」後に再チェックする場合）


class MermaidRefineRequest(BaseModel):
    image: str = ""            # base64 PNG（初回清書）
    source_path: str = ""      # workspace 内パス（再清書時はこちら）
    prompt: str                # 清書の指示
    workspace_scope: str = ""  # 作業ディレクトリスコープ


class MermaidBatchReplaceRequest(BaseModel):
    file_path: str                  # 対象MDファイル（ALLOWED_WORK_DIR相対）
    workspace_scope: str = ""
    replacements: list[dict]        # [{block_index: int, saved_path: str}]

class ConvertToDocxRequest(BaseModel):
    file_path: str                  # 対象MDファイル（ALLOWED_WORK_DIR相対）
    workspace_scope: str = ""


# サーバー側の安全ネット: クライアントが多く送ってきても最新20件に制限
MAX_HISTORY_MESSAGES = 20
# ローリングサマリーの設定
SUMMARY_TRIGGER = 25   # 履歴がこの件数を超えたら圧縮
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


def _gather_auto_context(workspace_scope: str = "") -> str:
    """workspace内のgit状態を自動収集する（Claude Code方式）"""
    parts = []

    # スコープ指定時はそのフォルダのみ対象、未指定時はworkspace直下全体
    try:
        if workspace_scope:
            targets = [ALLOWED_WORK_DIR / workspace_scope]
        else:
            targets = [p for p in sorted(ALLOWED_WORK_DIR.iterdir()) if p.is_dir() and not p.name.startswith('.')]
    except Exception:
        targets = []

    git_infos = []
    try:
        for p in targets:
            if not p.is_dir():
                continue
            git_dir = p / ".git"
            if not git_dir.exists():
                continue
            info_lines = [f"[{p.name}]"]
            r = subprocess.run(["git", "branch", "--show-current"],
                capture_output=True, text=True, timeout=5, cwd=str(p))
            if r.returncode == 0 and r.stdout.strip():
                info_lines.append(f"branch: {r.stdout.strip()}")
            r = subprocess.run(["git", "status", "--short"],
                capture_output=True, text=True, timeout=5, cwd=str(p))
            if r.returncode == 0 and r.stdout.strip():
                info_lines.append("status:\n" + r.stdout.strip()[:400])
            r = subprocess.run(["git", "diff", "--stat"],
                capture_output=True, text=True, timeout=5, cwd=str(p))
            if r.returncode == 0 and r.stdout.strip():
                info_lines.append("diff --stat:\n" + r.stdout.strip()[:400])
            r = subprocess.run(["git", "log", "--oneline", "-5"],
                capture_output=True, text=True, timeout=5, cwd=str(p))
            if r.returncode == 0 and r.stdout.strip():
                info_lines.append("recent commits:\n" + r.stdout.strip())
            if len(info_lines) > 1:
                git_infos.append("\n".join(info_lines))
    except Exception:
        pass

    if git_infos:
        parts.append("## Git Status\n" + "\n\n".join(git_infos[:5]))

    # ファイル一覧（スコープ指定時はそのフォルダ直下、未指定時はworkspace直下）
    try:
        list_base = (ALLOWED_WORK_DIR / workspace_scope) if workspace_scope else ALLOWED_WORK_DIR
        entries = sorted(list_base.iterdir())
        names = [e.name + ("/" if e.is_dir() else "") for e in entries if not e.name.startswith('.')]
        if names:
            label = f"workspace/{workspace_scope}" if workspace_scope else "workspace"
            parts.append(f"## {label}\n" + "  ".join(names))
    except Exception:
        pass

    if not parts:
        return ""
    return "<auto_context>\n" + "\n\n".join(parts) + "\n</auto_context>"


async def agent_stream(user_message: str, history: list, images: list = None, bypass_approval: bool = False, no_think: bool = False, workspace_scope: str = ""):
    try:
        async for chunk in _agent_stream_inner(user_message, history, images or [], bypass_approval, no_think, workspace_scope):
            yield chunk
    except Exception as e:
        import traceback
        err = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        yield f"data: {json.dumps({'type': 'answer_chunk', 'content': f'エラー: {type(e).__name__}: {e}'})}\n\n"
        yield f"data: {json.dumps({'type': 'answer_done'})}\n\n"
        send_email_notification(f"❌ エージェントエラー: {type(e).__name__}", str(e)[:500])
        print(err)  # uvicornログに出力


async def _interpret_plan_response(user_message: str, plan: dict, async_client, model: str) -> dict:
    """ユーザーの返答から action (execute / replan / cancel) を判定する"""
    from prompts import AGENT_ROLE_LABELS
    roles_str = " / ".join(AGENT_ROLE_LABELS.get(r, r) for r in plan.get("roles", []))
    prompt = (
        f"マルチエージェントの実行計画についてユーザーが返答しました。\n\n"
        f"計画: {roles_str} の順で実行\n"
        f"ユーザーの返答: 「{user_message}」\n\n"
        f"以下のJSONのみを返してください:\n"
        f'{"{"}"action": "execute" | "replan" | "cancel", "notes": "修正内容（replanの場合のみ）"{"}"}\n\n'
        f"判定基準:\n"
        f"- execute: 承認・実行・進めて・OK・やってみて 等\n"
        f"- replan: 役割の追加/削除/変更を求めている\n"
        f"- cancel: キャンセル・やめる・不要 等\n"
    )
    resp = await async_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        max_completion_tokens=200,
    )
    try:
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception:
        return {"action": "execute", "notes": ""}


async def multi_agent_stream(user_message: str, agent_mode: str = "balance", workspace_scope: str = "", resume_job_id: str = ""):
    """マルチエージェントモード:
    Phase 1 (resume_job_id なし): ディスパッチャー → 計画表示 → 停止（plan_ready イベント）
    Phase 2 (resume_job_id あり): ユーザー返答を解釈 → 実行 / 再計画 / キャンセル
    """
    from tools.multi_agent_tools import dispatch_task, run_sub_agent, generate_final_report, new_job_id
    from prompts import get_agent_system_prompt, AGENT_ROLE_LABELS

    def _sse(text: str) -> str:
        return f"data: {json.dumps({'type': 'answer_chunk', 'content': text})}\n\n"

    ma_cfg = _load_ma_config()
    d_cfg    = ma_cfg.get("dispatcher", {})
    d_preset = d_cfg.get("preset_id", _provider_config.get("preset_id", _provider_config["type"]))
    d_model  = d_cfg.get("model", _provider_config["model"])
    d_client = _make_async_client_for(d_preset)

    try:
        if resume_job_id:
            # ---- Phase 2: ユーザー返答を解釈 ----
            job_id = resume_job_id
            # ジョブディレクトリを探す（スコープ配下 or ルート）
            base_dir = (ALLOWED_WORK_DIR / workspace_scope) if workspace_scope else ALLOWED_WORK_DIR
            job_dir = base_dir / "jobs" / job_id
            if not (job_dir / "plan.json").exists():
                # スコープが異なる場合は全スコープを探す
                candidates = list(ALLOWED_WORK_DIR.glob(f"*/jobs/{job_id}")) + [ALLOWED_WORK_DIR / "jobs" / job_id]
                job_dir = next((c for c in candidates if (c / "plan.json").exists()), job_dir)

            plan_file = job_dir / "plan.json"
            if not plan_file.exists():
                yield _sse("❌ ジョブが見つかりません。\n")
                yield f"data: {json.dumps({'type': 'answer_done'})}\n\n"
                return

            plan = json.loads(plan_file.read_text(encoding="utf-8"))
            original_task_file = job_dir / "original_task.txt"
            original_task = original_task_file.read_text(encoding="utf-8") if original_task_file.exists() else user_message

            action_result = await _interpret_plan_response(user_message, plan, d_client, d_model)
            action = action_result.get("action", "execute")
            notes  = action_result.get("notes", "")

            if action == "cancel":
                yield _sse("🚫 了解しました、キャンセルします。\n")
                yield f"data: {json.dumps({'type': 'answer_done'})}\n\n"
                return

            if action == "replan":
                yield _sse("🔄 計画を修正中...\n\n")
                revised_message = original_task + (f"\n\n【修正指示】{notes}" if notes else "")
                plan = await dispatch_task(revised_message, d_client, d_model, job_dir)
                plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

                roles = plan.get("roles", [])
                tasks = plan.get("tasks", {})
                roles_str = " / ".join(AGENT_ROLE_LABELS.get(r, r) for r in roles)
                yield _sse(f"  → 修正後の役割: {roles_str}\n\n")
                for role in roles:
                    task = tasks.get(role, {})
                    label = AGENT_ROLE_LABELS.get(role, role)
                    t_sec = task.get("timeout_sec", "")
                    yield _sse(f"- **{label}**: {task.get('prompt', '')[:80]}" + (f" (最大{t_sec}秒)" if t_sec else "") + "\n")
                yield _sse("\nこの内容でよろしいですか？\n")
                yield f"data: {json.dumps({'type': 'plan_ready', 'job_id': job_id, 'roles': roles})}\n\n"
                yield f"data: {json.dumps({'type': 'answer_done'})}\n\n"
                return

            # action == "execute": エージェント実行フェーズへ
            yield _sse(f"▶ **実行開始** (job: `{job_id}`)\n\n")

        else:
            # ---- Phase 1: 新規ジョブ → 計画表示 → 停止 ----
            job_id = new_job_id()
            base_dir = (ALLOWED_WORK_DIR / workspace_scope) if workspace_scope else ALLOWED_WORK_DIR
            job_dir = base_dir / "jobs" / job_id
            job_dir.mkdir(parents=True, exist_ok=True)

            yield _sse(f"🤖 **マルチエージェントモード** (job: `{job_id}`)\n\n")
            yield _sse("📋 タスク分解中...\n")

            plan = await dispatch_task(user_message, d_client, d_model, job_dir)
            (job_dir / "original_task.txt").write_text(user_message, encoding="utf-8")
            (job_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

            roles = plan.get("roles", [])
            tasks = plan.get("tasks", {})
            roles_str = " / ".join(AGENT_ROLE_LABELS.get(r, r) for r in roles)
            yield _sse(f"  → 役割: {roles_str}\n\n")
            for role in roles:
                task = tasks.get(role, {})
                label = AGENT_ROLE_LABELS.get(role, role)
                t_sec = task.get("timeout_sec", "")
                yield _sse(f"- **{label}**: {task.get('prompt', '')[:80]}" + (f" (最大{t_sec}秒)" if t_sec else "") + "\n")
            yield _sse("\nこの流れで実行してよいですか？ 役割の追加・変更があればお知らせください。\n")
            yield f"data: {json.dumps({'type': 'plan_ready', 'job_id': job_id, 'roles': roles})}\n\n"
            yield f"data: {json.dumps({'type': 'answer_done'})}\n\n"
            return

        # ---- エージェント実行フェーズ（Phase 2 execute） ----
        roles = plan.get("roles", [])
        tasks = plan.get("tasks", {})

        for role in roles:
            task        = tasks.get(role, {})
            task_prompt = task.get("prompt", user_message)
            label       = AGENT_ROLE_LABELS.get(role, role)
            role_cfg    = ma_cfg.get(role, d_cfg)
            preset_id   = task.get("preset_id") or role_cfg.get("preset_id", d_preset)
            model       = task.get("model")     or role_cfg.get("model", d_model)
            sub_client  = _make_async_client_for(preset_id)

            yield _sse(f"🎯 **[{label}]** 開始... (`{preset_id}` / `{model}`)\n")
            system_prompt = get_agent_system_prompt(role, str(job_dir))
            try:
                await run_sub_agent(
                    role=role,
                    system_prompt=system_prompt,
                    task_prompt=task_prompt,
                    all_tools=TOOLS,
                    execute_tool_fn=execute_tool_async,
                    async_client=sub_client,
                    model=model,
                    job_dir=job_dir,
                    timeout_sec=task.get("timeout_sec"),
                )
                yield _sse(f"  → 完了 ✅\n\n")
            except Exception as e:
                yield _sse(f"  → エラー ⚠️ `{type(e).__name__}: {e}`\n\n")
                print(f"[multi_agent] {role} エラー: {e}")

        yield _sse("📄 最終報告書を生成中...\n\n")
        report = await generate_final_report(d_client, d_model, job_dir)
        yield _sse(f"---\n\n{report}\n\n")
        scope_path = f"workspace/{workspace_scope}/jobs/{job_id}" if workspace_scope else f"workspace/jobs/{job_id}"
        yield _sse(f"\n📁 成果物: `{scope_path}/`\n")

    except Exception as e:
        import traceback
        yield _sse(f"❌ マルチエージェントエラー: `{type(e).__name__}: {e}`")
        print(traceback.format_exc())

    yield f"data: {json.dumps({'type': 'answer_done'})}\n\n"


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


def _is_recent_head_unsafe(msgs: list) -> bool:
    """recent_part の先頭が孤立するメッセージかどうかを判定"""
    if not msgs:
        return False
    head = msgs[0]
    if head.get("role") == "tool":
        return True
    if head.get("role") == "assistant" and head.get("tool_calls"):
        return True
    return False


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


_NOTIFY_KEYWORDS = ["メールして", "メールで教えて", "メールで知らせて", "メールで報告", "メールで通知", "終わったら知らせて", "終わったら教えて", "完了したらメール", "通知して"]

async def _agent_stream_inner(user_message: str, history: list, images: list = None, bypass_approval: bool = False, no_think: bool = False, workspace_scope: str = ""):
    _notify_on_done = any(kw in user_message for kw in _NOTIFY_KEYWORDS)
    trimmed = history[-MAX_HISTORY_MESSAGES:] if len(history) > MAX_HISTORY_MESSAGES else history
    trimmed = _sanitize_history(trimmed)

    # ローリングサマリー: 古い部分を圧縮して文脈を維持
    compressed_history = None
    if len(trimmed) > SUMMARY_TRIGGER:
        old_part = trimmed[:-SUMMARY_KEEP_RECENT]
        recent_part = trimmed[-SUMMARY_KEEP_RECENT:]
        while _is_recent_head_unsafe(recent_part) and old_part:
            recent_part = [old_part[-1]] + recent_part
            old_part = old_part[:-1]
        summary = await asyncio.to_thread(_summarize_history, old_part)
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
    if no_think and isinstance(user_content, str):
        user_content = f"/no_think\n{user_content}"
    if bypass_approval and isinstance(user_content, str):
        user_content = f"[承認バイパスON: 確認・提案なしで即実行すること]\n{user_content}"
    # 自動コンテキスト収集（Claude Code方式: git status/diff/log をユーザーメッセージ先頭に注入）
    auto_ctx = _gather_auto_context(workspace_scope)
    if auto_ctx:
        if isinstance(user_content, list):
            user_content = [{"type": "text", "text": auto_ctx}] + user_content
        else:
            user_content = f"{auto_ctx}\n\n{user_content}"
    system_prompt = get_system_prompt(bypass_approval)
    if workspace_scope:
        system_prompt += f"\n\n## 作業ディレクトリ制限\n現在のセッションでは **workspace/{workspace_scope}/** のみを操作すること。このフォルダ外のファイルを読み書き・移動・削除してはならない。work_dir 指定時も必ず workspace/{workspace_scope}/ 以下のパスを使うこと。"
    if "5.4-mini" in _provider_config.get("model", ""):
        system_prompt += "\n\n絶対に同じ文章・段落を繰り返すな。一度出力した内容は再出力禁止。"
    messages = [{"role": "system", "content": system_prompt}] + trimmed + [{"role": "user", "content": user_content}]
    turn_messages = []  # このターンで追加されたメッセージ (tool関連)

    # サマリー圧縮が発生した場合はクライアントに通知（localStorage 更新のため）
    if compressed_history is not None:
        yield f"data: {json.dumps({'type': 'history_compressed', 'messages': compressed_history})}\n\n"

    is_local = _provider_config["type"] not in ("azure", "foundry", "gemini")
    tools_enabled = _provider_config.get("tools_enabled", not is_local)

    max_iterations = 30
    iteration = 0
    while iteration < max_iterations:
        iteration += 1
        # ローカルモデルは role:tool を Jinja テンプレートで処理できない場合があるため変換
        send_messages = _convert_messages_for_local(messages) if (is_local and not tools_enabled) else messages
        # tools_enabled=False 時はツールを渡さない（ローカルモデルのデフォルト）
        # tools_enabled=True に手動設定した場合はローカルモデルでもツールを渡す
        # ⚠️ ローカルモデルへのtools渡しは慎重に: Qwen3等はJinjaテンプレート問題で暴走する場合がある
        create_kwargs = dict(model=_provider_config["model"], messages=send_messages, stream=True)
        if tools_enabled:
            create_kwargs["tools"] = TOOLS
            create_kwargs["tool_choice"] = "auto"
        create_kwargs["stream_options"] = {"include_usage": True}
        if _provider_config.get("model", "") == "gpt-5-mini":
            create_kwargs["reasoning_effort"] = "low"
        stream = await _make_async_client().chat.completions.create(**create_kwargs)

        content_parts = []
        tool_calls_map = {}  # index -> {id, name, arguments}

        async for chunk in stream:
            # トークン使用量（最終chunk）
            if chunk.usage:
                yield f"data: {json.dumps({'type': 'token_usage', 'prompt': chunk.usage.prompt_tokens, 'completion': chunk.usage.completion_tokens, 'total': chunk.usage.total_tokens})}\n\n"
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # reasoning_content（思考トークン）はスキップ
            reasoning = getattr(delta, 'reasoning_content', None)
            if reasoning:
                continue

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
            yield f"data: {json.dumps({'type': 'answer_done', 'model': _provider_config['model']})}\n\n"
            if _notify_on_done:
                send_email_notification("✅ エージェント処理完了", final_answer[:500])
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

        # tool_calls を解析
        parsed_calls = [
            (tc["function"]["name"], json.loads(tc["function"]["arguments"] or "{}"), tc["id"])
            for tc in tool_calls_list
        ]

        # Deep Research 設定時に web_research が複数呼ばれた場合は最初の1件のみに制限（二重課金防止）
        if WEB_RESEARCH_PROVIDER.startswith("deep-research"):
            _dr_seen = False
            _filtered = []
            for _call in parsed_calls:
                if _call[0] == "web_research":
                    if not _dr_seen:
                        _filtered.append(_call)
                        _dr_seen = True
                    else:
                        # 2件目以降はスキップ（ダミー結果を後で返す）
                        _filtered.append((_call[0], _call[1], _call[2] + "__skipped__"))
                else:
                    _filtered.append(_call)
            parsed_calls = _filtered

        # tool_start イベントを全件先に送信
        for name, args, tc_id in parsed_calls:
            if tc_id.endswith("__skipped__"):
                continue
            yield f"data: {json.dumps({'type': 'tool_start', 'name': name, 'args': args})}\n\n"

        # 画像ツール・一括清書ダイアログにワークスペーススコープを注入（保存先をスコープ配下にするため）
        _SCOPE_TOOLS = {"generate_image", "edit_image", "watermark_image", "show_mermaid_batch_refine_dialog"}
        for _i, (_name, _args, _) in enumerate(parsed_calls):
            if _name in _SCOPE_TOOLS and workspace_scope:
                _args["_workspace_scope"] = workspace_scope

        # ツールを実行（run_commandはストリーミング、他は並列）
        _STREAMING_TOOLS = {"run_command"}
        _DR_SKIP_MSG = json.dumps({"error": "Deep Research の同時複数呼び出しは禁止されています。1ターンに1回のみ呼び出してください。"}, ensure_ascii=False)
        if any(name in _STREAMING_TOOLS for name, _, _ in parsed_calls):
            # ストリーミングツールが含まれる場合は順次実行
            results = []
            for name, args, tc_id in parsed_calls:
                if tc_id.endswith("__skipped__"):
                    results.append(_DR_SKIP_MSG)
                elif name in _STREAMING_TOOLS:
                    result_str = None
                    async for chunk in _stream_command(args):
                        if chunk["type"] == "line":
                            yield f"data: {json.dumps({'type': 'tool_stdout', 'line': chunk['line'], 'tool_id': tc_id})}\n\n"
                        elif chunk["type"] == "result":
                            result_str = chunk["result"]
                    results.append(result_str or json.dumps({"error": "ストリーミング結果なし", "stdout": "", "stderr": "", "returncode": -1}))
                else:
                    # 長時間ツール実行中は30秒ごとにSSEキープアライブを送ってブラウザ接続を維持
                    task = asyncio.create_task(execute_tool_async(name, args))
                    while True:
                        done, pending = await asyncio.wait({task}, timeout=30)
                        if not pending:
                            break
                        yield f": keepalive\n\n"
                    results.append(task.result())
        else:
            # ストリーミング不要ツールは並列実行（スキップ済みはダミー結果）
            # 長時間ツール実行中は30秒ごとにSSEキープアライブを送ってブラウザ接続を維持
            async def _skipped(_msg=_DR_SKIP_MSG):
                return _msg
            _tasks = [
                asyncio.create_task(_skipped() if tc_id.endswith("__skipped__") else execute_tool_async(name, args))
                for name, args, tc_id in parsed_calls
            ]
            while True:
                done, pending = await asyncio.wait(_tasks, timeout=30)
                if not pending:
                    break
                yield f": keepalive\n\n"
            results = [t.result() for t in _tasks]

        # 結果を順番に処理してメッセージ履歴に追加
        pending_vision_images = []  # render_manim の画像をまとめてvision messageに注入するためのキュー
        for (name, args, tc_id), result in zip(parsed_calls, results):
            # Deep Research スキップ済みの呼び出しはtool_idの__skipped__サフィックスを除去してLLMに返す
            real_tc_id = tc_id.replace("__skipped__", "") if tc_id.endswith("__skipped__") else tc_id
            tool_result_for_msg = result  # LLM に渡す tool メッセージの内容

            # web_research で Deep Research レポートが返った場合はUIに直接表示（AIに要約させない）
            if name == "web_research":
                try:
                    result_data = json.loads(result)
                    report = result_data.get("report", "")
                    if report:
                        backend = result_data.get("search_backend", "")
                        query = result_data.get("query", args.get("query", ""))
                        saved_filename = result_data.get("saved_filename", "")
                        yield f"data: {json.dumps({'type': 'deep_research_report', 'report': report, 'query': query, 'backend': backend}, ensure_ascii=False)}\n\n"
                        tool_result_for_msg = json.dumps({
                            "query": query,
                            "report": report,
                            "report_displayed": True,
                            "search_backend": backend,
                            "saved_filename": saved_filename,
                            "note": (
                                "レポート全文はチャットUIに直接表示済みです。"
                                "チャット回答ではレポートをそのまま繰り返さないこと。"
                                f"レポートは '{saved_filename}' として workspace/ に自動保存しました。必ずこのファイル名を回答に含めること。"
                                "チャット回答では調査結果の重要ポイントを端折らず十分な分量で要約すること（箇条書き・見出しを使って分かりやすく）。"
                            ),
                        }, ensure_ascii=False)
                except Exception:
                    pass

            # todo_update の場合はUIにタスクリストを即時反映
            if name == "todo_update":
                try:
                    result_data = json.loads(result)
                    if "todos" in result_data:
                        yield f"data: {json.dumps({'type': 'todo_update', 'todos': result_data['todos']})}\n\n"
                except Exception:
                    pass

            # workspace_cleanup_preview の場合はUIに削除確認モーダルを表示
            if name == "workspace_cleanup_preview":
                try:
                    result_data = json.loads(result)
                    if "to_delete" in result_data:
                        yield f"data: {json.dumps({'type': 'cleanup_preview', 'data': result_data})}\n\n"
                except Exception:
                    pass

            # list_ansible_playbooks: プレイブック選択UIをSSEで送信
            if name == "list_ansible_playbooks":
                try:
                    result_data = json.loads(result)
                    if "playbooks" in result_data:
                        yield f"data: {json.dumps({'type': 'ansible_chooser', 'playbooks': result_data['playbooks'], 'creds_exists': result_data.get('creds_exists', False), 'creds_filled': result_data.get('creds_filled', False)})}\n\n"
                except Exception:
                    pass

            # show_mermaid_batch_refine_dialog: 一括清書ダイアログをSSEで送信
            if name == "show_mermaid_batch_refine_dialog":
                try:
                    result_data = json.loads(result)
                    if result_data.get("trigger_ui") == "mermaid_batch_refine":
                        payload = json.dumps({'type': 'mermaid_batch_refine', 'file_path': result_data['file_path'], 'workspace_scope': result_data.get('workspace_scope', ''), 'blocks': result_data['blocks'], 'count': result_data['count']})
                        yield f"data: {payload}\n\n"
                except Exception:
                    pass

            # generate_image / edit_image / watermark_image: 画像をUIに送信（base64をLLM履歴から除去）
            if name in ("generate_image", "edit_image", "watermark_image"):
                try:
                    result_data = json.loads(result)
                    if result_data.get("image_base64"):
                        # generate_image / edit_image の場合は自動ウォーターマークを適用
                        display_b64 = result_data["image_base64"]
                        if name in ("generate_image", "edit_image"):
                            wm_b64, wm_path = apply_auto_watermark(display_b64, workspace_scope)
                            if wm_b64 != display_b64:
                                display_b64 = wm_b64
                                result_data["image_base64"] = wm_b64
                                if wm_path:
                                    result_data["saved_path"] = wm_path
                        yield f"data: {json.dumps({'type': 'image_generated', 'image': display_b64, 'mime': result_data.get('mime', 'image/png'), 'prompt': result_data.get('prompt', ''), 'provider': result_data.get('provider', ''), 'model': result_data.get('model', '')})}\n\n"
                        tool_result_for_msg = json.dumps({
                            "message": result_data.get("message", "画像を生成しました"),
                            "prompt": result_data.get("prompt"),
                            "provider": result_data.get("provider"),
                            "model": result_data.get("model"),
                            "saved_path": result_data.get("saved_path"),
                            "note": "生成画像はUIに表示済みです",
                        }, ensure_ascii=False)
                except Exception:
                    pass

            # MCP ツールが画像レスポンスを返した場合: UIに表示してLLM履歴を軽量化
            if "__" in name:
                try:
                    result_data = json.loads(result)
                    if result_data.get("image_base64"):
                        # mcp_client.py が ImageContent を検出してbase64化した場合
                        yield f"data: {json.dumps({'type': 'image_generated', 'image': result_data['image_base64'], 'mime': result_data.get('mime', 'image/png'), 'prompt': name, 'provider': 'mcp', 'model': ''})}\n\n"
                        tool_result_for_msg = json.dumps({
                            "message": "スクリーンショットを撮影しました",
                            "saved_path": result_data.get("saved_path", ""),
                            "text": result_data.get("text", ""),
                            "note": "画像はUIに表示済みです",
                        }, ensure_ascii=False)
                except Exception:
                    pass
                # Playwright MCP はテキスト結果でファイルパスを返すため、スクリーンショットファイルを検出して表示
                if "take_screenshot" in name or "screenshot" in name.lower():
                    import re as _re
                    import base64 as _b64
                    import shutil as _shutil
                    _img_match = _re.search(r'\]\(([^)]+\.(?:png|jpg|jpeg|webp|gif))\)', result)
                    if _img_match:
                        _img_rel = _img_match.group(1).lstrip("./")
                        _img_abs = ALLOWED_WORK_DIR.parent / _img_rel
                        if not _img_abs.exists():
                            _img_abs = ALLOWED_WORK_DIR / _img_rel
                        if _img_abs.exists():
                            try:
                                _ss_dir = ALLOWED_WORK_DIR / "playwright-screenshots"
                                _ss_dir.mkdir(parents=True, exist_ok=True)
                                _dest = _ss_dir / _img_abs.name
                                if _img_abs.resolve() != _dest.resolve():
                                    _shutil.move(str(_img_abs), str(_dest))
                                _ext = _dest.suffix.lower().lstrip(".")
                                _mime = f"image/{'jpeg' if _ext == 'jpg' else _ext}"
                                _b64_data = _b64.b64encode(_dest.read_bytes()).decode()
                                yield f"data: {json.dumps({'type': 'image_generated', 'image': _b64_data, 'mime': _mime, 'prompt': name, 'provider': 'mcp', 'model': ''})}\n\n"
                            except Exception:
                                pass

            # render_manim: 画像をUIに送信 + vision message 注入のためにキュー
            if name == "render_manim":
                try:
                    result_data = json.loads(result)
                    if result_data.get("rendered") and result_data.get("image_base64"):
                        # UIに画像を表示（base64をそのまま送信）
                        yield f"data: {json.dumps({'type': 'manim_render', 'image': result_data['image_base64'], 'mime': result_data.get('mime', 'image/png'), 'scene': result_data.get('scene_name', '')})}\n\n"
                        # vision message 注入用にキュー
                        pending_vision_images.append({
                            "base64": result_data["image_base64"],
                            "mime": result_data.get("mime", "image/png"),
                        })
                        # tool message から base64 を除去（巨大なデータをLLM履歴に入れない）
                        tool_result_for_msg = json.dumps({
                            "rendered": True,
                            "scene_name": result_data.get("scene_name"),
                            "message": result_data.get("message"),
                            "stdout": result_data.get("stdout", ""),
                            "stderr": result_data.get("stderr", ""),
                            "note": "レンダリング画像は次のユーザーメッセージ（vision）で提供されます",
                        }, ensure_ascii=False)
                except Exception:
                    pass

            yield f"data: {json.dumps({'type': 'tool_result', 'result': tool_result_for_msg})}\n\n"

            tool_msg = {
                "role": "tool",
                "tool_call_id": real_tc_id,
                "content": tool_result_for_msg,
            }
            messages.append(tool_msg)
            turn_messages.append(tool_msg)

        # render_manim で画像があれば vision user message を注入
        # LLM が次のターンで画像を見て自己評価・修正できるようにする
        if pending_vision_images:
            vision_content = [
                {"type": "text", "text": "以下がレンダリング結果の画像です。見た目を確認し、問題があれば改善してください。"}
            ]
            for img in pending_vision_images:
                vision_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img['mime']};base64,{img['base64']}"},
                })
            vision_msg = {"role": "user", "content": vision_content}
            messages.append(vision_msg)
            turn_messages.append(vision_msg)

    # ループ上限超過（無限ループ防止）
    if iteration >= max_iterations:
        msg = f"[ツール呼び出しが{max_iterations}回に達したため停止しました]"
        turn_messages.append({"role": "assistant", "content": msg})
        yield f"data: {json.dumps({'type': 'answer_chunk', 'content': msg})}\n\n"
        yield f"data: {json.dumps({'type': 'history_messages', 'messages': turn_messages})}\n\n"
        yield f"data: {json.dumps({'type': 'answer_done', 'model': _provider_config['model']})}\n\n"


@app.post("/chat")
async def chat(req: ChatRequest):
    if req.multi_agent or req.resume_job_id:
        stream = multi_agent_stream(req.message, req.agent_mode, req.workspace_scope, req.resume_job_id)
    else:
        stream = agent_stream(req.message, req.history, req.images, req.bypass_approval, req.no_think, req.workspace_scope)
    return StreamingResponse(stream, media_type="text/event-stream")


# -----------------------------------------------------------------------
# BG Task Classifier
# -----------------------------------------------------------------------

class ClassifyBgRequest(BaseModel):
    message: str

@app.post("/classify-bg")
async def classify_bg(req: ClassifyBgRequest):
    """Quick LLM call: judge whether this task needs background execution."""
    system = (
        "あなたはAIエージェントのタスク分類器です。"
        "ユーザーの依頼が「長時間の自律タスク"
        "（Web調査・複数ファイル生成・大規模コード作成・長い実行処理・レポート作成など）」か"
        "「短時間で終わる質問・確認・簡単な1ステップ操作」かを判定してください。"
        "JSONのみ返してください: {\"bg\": true | false, \"reason\": \"判断理由15字以内\"}"
    )
    try:
        client = _make_async_client()
        resp = await client.chat.completions.create(
            model=_provider_config["model"],
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": req.message[:500]},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=60,
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content or '{"bg":false,"reason":""}')
        return JSONResponse({"bg": bool(data.get("bg")), "reason": data.get("reason", "")})
    except Exception:
        return JSONResponse({"bg": False, "reason": ""})


# -----------------------------------------------------------------------
# Async Agent Job API
# -----------------------------------------------------------------------

class AsyncJobRequest(BaseModel):
    message: str
    max_turns: int = 30


@app.post("/async-agent/jobs")
async def async_job_create(req: AsyncJobRequest):
    """Register a background agent job. Returns job_id immediately."""
    job_id = _create_async_job(
        message=req.message,
        provider_config=dict(_provider_config),
        max_turns=req.max_turns,
    )
    return JSONResponse({"job_id": job_id, "status": "pending"})


@app.get("/async-agent/jobs")
async def async_job_list():
    """List all jobs (newest first)."""
    return JSONResponse({"jobs": _list_async_jobs()})


@app.get("/async-agent/jobs/{job_id}")
async def async_job_get(job_id: str, after_seq: int = -1):
    """Get job status + chunks since after_seq (for polling)."""
    job = _get_async_job(job_id)
    if job is None:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    chunks = _get_async_chunks(job_id, after_seq)
    return JSONResponse({**job, "chunks": chunks})


@app.get("/async-agent/jobs/{job_id}/stream")
async def async_job_stream(job_id: str, after_seq: int = -1):
    """SSE stream of job output. Supports reconnection via after_seq."""
    async def _gen():
        job = _get_async_job(job_id)
        if job is None:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Job not found'})}\n\n"
            return

        seq = after_seq
        while True:
            chunks = await asyncio.to_thread(_get_async_chunks, job_id, seq)
            for c in chunks:
                yield f"data: {json.dumps({'type': c['type'], 'content': c['content'], 'seq': c['seq']})}\n\n"
                seq = c["seq"]

            job = await asyncio.to_thread(_get_async_job, job_id)
            if job and job["status"] in ("done", "failed", "cancelled"):
                yield f"data: {json.dumps({'type': 'job_end', 'status': job['status']})}\n\n"
                break

            yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(_gen(), media_type="text/event-stream")


@app.delete("/async-agent/jobs/{job_id}")
async def async_job_cancel(job_id: str):
    """Cancel a pending or running job."""
    job = _get_async_job(job_id)
    if job is None:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    if job["status"] not in ("pending", "running"):
        return JSONResponse({"error": f"Cannot cancel job with status: {job['status']}"}, status_code=400)
    _update_async_job(job_id, status="cancelling")
    return JSONResponse({"job_id": job_id, "status": "cancelling"})


@app.delete("/async-agent/jobs/{job_id}/delete")
async def async_job_delete(job_id: str):
    """Permanently delete a completed/failed/cancelled job."""
    job = _get_async_job(job_id)
    if job is None:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    if job["status"] in ("pending", "running"):
        return JSONResponse({"error": "Cannot delete an active job. Cancel it first."}, status_code=400)
    _delete_async_job(job_id)
    return JSONResponse({"job_id": job_id, "deleted": True})


@app.get("/async-agent/worker/status")
async def async_worker_status():
    """Check if the worker process is alive."""
    alive = _async_worker_proc.poll() is None
    return JSONResponse({"alive": alive, "pid": _async_worker_proc.pid})


@app.post("/mermaid-check")
async def mermaid_check(req: MermaidCheckRequest):
    """Mermaid図のレイアウトをAIビジョンでチェックし、修正コードがあれば返す"""
    import re as _re
    if req.user_note:
        system_prompt = (
            "あなたはMermaidダイアグラムの修正アシスタントです。"
            "ユーザーから指摘された問題点を踏まえて、MermaidコードとPNG画像を確認し、修正したMermaidコードを```mermaidブロックで返してください。"
            "余分な説明は不要です。修正コードのみ返してください。"
        )
    else:
        system_prompt = (
            "あなたはMermaidダイアグラムの品質チェッカーです。"
            "提示されたMermaidコードと、そのコードから生成した図の画像を確認してください。"
            "以下の問題がある場合のみ、修正したMermaidコードを```mermaidブロックで返してください:\n"
            "- ノード名・テキストの文字被り・重なり\n"
            "- 矢印や接続線の重なり・交差\n"
            "- 読みにくいレイアウト\n"
            "問題がなければ「問題なし」とだけ答えてください。余分な説明は不要です。"
        )
    note_text = f"\n\nユーザーからの指摘: {req.user_note}" if req.user_note else ""
    user_content = [
        {"type": "text", "text": f"Mermaidコード:\n```mermaid\n{req.code}\n```\n\n上記コードが生成した図の画像を確認し、レイアウトの問題をチェックしてください。{note_text}"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{req.image}"}},
    ]
    try:
        client = _make_async_client()
        resp = await client.chat.completions.create(
            model=_provider_config["model"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_completion_tokens=1000,
            temperature=0,
        )
        content = resp.choices[0].message.content or ""
        m = _re.search(r'```mermaid\s*([\s\S]*?)```', content)
        fixed_code = m.group(1).strip() if m else None
        return JSONResponse({"fixed_code": fixed_code, "message": content})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/mermaid-refine")
async def mermaid_refine(req: MermaidRefineRequest):
    """MermaidのPNGをgpt-image-2で清書する（img2img）"""
    from tools.image_tools import _make_client, _b64_from_response, _save_to_workspace
    from config import IMAGE_PROVIDER, IMAGE_MODEL, IMAGE_SIZE, ALLOWED_WORK_DIR
    import io as _io, base64 as _b64lib
    try:
        client = _make_client(IMAGE_PROVIDER)
        if req.source_path:
            # 再清書：workspace内の保存済みファイルから読み込む
            target = (ALLOWED_WORK_DIR / req.source_path).resolve()
            if not str(target).startswith(str(ALLOWED_WORK_DIR)):
                return JSONResponse({"error": "作業ディレクトリ外のファイルへのアクセスは禁止"}, status_code=400)
            if not target.exists():
                return JSONResponse({"error": f"ファイルが見つかりません: {req.source_path}"}, status_code=404)
            img_bytes = target.read_bytes()
        else:
            img_bytes = _b64lib.b64decode(req.image)
        buf = _io.BytesIO(img_bytes)
        kwargs: dict = dict(
            model=IMAGE_MODEL,
            image=("mermaid.png", buf, "image/png"),
            prompt=req.prompt,
            n=1,
        )
        sz = IMAGE_SIZE or "1024x1024"
        if sz and sz != "auto":
            kwargs["size"] = sz
        resp = await asyncio.to_thread(lambda: client.images.edit(**kwargs))
        b64 = _b64_from_response(resp.data[0])
        saved_path = _save_to_workspace(b64, "mermaid_refined", req.workspace_scope)
        return JSONResponse({"image": b64, "mime": "image/png", "saved_path": saved_path, "model": IMAGE_MODEL})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/mermaid-batch-replace")
async def mermaid_batch_replace(req: MermaidBatchReplaceRequest):
    """MDファイルのMermaidブロックを清書済み画像参照（Markdown形式）に差し替える"""
    import re as _re
    from config import ALLOWED_WORK_DIR
    target = (ALLOWED_WORK_DIR / req.file_path).resolve()
    if not str(target).startswith(str(ALLOWED_WORK_DIR)):
        return JSONResponse({"error": "作業ディレクトリ外のファイルへのアクセスは禁止"}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": f"ファイルが見つかりません: {req.file_path}"}, status_code=404)
    import os as _os
    content = target.read_text(encoding="utf-8")
    matches = list(_re.finditer(r'```mermaid\n.*?\n```', content, _re.DOTALL))
    # MDファイルのディレクトリ（saved_pathからの相対パス計算用）
    md_dir = _os.path.dirname(req.file_path) or "."
    # 後ろから差し替えることで前方の位置ずれを防ぐ
    sorted_reps = sorted(req.replacements, key=lambda x: x.get("block_index", 0), reverse=True)
    replaced = 0
    for rep in sorted_reps:
        idx = rep.get("block_index", -1)
        saved_path = rep.get("saved_path", "")
        if idx < 0 or idx >= len(matches) or not saved_path:
            continue
        m = matches[idx]
        fig_label = f"図{idx + 1}"
        # MDファイルからの相対パスに変換（Markdown画像参照用）
        rel_img_path = _os.path.relpath(saved_path, md_dir).replace("\\", "/")
        img_ref = f"![{fig_label}]({rel_img_path})"
        content = content[:m.start()] + img_ref + content[m.end():]
        replaced += 1
    target.write_text(content, encoding="utf-8")
    return JSONResponse({"ok": True, "replaced": replaced, "total": len(req.replacements)})


@app.post("/convert-to-docx")
async def convert_to_docx(req: ConvertToDocxRequest):
    """MDファイルをWordのDOCX形式に変換する（PowerShell + Word COM経由）"""
    import subprocess as _sp, tempfile as _tmp, re as _re
    from config import ALLOWED_WORK_DIR
    from markdown_it import MarkdownIt

    target = (ALLOWED_WORK_DIR / req.file_path).resolve()
    if not str(target).startswith(str(ALLOWED_WORK_DIR)):
        return JSONResponse({"error": "作業ディレクトリ外のファイルへのアクセスは禁止"}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": f"ファイルが見つかりません: {req.file_path}"}, status_code=404)

    md_content = target.read_text(encoding="utf-8")

    # A4 ページ本文幅（2cm余白）の実効ピクセル数（96dpi換算）
    PAGE_WIDTH_PX = 540

    def _wslpath_to_windows(wsl_path: str) -> str:
        try:
            r = _sp.run(["wslpath", "-w", wsl_path], capture_output=True, text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else wsl_path
        except Exception:
            return wsl_path

    def _img_tag(alt: str, abs_path, hint_ratio: float = None) -> str:
        """アスペクト比を保ちページ幅に収まる img タグを生成する"""
        from PIL import Image as _Img
        win_path = _wslpath_to_windows(str(abs_path))
        try:
            with _Img.open(str(abs_path)) as im:
                orig_w, orig_h = im.size
            if hint_ratio:
                # パイプ記法で比率指定がある場合は横幅に適用
                disp_w = int(PAGE_WIDTH_PX * hint_ratio)
            else:
                disp_w = min(orig_w, PAGE_WIDTH_PX)
            disp_h = int(orig_h * disp_w / orig_w) if orig_w else disp_w
            return f'<img alt="{alt}" src="{win_path}" width="{disp_w}" height="{disp_h}">'
        except Exception:
            # 画像を開けない場合は幅だけ指定（アスペクト比は Word に任せる）
            return f'<img alt="{alt}" src="{win_path}" style="max-width:{PAGE_WIDTH_PX}px;height:auto">'

    # パイプ記法 ![alt|80%](path) を img タグに変換
    def _pipe_to_img(text):
        def _replace(m):
            inner, path = m.group(1), m.group(2)
            if path.startswith("http://") or path.startswith("https://"):
                return m.group(0)
            abs_path = (target.parent / path).resolve()
            if "|" in inner:
                alt, size = inner.rsplit("|", 1)
                size = size.strip()
                ratio = float(size.rstrip("%")) / 100 if size.endswith("%") else None
                return _img_tag(alt.strip(), abs_path, ratio)
            return _img_tag(inner.strip(), abs_path)
        return _re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', _replace, text)

    preprocessed = _pipe_to_img(md_content)
    md_it = MarkdownIt()
    html_body = md_it.render(preprocessed)

    # markdown-it が残した通常の src="相対パス" を絶対 Windows パスに変換
    def _resolve_img_src(m):
        src = m.group(1)
        if src.startswith("http://") or src.startswith("https://") or src.startswith("\\\\"):
            return m.group(0)
        abs_path = (target.parent / src).resolve()
        win_path = _wslpath_to_windows(str(abs_path))
        return f'src="{win_path}"'
    html_body = _re.sub(r'src="([^"]+)"', _resolve_img_src, html_body)

    # img タグに width/height が未設定のものに実寸ベースのサイズを付与
    def _fix_img_size(m):
        tag = m.group(0)
        if 'width=' in tag or 'style=' in tag:
            return tag
        src_m = _re.search(r'src="([^"]+)"', tag)
        if not src_m:
            return tag
        win_path = src_m.group(1)
        try:
            from PIL import Image as _Img
            # Windows UNC パス → WSL パスに戻して開く
            r = _sp.run(["wslpath", "-u", win_path], capture_output=True, text=True, timeout=5)
            wsl_path = r.stdout.strip()
            with _Img.open(wsl_path) as im:
                orig_w, orig_h = im.size
            disp_w = min(orig_w, PAGE_WIDTH_PX)
            disp_h = int(orig_h * disp_w / orig_w)
            return tag.rstrip('>').rstrip('/') + f' width="{disp_w}" height="{disp_h}">'
        except Exception:
            return tag
    html_body = _re.sub(r'<img[^>]+>', _fix_img_size, html_body)

    html_full = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: "Meiryo", "Yu Gothic", sans-serif; font-size: 11pt; line-height: 1.6; margin: 2cm; }}
h1 {{ font-size: 18pt; }} h2 {{ font-size: 15pt; }} h3 {{ font-size: 13pt; }}
table {{ border-collapse: collapse; width: 100%; }} td, th {{ border: 1px solid #ccc; padding: 4px 8px; }}
img {{ max-width: 100%; }}
code {{ background: #f4f4f4; padding: 2px 4px; font-family: Consolas, monospace; }}
pre {{ background: #f4f4f4; padding: 8px; overflow-x: auto; }}
</style></head><body>{html_body}</body></html>"""

    # 一時HTMLファイルをworkspaceに保存
    html_path = target.with_suffix(".tmp_conv.html")
    html_path.write_text(html_full, encoding="utf-8")
    docx_path = target.with_suffix(".docx")

    try:
        html_win = _wslpath_to_windows(str(html_path))
        docx_win = _wslpath_to_windows(str(docx_path))

        ps_script = f"""
$word = New-Object -ComObject Word.Application
$word.Visible = $false
try {{
    $doc = $word.Documents.Open("{html_win}")
    $doc.SaveAs2([ref]"{docx_win}", [ref]16)
    $doc.Close($false)
    Write-Output "ok"
}} catch {{
    Write-Error $_.Exception.Message
}} finally {{
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($word) | Out-Null
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
}}
"""
        from tools.windows_tools import run_powershell
        result = await asyncio.to_thread(run_powershell, ps_script, 60)
        if result.get("error") or result.get("returncode", 0) != 0:
            err = result.get("error") or result.get("stderr") or "変換失敗"
            return JSONResponse({"error": err}, status_code=500)

        # docxパスをworkspace相対で返す
        rel_docx = str(docx_path.relative_to(ALLOWED_WORK_DIR))
        return JSONResponse({"ok": True, "docx_path": rel_docx, "docx_name": docx_path.name})
    finally:
        if html_path.exists():
            html_path.unlink()


@app.get("/version")
async def get_version():
    return {"version": APP_VERSION}


@app.get("/workspace/download")
async def workspace_download(path: str):
    """ワークスペースのファイルをダウンロードとして返す"""
    import mimetypes
    from config import ALLOWED_WORK_DIR
    try:
        resolved = (ALLOWED_WORK_DIR / path).resolve()
        if not str(resolved).startswith(str(ALLOWED_WORK_DIR)):
            return JSONResponse({"error": "作業ディレクトリ外のアクセスは禁止"}, status_code=400)
        if not resolved.exists() or not resolved.is_file():
            return JSONResponse({"error": "File not found"}, status_code=404)
        mt = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        import urllib.parse as _up
        fname_enc = _up.quote(resolved.name, safe='')
        headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{fname_enc}"}
        return FileResponse(str(resolved), media_type=mt, headers=headers)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/skills")
async def get_skills():
    """登録済みスキルの一覧を返す"""
    from fastapi.responses import JSONResponse as _JSONResponse
    from prompts import _SKILLS_DIR
    result = []
    if _SKILLS_DIR.exists():
        for skill_dir in sorted(_SKILLS_DIR.iterdir()):
            skill_file = skill_dir / "SKILL.md"
            if skill_dir.is_dir() and skill_file.exists():
                try:
                    content = skill_file.read_text(encoding="utf-8")
                    name = skill_dir.name
                    description = ""
                    trigger = f"/{name}"
                    for line in content.split("\n"):
                        if line.startswith("description:"):
                            description = line.split(":", 1)[1].strip()
                        elif line.startswith("trigger:"):
                            trigger = line.split(":", 1)[1].strip()
                    result.append({"name": name, "trigger": trigger, "description": description})
                except Exception:
                    pass
    return _JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@app.get("/gitlab/issues")
async def gitlab_issues(project: str = "", state: str = "opened"):
    """GitLab イシュー一覧を取得する。project は 'namespace/repo' 形式。省略時は GITLAB_USER のデフォルトリポジトリ。"""
    if not GITLAB_PAT:
        return JSONResponse({"error": "GITLAB_PAT が設定されていません"}, status_code=400)
    ns = project or f"{GITLAB_USER}/AI-Codeagent"
    encoded = ns.replace("/", "%2F")
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            resp = await client.get(
                f"https://gitlab.com/api/v4/projects/{encoded}/issues",
                headers={"PRIVATE-TOKEN": GITLAB_PAT},
                params={"per_page": 100, "state": state},
            )
        resp.raise_for_status()
        issues = [
            {
                "iid": i["iid"],
                "state": i["state"],
                "title": i["title"],
                "web_url": i["web_url"],
            }
            for i in resp.json()
        ]
        issues.sort(key=lambda x: x["iid"])
        return JSONResponse(issues)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
    tools_enabled: bool | None = None  # None=自動判定（Azure→True, ローカル→False）


def _active_foundry_instance() -> dict | None:
    """現在アクティブな Foundry インスタンスを返す（非 foundry タイプなら None）"""
    if _provider_config["type"] != "foundry":
        return None
    preset_id = _provider_config.get("preset_id", "foundry_1")
    for inst in FOUNDRY_INSTANCES:
        if inst["id"] == preset_id:
            return inst
    return FOUNDRY_INSTANCES[0] if FOUNDRY_INSTANCES else None


@app.get("/providers/deployments")
async def providers_deployments():
    """現在のプロバイダーのモデル一覧と現在のモデルを返す"""
    if _provider_config["type"] == "foundry":
        inst = _active_foundry_instance()
        deployments = inst["models"] if inst else FOUNDRY_MODELS
    elif _provider_config["type"] == "gemini":
        deployments = GEMINI_MODELS or _GEMINI_DEFAULT_MODELS
    elif _provider_config["type"] == "openai":
        deployments = OPENAI_MODELS or _OPENAI_DEFAULT_MODELS
    else:
        deployments = AZURE_OPENAI_DEPLOYMENTS
    return JSONResponse({
        "deployments": deployments,
        "current": _provider_config["model"],
    })


class DeploymentRequest(BaseModel):
    model: str


@app.post("/providers/deployment")
async def providers_set_deployment(req: DeploymentRequest):
    """現在のプロバイダーのモデルだけを切り替える"""
    global _provider_config
    if _provider_config["type"] == "foundry":
        inst = _active_foundry_instance()
        allowed = inst["models"] if inst else FOUNDRY_MODELS
    elif _provider_config["type"] == "gemini":
        allowed = GEMINI_MODELS or _GEMINI_DEFAULT_MODELS
    elif _provider_config["type"] == "openai":
        allowed = OPENAI_MODELS or _OPENAI_DEFAULT_MODELS
    else:
        allowed = AZURE_OPENAI_DEPLOYMENTS
    if req.model not in allowed:
        return JSONResponse({"error": f"未登録のモデル: {req.model}"}, status_code=400)
    _provider_config = {**_provider_config, "model": req.model}
    _save_provider_config(_provider_config)
    return JSONResponse({"status": "ok", "model": req.model})


@app.get("/providers/current")
async def providers_current():
    return JSONResponse({
        "type": _provider_config["type"],
        "preset_id": _provider_config.get("preset_id", _provider_config["type"]),
        "name": _provider_config.get("name", ""),
        "url": _provider_config["url"],
        "model": _provider_config["model"],
        "tools_enabled": _provider_config.get("tools_enabled", True),
        "web_research_provider": WEB_RESEARCH_PROVIDER,
    })


@app.get("/providers/models")
async def providers_models(url: str, api_key: str = ""):
    """指定URLの /v1/models を叩いてモデル一覧を返す。
    Azure OpenAI の場合は /v1/models が存在しないため .env の設定値を返す。"""
    try:
        # Azure OpenAI / Foundry は /v1/models をサポートしないため設定値を返す
        if "openai.azure.com" in url or "cognitiveservices.azure.com" in url:
            from config import AZURE_OPENAI_DEPLOYMENTS, FOUNDRY_INSTANCES
            models = list(AZURE_OPENAI_DEPLOYMENTS)
            # Foundry インスタンスのモデルも追加
            for inst in FOUNDRY_INSTANCES:
                if inst["endpoint"].rstrip("/") == url.rstrip("/"):
                    models = list(inst["models"])
                    break
            if not models:
                models = []
            return JSONResponse({"models": models, "note": "Azure: .envの設定値を使用"})

        # OpenAI 互換エンドポイント（LM Studio, Ollama 等）
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


class PresetRequest(BaseModel):
    preset: str  # "azure" | "foundry"


@app.get("/providers/presets")
async def providers_presets():
    """設定済みプリセット一覧を返す"""
    return JSONResponse({
        "azure": bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY),
        "foundry_instances": [
            {"id": inst["id"], "name": inst["name"], "default_model": inst["default_model"]}
            for inst in FOUNDRY_INSTANCES
        ],
        "gemini": bool(GEMINI_API_KEY),
        "gemini_models": GEMINI_MODELS or _GEMINI_DEFAULT_MODELS,
        "openai": bool(OPENAI_API_KEY),
        "openai_models": OPENAI_MODELS or _OPENAI_DEFAULT_MODELS,
    })


@app.post("/providers/preset")
async def providers_set_preset(req: PresetRequest):
    """.env のプリセット設定に切り替える"""
    global _provider_config
    if req.preset == "azure":
        _provider_config = dict(_default_provider_config)
    elif req.preset.startswith("foundry"):
        # "foundry" (後方互換) → "foundry_1" に正規化
        preset_id = req.preset if req.preset != "foundry" else "foundry_1"
        inst = next((i for i in FOUNDRY_INSTANCES if i["id"] == preset_id), None)
        if not inst:
            return JSONResponse({"error": f"Foundry インスタンスが見つかりません: {preset_id}"}, status_code=400)
        _provider_config = {
            "type": "foundry",
            "preset_id": inst["id"],
            "name": inst["name"],
            "url": inst["endpoint"],
            "api_key": inst["api_key"],
            "model": inst["default_model"],
            "api_version": inst["api_version"],
            "tools_enabled": True,
        }
    elif req.preset == "gemini":
        if not GEMINI_API_KEY:
            return JSONResponse({"error": ".env に GEMINI_API_KEY が未設定です"}, status_code=400)
        default_model = (GEMINI_MODELS[0] if GEMINI_MODELS else _GEMINI_DEFAULT_MODELS[0])
        _provider_config = {
            "type": "gemini",
            "preset_id": "gemini",
            "name": "Google Gemini",
            "url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "api_key": GEMINI_API_KEY,
            "model": default_model,
            "api_version": "",
            "tools_enabled": True,
        }
    elif req.preset == "openai":
        if not OPENAI_API_KEY:
            return JSONResponse({"error": ".env に OPENAI_API_KEY が未設定です"}, status_code=400)
        default_model = OPENAI_MODEL or (OPENAI_MODELS[0] if OPENAI_MODELS else _OPENAI_DEFAULT_MODELS[0])
        _provider_config = {
            "type": "openai",
            "preset_id": "openai",
            "name": "OpenAI",
            "url": "https://api.openai.com/v1",
            "api_key": OPENAI_API_KEY,
            "model": default_model,
            "api_version": "",
            "tools_enabled": True,
        }
    else:
        return JSONResponse({"error": f"不明なプリセット: {req.preset}"}, status_code=400)
    _save_provider_config(_provider_config)
    return JSONResponse({"status": "ok", "provider": {
        "type": _provider_config["type"],
        "preset_id": _provider_config.get("preset_id", _provider_config["type"]),
        "name": _provider_config.get("name", ""),
        "url": _provider_config["url"],
        "model": _provider_config["model"],
        "tools_enabled": _provider_config.get("tools_enabled", True),
    }})


@app.post("/providers/config")
async def providers_config(req: ProviderConfigRequest):
    """プロバイダー設定を更新する。urlが空の場合はAzureデフォルトに戻す"""
    global _provider_config
    if not req.url:
        # Azureデフォルトにリセット
        _provider_config = dict(_default_provider_config)
    else:
        if ".openai.azure.com" in req.url:
            provider_type = "azure"
            api_version = _default_provider_config["api_version"]
        elif ".cognitiveservices.azure.com" in req.url:
            provider_type = "foundry"
            api_version = FOUNDRY_API_VERSION
        elif "api.openai.com" in req.url:
            provider_type = "openai"
            api_version = ""
        else:
            provider_type = "openai_compatible"
            api_version = _default_provider_config["api_version"]
        # tools_enabled: 明示指定があればそれを使う。なければ Azure/Foundry→True, ローカル→False
        tools_enabled = req.tools_enabled if req.tools_enabled is not None else (provider_type in ("azure", "foundry"))
        _provider_config = {
            "type": provider_type,
            "url": req.url,
            "api_key": req.api_key,
            "model": req.model,
            "api_version": api_version,
            "tools_enabled": tools_enabled,
        }
    _save_provider_config(_provider_config)
    return JSONResponse({"status": "ok", "provider": {
        "type": _provider_config["type"],
        "url": _provider_config["url"],
        "model": _provider_config["model"],
        "tools_enabled": _provider_config.get("tools_enabled", True),
    }})


# ============================================================
# マルチエージェント: プロバイダー横断設定
# ============================================================

_MA_CONFIG_FILE = Path(__file__).parent / ".multi_agent_config.json"

_MA_ROLE_DEFAULTS = ["dispatcher", "design", "coding", "debug", "security", "docs", "research", "infra"]


def _load_ma_config() -> dict:
    if _MA_CONFIG_FILE.exists():
        try:
            return json.loads(_MA_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # デフォルト: 現在のアクティブプロバイダーを全役割に適用
    default = {"preset_id": _provider_config.get("preset_id", _provider_config["type"]), "model": _provider_config["model"]}
    return {role: dict(default) for role in _MA_ROLE_DEFAULTS}


def _save_ma_config(cfg: dict):
    try:
        _MA_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[ma_config] save failed: {e}")


def _make_async_client_for(preset_id: str):
    """任意のプロバイダーID から非同期クライアントを生成する"""
    if preset_id == "azure":
        return AsyncAzureOpenAI(
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
            http_client=httpx.AsyncClient(trust_env=False),
        )
    if preset_id.startswith("foundry"):
        inst = next((i for i in FOUNDRY_INSTANCES if i["id"] == preset_id), None)
        if not inst and FOUNDRY_INSTANCES:
            inst = FOUNDRY_INSTANCES[0]
        if inst:
            return AsyncAzureOpenAI(
                azure_endpoint=inst["endpoint"],
                api_key=inst["api_key"],
                api_version=inst["api_version"],
                http_client=httpx.AsyncClient(trust_env=False),
            )
    if preset_id == "gemini":
        return AsyncOpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=GEMINI_API_KEY,
            http_client=httpx.AsyncClient(trust_env=False),
        )
    if preset_id == "openai":
        return AsyncOpenAI(
            api_key=OPENAI_API_KEY,
            http_client=httpx.AsyncClient(trust_env=False),
        )
    # フォールバック: 現在のアクティブプロバイダー
    return _make_async_client()


@app.get("/multi-agent/providers")
async def ma_providers():
    """マルチエージェント設定UI用: 登録済みプロバイダーとモデル一覧を返す"""
    providers = []
    if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY:
        providers.append({"preset_id": "azure", "name": "Azure OpenAI", "models": list(AZURE_OPENAI_DEPLOYMENTS)})
    for inst in FOUNDRY_INSTANCES:
        providers.append({"preset_id": inst["id"], "name": inst["name"], "models": list(inst["models"])})
    if GEMINI_API_KEY:
        providers.append({"preset_id": "gemini", "name": "Google Gemini", "models": list(GEMINI_MODELS or _GEMINI_DEFAULT_MODELS)})
    if OPENAI_API_KEY:
        providers.append({"preset_id": "openai", "name": "OpenAI", "models": list(OPENAI_MODELS or _OPENAI_DEFAULT_MODELS)})
    return JSONResponse({"providers": providers})


@app.get("/multi-agent/config")
async def ma_config_get():
    return JSONResponse(_load_ma_config())


@app.post("/multi-agent/config")
async def ma_config_save(body: dict):
    _save_ma_config(body)
    return JSONResponse({"ok": True})


class CleanupRequest(BaseModel):
    paths: list


@app.get("/workspace/subdirs")
async def workspace_subdirs():
    """workspace 直下のサブディレクトリ一覧を返す（スコープ選択モーダル用）"""
    try:
        dirs = sorted(p.name for p in ALLOWED_WORK_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))
        return JSONResponse({"dirs": dirs})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


class MkdirRequest(BaseModel):
    name: str

@app.post("/workspace/mkdir")
async def workspace_mkdir(req: MkdirRequest):
    """workspace 直下に新規ディレクトリを作成する（スコープ選択モーダル用）"""
    name = req.name.strip().strip("/")
    if not name or "/" in name or name.startswith("."):
        return JSONResponse({"error": "無効なフォルダ名です"}, status_code=400)
    target = ALLOWED_WORK_DIR / name
    if target.exists():
        return JSONResponse({"error": "既に存在します"}, status_code=400)
    try:
        target.mkdir()
        return JSONResponse({"name": name})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/workspace/ls")
async def workspace_ls():
    """workspace のファイル一覧を直接返す（LLM経由なし）"""
    result = list_files()
    return JSONResponse({"output": result})


@app.get("/workspace/git-status")
async def workspace_git_status():
    """workspace の git status を直接返す（LLM経由なし）"""
    result = run_command("git status")
    output = result.get("stdout") or result.get("stderr") or result.get("error", "エラー")
    return JSONResponse({"output": output})


@app.get("/workspace/git-diff")
async def workspace_git_diff():
    """workspace の git diff を直接返す（LLM経由なし）"""
    result = run_command("git diff")
    output = result.get("stdout") or result.get("stderr") or result.get("error", "エラー")
    if not output.strip():
        output = "(変更なし)"
    return JSONResponse({"output": output})


@app.get("/workspace/cleanup-preview")
async def workspace_cleanup_preview_api():
    """UIから直接掃除モーダルを開くためのエンドポイント"""
    result = workspace_cleanup_preview()
    return JSONResponse(json.loads(result) if isinstance(result, str) else result)


class ProtectedUpdateRequest(BaseModel):
    paths: list[str]

@app.post("/workspace/protected")
async def workspace_protected_update(req: ProtectedUpdateRequest):
    """保護リストをUIから直接更新する"""
    from tools.workspace_tools import PROTECTED_LIST_FILE, ALWAYS_PROTECTED
    try:
        clean = list(dict.fromkeys(p for p in req.paths if p not in ALWAYS_PROTECTED))
        PROTECTED_LIST_FILE.write_text(
            json.dumps({"paths": clean}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return JSONResponse({"paths": clean, "count": len(clean)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


class RawWriteRequest(BaseModel):
    path: str
    content: str

@app.post("/workspace/upload")
async def workspace_upload(file: UploadFile = FastAPIFile(...), folder: str = ""):
    """ファイルをworkspaceにアップロードして保存する。バイナリファイル（PDF・Office等）対応。folder を指定するとサブフォルダに保存。"""
    from tools.file_tools import _resolve_safe_path
    try:
        filename = Path(file.filename).name  # パストラバーサル防止
        rel = f"{folder}/{filename}" if folder else filename
        target = _resolve_safe_path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        target.write_bytes(content)
        return JSONResponse({"path": rel, "size": len(content), "error": None})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/workspace/write-raw")
async def workspace_write_raw(req: RawWriteRequest):
    """LLMを経由せずコンテンツをそのままworkspaceに書き込む"""
    if req.path.lower().endswith(".ps1"):
        # PowerShell 5.1はBOMなしUTF-8を認識しないためBOM付きで保存
        from tools.file_tools import _resolve_safe_path
        target = _resolve_safe_path(req.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(req.content, encoding="utf-8-sig")
        return JSONResponse({"result": {"message": f"{req.path} に書き込みました"}})
    result = write_file(req.path, req.content)
    return JSONResponse({"result": result})


@app.get("/workspace/file")
async def workspace_file_read(path: str):
    """エディタ用: workspaceのファイル内容を返す"""
    from tools.file_tools import _resolve_safe_path
    try:
        resolved = _resolve_safe_path(path)
        if not resolved.exists() or not resolved.is_file():
            return JSONResponse({"error": "File not found"}, status_code=404)
        content = resolved.read_text(encoding="utf-8", errors="replace")
        return JSONResponse({"content": content, "path": path})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/workspace/image")
async def workspace_image_serve(path: str):
    """ワークスペースの画像ファイルをバイナリで返す（PNG/JPG等）"""
    import mimetypes
    from tools.file_tools import _resolve_safe_path
    try:
        resolved = _resolve_safe_path(path)
        if not resolved.exists() or not resolved.is_file():
            return JSONResponse({"error": "File not found"}, status_code=404)
        mt = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        headers = {"Content-Disposition": f'inline; filename="{resolved.name}"'}
        return FileResponse(str(resolved), media_type=mt, headers=headers)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/workspace/file")
async def workspace_file_delete(path: str):
    """ワークスペース内のファイルを削除する"""
    from tools.file_tools import _resolve_safe_path
    try:
        target = _resolve_safe_path(path)
        if not target.exists():
            return JSONResponse({"error": "File not found"}, status_code=404)
        target.unlink()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/workspace/temp-images")
async def workspace_temp_images(scope: str = ""):
    """TEMP フォルダ内の画像ファイル一覧を返す（生成元画像選択用）"""
    from tools.file_tools import _resolve_safe_path
    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    try:
        folder = f"{scope}/TEMP" if scope else "TEMP"
        temp_dir = _resolve_safe_path(folder)
        if not temp_dir.exists():
            return JSONResponse({"images": []})
        images = sorted(
            [f for f in temp_dir.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        rel_paths = [f"{folder}/{f.name}" for f in images]
        return JSONResponse({"images": rel_paths})
    except Exception as e:
        return JSONResponse({"images": [], "error": str(e)})


@app.get("/workspace/tree")
async def workspace_tree():
    """エディタ用: workspaceのファイルツリーをフラットリストで返す"""
    result = []
    try:
        for p in sorted(ALLOWED_WORK_DIR.rglob("*")):
            if any(part.startswith(".") for part in p.parts[len(ALLOWED_WORK_DIR.parts):]):
                continue
            rel = p.relative_to(ALLOWED_WORK_DIR)
            result.append({"path": str(rel).replace("\\", "/"), "is_dir": p.is_dir()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"files": result})


@app.post("/workspace/copy")
async def workspace_copy(body: dict):
    """エディタ用: ファイルを複製（同じディレクトリに _copy サフィックスで作成）"""
    import shutil
    from tools.file_tools import _resolve_safe_path
    src_rel = body.get("src", "").strip("/")
    if not src_rel:
        return JSONResponse({"error": "src が必要です"}, status_code=400)
    try:
        src_path = _resolve_safe_path(src_rel)
        if not src_path.exists() or not src_path.is_file():
            return JSONResponse({"error": "ファイルが見つかりません"}, status_code=404)
        stem = src_path.stem
        suffix = src_path.suffix
        new_path = src_path.parent / f"{stem}_copy{suffix}"
        n = 2
        while new_path.exists():
            new_path = src_path.parent / f"{stem}_copy{n}{suffix}"
            n += 1
        shutil.copy2(str(src_path), str(new_path))
        rel = str(new_path.relative_to(ALLOWED_WORK_DIR)).replace("\\", "/")
        return JSONResponse({"ok": True, "new_path": rel})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/workspace/archive-info")
async def workspace_archive_info(scope: str = ""):
    """アーカイブスキル用: vault パス・コピー元/先・ホスト名を返す"""
    import socket, platform as _platform
    hostname = socket.gethostname()
    is_wsl = "wsl" in _platform.uname().release.lower() or Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
    suffix = "wsl" if is_wsl else "win"
    vault_path = OBSIDIAN_VAULT_PATH
    archives_base = str(Path(vault_path) / "archives" / f"{hostname}_{suffix}") if vault_path else ""
    src_path = str(ALLOWED_WORK_DIR / scope) if scope else ""
    dst_path = str(Path(archives_base) / scope) if (archives_base and scope) else ""
    return JSONResponse({
        "vault_path": vault_path,
        "archives_base": archives_base,
        "hostname": hostname,
        "platform": suffix,
        "scope": scope,
        "src_path": src_path,
        "dst_path": dst_path,
    })


@app.get("/workspace/archived-status")
async def workspace_archived_status(scope: str = ""):
    """指定スコープの .archived マーカーファイルの有無・内容を返す"""
    if not scope:
        return JSONResponse({"archived": False})
    marker = ALLOWED_WORK_DIR / scope / ".archived"
    if not marker.exists():
        return JSONResponse({"archived": False})
    content = marker.read_text(encoding="utf-8")
    archived_at = ""
    for line in content.splitlines():
        if line.startswith("archived_at:"):
            archived_at = line.split(":", 1)[1].strip()
            break
    return JSONResponse({"archived": True, "archived_at": archived_at})


@app.post("/workspace/rename")
async def workspace_rename(body: dict):
    """エディタ用: ファイル/フォルダの名前変更"""
    import shutil
    from tools.file_tools import _resolve_safe_path
    src_rel = body.get("src", "").strip("/")
    newname = body.get("newname", "").strip()
    if not src_rel or not newname:
        return JSONResponse({"error": "src と newname が必要です"}, status_code=400)
    if "/" in newname or "\\" in newname:
        return JSONResponse({"error": "名前にスラッシュは使えません"}, status_code=400)
    try:
        src_path = _resolve_safe_path(src_rel)
        if not src_path.exists():
            return JSONResponse({"error": "ファイルが見つかりません"}, status_code=404)
        new_path = src_path.parent / newname
        if not str(new_path.resolve()).startswith(str(ALLOWED_WORK_DIR)):
            return JSONResponse({"error": "パストラバーサル検出"}, status_code=400)
        if new_path.exists():
            return JSONResponse({"error": f"{newname} は既に存在します"}, status_code=409)
        shutil.move(str(src_path), str(new_path))
        rel = str(new_path.relative_to(ALLOWED_WORK_DIR)).replace("\\", "/")
        return JSONResponse({"ok": True, "new_path": rel})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/workspace/move")
async def workspace_move(body: dict):
    """エディタ用: ファイル/フォルダを移動"""
    import shutil
    from tools.file_tools import _resolve_safe_path
    src_rel = body.get("src", "").strip("/")
    dst_rel = body.get("dst", "").strip("/")
    if not src_rel:
        return JSONResponse({"error": "src required"}, status_code=400)
    try:
        src_path = _resolve_safe_path(src_rel)
        if not src_path.exists():
            return JSONResponse({"error": "移動元が見つかりません"}, status_code=404)
        dst_dir = _resolve_safe_path(dst_rel) if dst_rel else ALLOWED_WORK_DIR
        if not dst_dir.is_dir():
            return JSONResponse({"error": "移動先がディレクトリではありません"}, status_code=400)
        new_path = dst_dir / src_path.name
        if new_path == src_path:
            return JSONResponse({"ok": True})
        if new_path.exists():
            return JSONResponse({"error": f"{src_path.name} は移動先に既に存在します"}, status_code=409)
        shutil.move(str(src_path), str(new_path))
        rel = str(new_path.relative_to(ALLOWED_WORK_DIR)).replace("\\", "/")
        return JSONResponse({"ok": True, "new_path": rel})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/workspace/shells")
async def workspace_shells():
    """workspace内の .sh / .ps1 ファイルを再帰的に列挙する"""
    result = []
    try:
        patterns = ["*.sh", "*.ps1"] if IS_WINDOWS else ["*.sh"]
        for pattern in patterns:
            for p in sorted(ALLOWED_WORK_DIR.rglob(pattern)):
                rel = p.relative_to(ALLOWED_WORK_DIR)
                result.append(str(rel).replace("\\", "/"))
        result.sort()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"scripts": result})


@app.get("/workspace/ls")
async def workspace_ls(path: str = ""):
    """指定パス直下のディレクトリ・ファイルを1階層だけ返す（シェルパネルのディレクトリナビ用）"""
    from tools.file_tools import _resolve_safe_path
    try:
        base = _resolve_safe_path(path) if path else ALLOWED_WORK_DIR
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not base.is_dir():
        return JSONResponse({"error": "not a directory"}, status_code=400)
    entries = []
    for p in sorted(base.iterdir()):
        if p.name.startswith("."):
            continue
        rel = p.relative_to(ALLOWED_WORK_DIR)
        entries.append({"name": p.name, "path": str(rel).replace("\\", "/"), "is_dir": p.is_dir()})
    # 親ディレクトリへのパス（workspace ルートより上には出ない）
    parent = None
    if base != ALLOWED_WORK_DIR:
        parent = str(base.parent.relative_to(ALLOWED_WORK_DIR)).replace("\\", "/")
        if parent == ".":
            parent = ""
    return JSONResponse({"entries": entries, "current": str(base.relative_to(ALLOWED_WORK_DIR)).replace("\\", "/"), "parent": parent})


_GIT_BASH_CANDIDATES = [
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
]

def _find_git_bash() -> str | None:
    found = shutil.which("bash.exe")
    if found:
        return found
    for p in _GIT_BASH_CANDIDATES:
        if Path(p).exists():
            return p
    return None

def _find_powershell_exe() -> str:
    from tools.windows_tools import _find_powershell
    ps = _find_powershell()
    return ps or "powershell.exe"

IS_WINDOWS = sys.platform == "win32"

@app.get("/workspace/run-shell")
async def workspace_run_shell(path: str):
    """指定した .sh / .ps1 スクリプトを実行し、出力をSSEストリームで返す"""
    from tools.file_tools import _resolve_safe_path
    try:
        resolved = _resolve_safe_path(path)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not resolved.exists() or not resolved.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)

    suffix = resolved.suffix.lower()
    if IS_WINDOWS:
        if suffix == ".ps1":
            cmd = [_find_powershell_exe(), "-ExecutionPolicy", "Bypass", "-NoProfile",
                   "-Command", f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; & '{resolved}'"]
        elif suffix == ".sh":
            bash = _find_git_bash()
            if not bash:
                return JSONResponse({"error": "Git Bash が見つかりません。Git for Windows をインストールしてください。"}, status_code=400)
            cmd = [bash, str(resolved)]
        else:
            return JSONResponse({"error": ".sh / .ps1 ファイルのみ実行できます"}, status_code=400)
    else:
        if suffix != ".sh":
            return JSONResponse({"error": ".sh ファイルのみ実行できます"}, status_code=400)
        cmd = ["bash", str(resolved)]

    async def stream():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(resolved.parent),
        )
        yield f"data: {json.dumps({'type': 'start', 'path': path})}\n\n"
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
            yield f"data: {json.dumps({'type': 'line', 'text': text})}\n\n"
        await proc.wait()
        yield f"data: {json.dumps({'type': 'done', 'returncode': proc.returncode})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ShellExecRequest(BaseModel):
    command: str
    cwd: str = ""

@app.post("/workspace/exec-shell")
async def workspace_exec_shell(req: ShellExecRequest):
    """任意のコマンドを実行し、出力をSSEストリームで返す（Windows版はPowerShell使用）"""
    from tools.file_tools import _resolve_safe_path
    if req.cwd:
        try:
            exec_cwd = str(_resolve_safe_path(req.cwd))
        except Exception:
            exec_cwd = str(ALLOWED_WORK_DIR)
    else:
        exec_cwd = str(ALLOWED_WORK_DIR)

    if IS_WINDOWS:
        cmd = [_find_powershell_exe(), "-NoProfile", "-Command",
               f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; {req.command}"]
    else:
        cmd = ["bash", "-c", req.command]

    async def stream():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=exec_cwd,
        )
        yield f"data: {json.dumps({'type': 'start', 'command': req.command})}\n\n"
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
            yield f"data: {json.dumps({'type': 'line', 'text': text})}\n\n"
        await proc.wait()
        yield f"data: {json.dumps({'type': 'done', 'returncode': proc.returncode})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class EditorCompleteRequest(BaseModel):
    code_before: str
    code_after: str = ""
    language: str = "plaintext"
    filename: str = ""
    model: str = ""

@app.post("/editor/complete")
async def editor_complete(req: EditorCompleteRequest):
    """Monaco エディタ用AI補完。カーソル前後のコードを渡すと補完候補を返す。"""
    # カーソル前の現在行内容を取得（インデント量の参考用）
    current_line = req.code_before.split('\n')[-1] if req.code_before else ''
    prompt = (
        f"コード補完アシスタント。<CURSOR>の位置に挿入するテキストを1つ提案してください。\n"
        f"言語: {req.language}  ファイル: {req.filename or '(不明)'}\n\n"
        f"【コード】\n{req.code_before[-1000:]}<CURSOR>{req.code_after[:200]}\n\n"
        "ルール:\n"
        "- <CURSOR>の直後から始まるテキストのみを insertText に入れること\n"
        "- 新しい行が必要なら \\n を使うこと（カーソルが行末にある場合など）\n"
        f"- 現在行: {repr(current_line)}  （この行の続きか、改行してから次の行を補完）\n"
        "- カーソル前のインデントは含めない（カーソルは既にその位置にある）\n"
        'JSON配列1件のみ: [{"insertText":"..."}]  JSONのみ返すこと。'
    )
    complete_model = req.model.strip() if req.model and req.model.strip() else _provider_config["model"]
    try:
        client = _make_client()
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=complete_model,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=800,
            )
        )
        text = resp.choices[0].message.content.strip()
        # JSONブロック記号を除去
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
        if text.endswith("```"):
            text = "\n".join(text.split("\n")[:-1])
        items = json.loads(text)
        return JSONResponse({"items": items})
    except Exception as e:
        return JSONResponse({"error": str(e), "items": []}, status_code=500)


class InlineChatRequest(BaseModel):
    messages: list
    current_code: str = ""
    language: str = "plaintext"
    filename: str = ""
    model: str = ""
    is_selection: bool = False

@app.post("/editor/chat")
async def editor_chat(req: InlineChatRequest):
    """インラインチャット。現在のコードを文脈として往復チャット（ツールなし）。"""
    chat_model = req.model.strip() if req.model and req.model.strip() else _provider_config["model"]
    system = (
        f"あなたはエキスパートプログラマーのアシスタントです。"
        f"ユーザーが今開いているファイル（言語: {req.language}、ファイル名: {req.filename or '不明'}）について質問・作業しています。\n"
        "- コードの説明・修正・生成の依頼に応えてください。\n"
        "- コードを提案する場合は必ずコードブロック（```lang\n...\n```）で囲んでください。\n"
        "- 簡潔に、要点を絞って回答してください。\n"
        "- 日本語で回答してください。"
    )
    if req.current_code:
        lang = req.language or "plaintext"
        if req.is_selection:
            system += (
                f"\n\n以下はユーザーが選択した範囲のコードです:\n```{lang}\n{req.current_code[:3000]}\n```\n"
                "コードを修正する場合は、この選択範囲全体を変更済みの状態で返してください（変更箇所だけでなく選択範囲全体を返すこと）。ファイル全体は返さないでください。"
            )
        else:
            total_lines = len(req.current_code.splitlines())
            system += (
                f"\n\n現在のコード（ファイル全体・{total_lines}行）:\n```{lang}\n{req.current_code[:6000]}\n```\n"
                f"【重要】コードを修正・追加する場合は、必ず{total_lines}行すべてを含むファイル全体を変更済みの状態で返してください。"
                "スニペットや一部だけを返すことは絶対にしないでください。説明文や挿入指示も不要です。コードブロックのみ返してください。"
            )

    messages = [{"role": "system", "content": system}]
    for msg in req.messages:
        messages.append({"role": msg["role"], "content": msg["content"]})

    try:
        client = _make_client()
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=chat_model,
                messages=messages,
                max_completion_tokens=2000,
            )
        )
        reply = resp.choices[0].message.content.strip()
        return JSONResponse({"reply": reply})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/workspace/cleanup")
async def workspace_cleanup(req: CleanupRequest):
    """保護リストにないファイル・ディレクトリを削除する"""
    from tools.workspace_tools import PROTECTED_LIST_FILE, ALWAYS_PROTECTED
    # 保護リストをサーバー側で再ロード（フロントエンドから渡されたリストは信頼しない）
    try:
        if PROTECTED_LIST_FILE.exists():
            user_protected = set(json.loads(PROTECTED_LIST_FILE.read_text(encoding="utf-8")).get("paths", []))
        else:
            user_protected = set()
    except Exception:
        user_protected = set()
    protected_names = ALWAYS_PROTECTED | user_protected

    deleted = []
    errors = []
    for name in req.paths:
        # 保護リストチェック（末尾スラッシュあり・なし両方）
        if name in protected_names or (name + "/") in protected_names:
            errors.append({"name": name, "error": "保護リストに含まれているため削除不可"})
            continue
        # パストラバーサル防止
        target = (ALLOWED_WORK_DIR / name).resolve()
        if not str(target).startswith(str(ALLOWED_WORK_DIR)):
            errors.append({"name": name, "error": "パストラバーサル検出"})
            continue
        if not target.exists():
            errors.append({"name": name, "error": "存在しない"})
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            deleted.append(name)
            print(f"[cleanup] deleted: {name}", flush=True)
        except Exception as e:
            errors.append({"name": name, "error": str(e)})
            print(f"[cleanup] error deleting {name}: {e}", flush=True)
    if errors:
        blocked = [e for e in errors if "保護リスト" in e.get("error", "")]
        if blocked:
            print(f"[cleanup] blocked (protected): {[e['name'] for e in blocked]}", flush=True)
    return JSONResponse({"deleted": deleted, "errors": errors})


# ---- セッション履歴管理 ----
SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR = SESSIONS_DIR / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)
PROTECTED_FILE = SESSIONS_DIR / ".protected"
SESSIONS_KEEP = 20
ARCHIVE_KEEP = 100


def _load_protected() -> set:
    if not PROTECTED_FILE.exists():
        return set()
    try:
        return set(json.loads(PROTECTED_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_protected(ids: set):
    PROTECTED_FILE.write_text(json.dumps(sorted(ids), ensure_ascii=False), encoding="utf-8")


def _archive_old_sessions():
    """sessions/ が SESSIONS_KEEP 件を超えたら古い順にアーカイブ移動。archive/ が ARCHIVE_KEEP 件を超えたら古い順に削除。"""
    protected = _load_protected()
    def _updated_at(p):
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("updated_at", "")
        except Exception:
            return ""
    files = sorted(SESSIONS_DIR.glob("*.json"), key=_updated_at, reverse=True)
    keep, overflow = [], []
    for f in files:
        sid = f.stem
        if sid in protected or len(keep) < SESSIONS_KEEP:
            keep.append(f)
        else:
            overflow.append(f)
    for f in overflow:
        dest = ARCHIVE_DIR / f.name
        f.rename(dest)
    archive_files = sorted(ARCHIVE_DIR.glob("*.json"), key=lambda p: json.loads(p.read_text(encoding="utf-8")).get("updated_at", "") if p.exists() else "", reverse=True)
    for f in archive_files[ARCHIVE_KEEP:]:
        f.unlink(missing_ok=True)


class SessionSaveRequest(BaseModel):
    session_id: str
    history: list
    turn_models: list = []


class CompactRequest(BaseModel):
    messages: list


@app.post("/compact")
async def compact_history_endpoint(req: CompactRequest):
    """クライアントから明示的に /compact を実行したときの圧縮エンドポイント"""
    messages = _sanitize_history(req.messages)
    if len(messages) <= SUMMARY_KEEP_RECENT:
        return JSONResponse({"messages": messages, "compressed": False})
    old_part = messages[:-SUMMARY_KEEP_RECENT]
    recent_part = messages[-SUMMARY_KEEP_RECENT:]
    while _is_recent_head_unsafe(recent_part) and old_part:
        recent_part = [old_part[-1]] + recent_part
        old_part = old_part[:-1]
    summary = await asyncio.to_thread(_summarize_history, old_part)
    if not summary:
        return JSONResponse({"messages": messages, "compressed": False})
    compressed = [
        {"role": "user", "content": f"[これまでの作業サマリー]\n{summary}"},
        {"role": "assistant", "content": "了解しました。続けます。"},
    ] + recent_part
    return JSONResponse({"messages": compressed, "compressed": True})


@app.get("/sessions")
async def list_sessions():
    """セッション一覧を取得（最新順）"""
    protected = _load_protected()
    sessions = []
    def _mtime(p):
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("updated_at", "")
        except Exception:
            return ""
    for f in sorted(SESSIONS_DIR.glob("*.json"), key=_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sid = data.get("session_id")
            turn_count = len([m for m in data.get("history", []) if m.get("role") == "user"])
            sessions.append({
                "session_id": sid,
                "title": data.get("title", "無題"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "turn_count": turn_count,
                "protected": sid in protected,
            })
        except Exception:
            pass
    return JSONResponse(sessions)


@app.get("/sessions/archive")
async def list_archive_sessions():
    """アーカイブ済みセッション一覧を取得（最新順）"""
    protected = _load_protected()
    sessions = []
    for f in sorted(ARCHIVE_DIR.glob("*.json"), key=lambda p: json.loads(p.read_text(encoding="utf-8")).get("updated_at", ""), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sid = data.get("session_id")
            turn_count = len([m for m in data.get("history", []) if m.get("role") == "user"])
            sessions.append({
                "session_id": sid,
                "title": data.get("title", "無題"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "turn_count": turn_count,
                "protected": sid in protected,
                "archived": True,
            })
        except Exception:
            pass
    return JSONResponse(sessions)


def _extract_snippet(history: list, keyword: str, context: int = 60) -> dict:
    """会話履歴からキーワード周辺テキストを抜き出してターン番号・ロール・スニペットを返却"""
    kw = keyword.lower()
    turn = 0
    for msg in history:
        role = msg.get("role", "")
        if role == "user":
            turn += 1
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
            )
        if not isinstance(content, str):
            continue
        idx = content.lower().find(kw)
        if idx == -1:
            continue
        start = max(0, idx - context)
        end = min(len(content), idx + len(keyword) + context)
        snippet = content[start:end].replace("\n", " ")
        if start > 0:
            snippet = "…" + snippet
        if end < len(content):
            snippet = snippet + "…"
        role_label = "あなた" if role == "user" else "AI"
        return {"text": snippet, "turn": turn, "role": role_label}
    return {}


@app.get("/sessions/search")
async def search_sessions(q: str = "", archive: int = 0):
    """セッションをキーワード検索（タイトル・会話内容）"""
    keyword = q.strip().lower()
    protected = _load_protected()
    results = []
    dirs = [SESSIONS_DIR, ARCHIVE_DIR] if archive else [SESSIONS_DIR]
    for d in dirs:
        for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                text = f.read_text(encoding="utf-8")
                if keyword and keyword not in text.lower():
                    continue
                data = json.loads(text)
                sid = data.get("session_id")
                history = data.get("history", [])
                turn_count = len([m for m in history if m.get("role") == "user"])
                snippet = _extract_snippet(history, keyword) if keyword else {}
                results.append({
                    "session_id": sid,
                    "title": data.get("title", "無題"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "turn_count": turn_count,
                    "protected": sid in protected,
                    "archived": d == ARCHIVE_DIR,
                    "snippet": snippet.get("text", ""),
                    "snippet_turn": snippet.get("turn"),
                    "snippet_role": snippet.get("role", ""),
                })
            except Exception:
                pass
    return JSONResponse(results)


@app.post("/sessions/{session_id}/protect")
async def toggle_protect_session(session_id: str):
    """セッションの保護フラグをトグル"""
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        return JSONResponse({"error": "invalid session_id"}, status_code=400)
    protected = _load_protected()
    if session_id in protected:
        protected.discard(session_id)
        is_protected = False
    else:
        protected.add(session_id)
        is_protected = True
    _save_protected(protected)
    return JSONResponse({"ok": True, "protected": is_protected})


@app.post("/sessions/save")
async def save_session(req: SessionSaveRequest):
    """セッションを保存/更新"""
    # パストラバーサル防止
    if "/" in req.session_id or "\\" in req.session_id or ".." in req.session_id:
        return JSONResponse({"error": "invalid session_id"}, status_code=400)
    session_file = SESSIONS_DIR / f"{req.session_id}.json"
    now = datetime.now().isoformat()
    # タイトル = 最初のユーザーメッセージの先頭50文字
    title = "無題"
    for msg in req.history:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        content = item.get("text", "")
                        break
            if isinstance(content, str) and content.strip():
                title = content.strip()[:50]
                if len(content.strip()) > 50:
                    title += "..."
            break
    created_at = now
    if session_file.exists():
        try:
            existing = json.loads(session_file.read_text(encoding="utf-8"))
            created_at = existing.get("created_at", now)
        except Exception:
            pass
    data = {
        "session_id": req.session_id,
        "title": title,
        "created_at": created_at,
        "updated_at": now,
        "history": req.history,
        "turn_models": req.turn_models,
    }
    session_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _archive_old_sessions()
    return JSONResponse({"ok": True})


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """セッション内容を取得（アーカイブも検索）"""
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        return JSONResponse({"error": "invalid session_id"}, status_code=400)
    session_file = SESSIONS_DIR / f"{session_id}.json"
    if not session_file.exists():
        session_file = ARCHIVE_DIR / f"{session_id}.json"
    if not session_file.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """セッションを削除（アーカイブも対象）"""
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        return JSONResponse({"error": "invalid session_id"}, status_code=400)
    for d in (SESSIONS_DIR, ARCHIVE_DIR):
        f = d / f"{session_id}.json"
        if f.exists():
            f.unlink()
    protected = _load_protected()
    if session_id in protected:
        protected.discard(session_id)
        _save_protected(protected)
    return JSONResponse({"ok": True})


@app.get("/")
async def index():
    return FileResponse("index.html")


@app.get("/drawio-proxy")
async def drawio_proxy(request: Request):
    """draw.io を自サーバー経由で配信し addGCP3Palette polyfill を注入する"""
    from fastapi.responses import Response as FastAPIResponse
    params = str(request.url.query)
    url = f"https://embed.diagrams.net/?{params}" if params else "https://embed.diagrams.net/"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        html = resp.content
        polyfill = b"""<base href="https://embed.diagrams.net/">
<script>
(function(){
  var _eu;
  try {
    Object.defineProperty(window, 'EditorUi', {
      get: function(){ return _eu; },
      set: function(v){
        _eu = v;
        if(v && v.prototype){
          ['addGCP3Palette','addGCP2Palette','addGCPPalette'].forEach(function(m){
            if(!v.prototype[m]) v.prototype[m] = function(){};
          });
        }
      },
      configurable: true, enumerable: true
    });
  } catch(e){}
  var _oa = window.alert;
  window.alert = function(m){
    if(typeof m==='string' && m.indexOf('addGCP')>=0){ return; }
    return _oa && _oa.call(window, m);
  };
})();
</script>"""
        if b"<head>" in html:
            html = html.replace(b"<head>", b"<head>" + polyfill, 1)
        else:
            html = polyfill + html
        return FastAPIResponse(content=html, media_type="text/html",
                               headers={"Cache-Control": "no-store"})
    except Exception as e:
        return FastAPIResponse(content=f"<p>draw.io proxy error: {e}</p>", media_type="text/html")


def _get_mcp_enabled(server_id: str) -> str:
    """mcp_servers.json から指定サーバーの enabled 値を返す"""
    try:
        mcp_conf = json.loads((Path(__file__).parent / "config" / "mcp_servers.json").read_text(encoding="utf-8"))
        for s in mcp_conf.get("servers", []):
            if s.get("id") == server_id:
                return "true" if s.get("enabled") else "false"
    except Exception:
        pass
    return "false"


def _set_mcp_enabled(server_id: str, enabled: bool):
    """mcp_servers.json の指定サーバーの enabled を更新する"""
    conf_path = Path(__file__).parent / "config" / "mcp_servers.json"
    try:
        mcp_conf = json.loads(conf_path.read_text(encoding="utf-8"))
        for s in mcp_conf.get("servers", []):
            if s.get("id") == server_id:
                s["enabled"] = enabled
        conf_path.write_text(json.dumps(mcp_conf, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[setup] mcp_servers.json 更新失敗: {e}")


@app.get("/mcp/servers")
async def mcp_servers_get():
    conf_path = Path(__file__).parent / "config" / "mcp_servers.json"
    try:
        return JSONResponse(json.loads(conf_path.read_text(encoding="utf-8")))
    except Exception:
        return JSONResponse({"servers": []})


@app.post("/mcp/servers")
async def mcp_servers_save(data: dict):
    conf_path = Path(__file__).parent / "config" / "mcp_servers.json"
    try:
        conf_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)
    if sys.platform == "win32":
        import os
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return JSONResponse({"status": "ok"})
    else:
        try:
            subprocess.Popen(["sudo", "systemctl", "restart", "ai-codeagent"])
            return JSONResponse({"status": "ok"})
        except Exception as e:
            return JSONResponse({"status": "ok", "warning": f"再起動失敗: {e}"})


@app.get("/inbox/status")
async def inbox_status():
    return JSONResponse(inbox_get_status())


@app.get("/inbox/draft-alerts")
async def inbox_draft_alerts():
    return JSONResponse({"stale": get_stale_drafts()})


@app.post("/inbox/scan")
async def inbox_scan_trigger():
    """即時スキャンを実行して検出件数を返す。"""
    from tools.inbox_worker import scan_inbox, accept_request
    pending = scan_inbox()
    if not pending:
        return JSONResponse({"triggered": 0, "message": "inbox に新しいリクエストはありません"})
    for md_path in pending:
        processing_path = accept_request(md_path)
        if processing_path:
            asyncio.create_task(_inbox_process(processing_path))
    return JSONResponse({"triggered": len(pending), "message": f"{len(pending)} 件の処理を開始しました"})


@app.get("/setup")
async def setup_page():
    return FileResponse("setup.html")


@app.get("/setup/current")
async def setup_current():
    """現在の .env 値を返す（APIキーはマスク）"""
    def mask(v: str) -> str:
        """末尾4文字を残して *** でマスク（フィールドに値として表示し、未変更時に保持する）"""
        return "***" + v[-4:] if len(v) > 4 else ("***" if v else "")

    env_path = Path(__file__).parent / ".env"
    raw = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                raw[k.strip()] = v.strip()

    # providers: 設定済みプロバイダーを順番に収集
    providers = []

    # Azure OpenAI（常に1番目・未設定でも空エントリを返す）
    providers.append({
        "type":        "azure_openai",
        "name":        raw.get("AZURE_OPENAI_NAME", "") or "Azure OpenAI",
        "endpoint":    raw.get("AZURE_OPENAI_ENDPOINT", ""),
        "api_key":     mask(raw.get("AZURE_OPENAI_API_KEY", "")),
        "api_key_set": bool(raw.get("AZURE_OPENAI_API_KEY")),
        "deployment":  raw.get("AZURE_OPENAI_DEPLOYMENT", ""),
        "deployments": raw.get("AZURE_OPENAI_DEPLOYMENTS", ""),
        "api_version": raw.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
    })

    # Azure AI Foundry インスタンス 1
    if raw.get("FOUNDRY_ENDPOINT"):
        providers.append({
            "type":        "azure_foundry",
            "name":        raw.get("FOUNDRY_NAME", "") or "Azure AI Foundry",
            "endpoint":    raw.get("FOUNDRY_ENDPOINT", ""),
            "api_key":     mask(raw.get("FOUNDRY_API_KEY", "")),
            "api_key_set": bool(raw.get("FOUNDRY_API_KEY")),
            "model":       raw.get("FOUNDRY_MODEL", ""),
            "models":      raw.get("FOUNDRY_MODELS", ""),
            "api_version": raw.get("FOUNDRY_API_VERSION", "2024-12-01-preview"),
        })
    # Azure AI Foundry インスタンス 2, 3, ...
    n = 2
    while raw.get(f"FOUNDRY_{n}_ENDPOINT"):
        providers.append({
            "type":        "azure_foundry",
            "name":        raw.get(f"FOUNDRY_{n}_NAME", "") or f"Azure AI Foundry {n}",
            "endpoint":    raw.get(f"FOUNDRY_{n}_ENDPOINT", ""),
            "api_key":     mask(raw.get(f"FOUNDRY_{n}_API_KEY", "")),
            "api_key_set": bool(raw.get(f"FOUNDRY_{n}_API_KEY")),
            "model":       raw.get(f"FOUNDRY_{n}_MODEL", ""),
            "models":      raw.get(f"FOUNDRY_{n}_MODELS", ""),
            "api_version": raw.get(f"FOUNDRY_{n}_API_VERSION", "2024-12-01-preview"),
        })
        n += 1

    # Google Gemini
    if raw.get("GEMINI_API_KEY"):
        providers.append({
            "type":        "gemini",
            "api_key":     mask(raw.get("GEMINI_API_KEY", "")),
            "api_key_set": bool(raw.get("GEMINI_API_KEY")),
            "models":      raw.get("GEMINI_MODELS", ""),
        })

    # 本家 OpenAI
    if raw.get("OPENAI_API_KEY"):
        providers.append({
            "type":        "openai",
            "api_key":     mask(raw.get("OPENAI_API_KEY", "")),
            "api_key_set": bool(raw.get("OPENAI_API_KEY")),
            "model":       raw.get("OPENAI_MODEL", "gpt-4.5"),
            "models":      raw.get("OPENAI_MODELS", ""),
        })

    return JSONResponse({
        "providers": providers,
        "agent": {
            "name":    raw.get("AGENT_NAME", ""),
            "workdir": raw.get("ALLOWED_WORK_DIR", "./workspace"),
            "timeout": raw.get("COMMAND_TIMEOUT_SECONDS", "30"),
        },
        "gitlab": {
            "user":    raw.get("GITLAB_USER", ""),
            "pat":     mask(raw.get("GITLAB_PAT", "")),
            "pat_set": bool(raw.get("GITLAB_PAT")),
            "email":   subprocess.run(["git", "config", "--global", "user.email"],
                           capture_output=True, text=True).stdout.strip(),
        },
        "searxng": {
            "url":     raw.get("SEARXNG_BASE_URL", "http://localhost:8888"),
            "enabled": raw.get("SEARXNG_ENABLED", "false"),
            "tavily_api_key": mask(raw.get("TAVILY_API_KEY", "")),
            "tavily_api_key_set": bool(raw.get("TAVILY_API_KEY")),
            "web_research_provider": raw.get("WEB_RESEARCH_PROVIDER", "tavily"),
            "openai_api_key_set": bool(raw.get("OPENAI_API_KEY")),
        },
        "responses_api": {
            "enabled":     raw.get("RESPONSES_API_ENABLED", "false"),
            "endpoint":    raw.get("RESPONSES_API_ENDPOINT", ""),
            "api_key":     mask(raw.get("RESPONSES_API_KEY", "")),
            "api_key_set": bool(raw.get("RESPONSES_API_KEY")),
            "model":       raw.get("RESPONSES_API_MODEL", ""),
            "api_version": raw.get("RESPONSES_API_VERSION", ""),
        },
        "rag_embed": {
            "enabled":     raw.get("RAG_ENABLED", "true"),
            "mode":        raw.get("RAG_EMBED_MODE", "default"),
            "endpoint":    raw.get("RAG_EMBED_ENDPOINT", ""),
            "api_key":     mask(raw.get("RAG_EMBED_API_KEY", "")),
            "api_key_set": bool(raw.get("RAG_EMBED_API_KEY")),
            "deployment":  raw.get("RAG_EMBED_DEPLOYMENT", ""),
            "api_version": raw.get("RAG_EMBED_API_VERSION", "2024-02-01"),
        },
        "image_gen": {
            "provider":         raw.get("IMAGE_PROVIDER", "openai"),
            "model":            raw.get("IMAGE_MODEL", "gpt-image-2"),
            "quality":          raw.get("IMAGE_QUALITY", "medium"),
            "size":             raw.get("IMAGE_SIZE", "1024x1024"),
            "inherit":          raw.get("IMAGE_INHERIT", "true"),
            "openai_api_key_set":   bool(raw.get("IMAGE_OPENAI_API_KEY")),
            "gemini_api_key_set":   bool(raw.get("IMAGE_GEMINI_API_KEY")),
            "azure_endpoint":       raw.get("IMAGE_AZURE_ENDPOINT", ""),
            "azure_api_key_set":    bool(raw.get("IMAGE_AZURE_API_KEY")),
            "azure_api_version":    raw.get("IMAGE_AZURE_API_VERSION", ""),
            "foundry_endpoint":     raw.get("IMAGE_FOUNDRY_ENDPOINT", ""),
            "foundry_api_key_set":  bool(raw.get("IMAGE_FOUNDRY_API_KEY")),
            "foundry_api_version":  raw.get("IMAGE_FOUNDRY_API_VERSION", ""),
            "watermark_enabled":   raw.get("WATERMARK_ENABLED", "false"),
            "watermark_text":      raw.get("WATERMARK_TEXT", "AI Generated"),
            "watermark_position":  raw.get("WATERMARK_POSITION", "bottomright"),
            "watermark_color":     raw.get("WATERMARK_COLOR", "#ffffff"),
            "watermark_opacity":   raw.get("WATERMARK_OPACITY", "0.6"),
            "watermark_font_size": raw.get("WATERMARK_FONT_SIZE", "0"),
        },
        "email_notify": {
            "address":      raw.get("NOTIFY_EMAIL", ""),
            "password_set": bool(raw.get("NOTIFY_EMAIL_PASSWORD")),
            "to":           raw.get("NOTIFY_EMAIL_TO", ""),
            "enabled":      raw.get("NOTIFY_EMAIL_ENABLED", "false"),
        },
        "obsidian": {
            "vault_path":    raw.get("OBSIDIAN_VAULT_PATH", ""),
            "mcp_enabled":   _get_mcp_enabled("obsidian"),
            "inbox_enabled": raw.get("OBSIDIAN_INBOX_ENABLED", "false"),
            "inbox_poll_sec": raw.get("OBSIDIAN_INBOX_POLL_SEC", "900"),
        },
    })


@app.get("/setup/fetch-models")
async def setup_fetch_models(type: str, endpoint: str = ""):
    """セットアップ画面用: .env に保存済みのキーを使ってモデル/デプロイ一覧を取得"""
    env_path = Path(__file__).parent / ".env"
    raw: dict = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                raw[k.strip()] = v.strip().strip('"').strip("'")

    try:
        if type == "azure_openai":
            api_key = raw.get("AZURE_OPENAI_API_KEY", "")
            ep = (endpoint or raw.get("AZURE_OPENAI_ENDPOINT", "")).rstrip("/")
            api_ver = raw.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
            if not ep or not api_key:
                return JSONResponse({"error": "エンドポイントまたはAPIキーが未設定です"}, status_code=400)
            url = f"{ep}/openai/deployments?api-version={api_ver}"
            resp = requests.get(url, headers={"api-key": api_key}, timeout=8, proxies={"http": None, "https": None})
            if resp.status_code == 404:
                # /openai/deployments が使えないリソースの場合は .env の設定値を返す
                saved = raw.get("AZURE_OPENAI_DEPLOYMENTS", raw.get("AZURE_OPENAI_DEPLOYMENT", ""))
                models = [m.strip() for m in saved.split(",") if m.strip()]
                return JSONResponse({"models": models, "note": "deployments API 非対応のため .env の設定値を使用"})
            resp.raise_for_status()
            data = resp.json()
            models = [d["id"] for d in data.get("value", [])]

        elif type == "azure_foundry":
            # endpoint で一致するFoundryインスタンスを探す
            api_key = ""
            api_ver = ""
            matched_ep = ""
            for prefix in ["FOUNDRY"] + [f"FOUNDRY_{n}" for n in range(2, 10)]:
                ep = raw.get(f"{prefix}_ENDPOINT", "").rstrip("/")
                if not ep:
                    continue
                if not endpoint or ep == endpoint.rstrip("/"):
                    api_key = raw.get(f"{prefix}_API_KEY", "")
                    api_ver = raw.get(f"{prefix}_API_VERSION", "2024-12-01-preview")
                    matched_ep = ep
                    break
            if not matched_ep or not api_key:
                return JSONResponse({"error": "エンドポイントまたはAPIキーが未設定です"}, status_code=400)
            # Foundry は /openai/deployments か /v1/models の両方を試みる
            url = f"{matched_ep}/openai/deployments?api-version={api_ver}"
            resp = requests.get(url, headers={"api-key": api_key}, timeout=8, proxies={"http": None, "https": None})
            resp.raise_for_status()
            data = resp.json()
            if "value" in data:
                models = [d["id"] for d in data.get("value", [])]
            else:
                models = [m["id"] for m in data.get("data", [])]

        elif type == "gemini":
            api_key = raw.get("GEMINI_API_KEY", "")
            if not api_key:
                return JSONResponse({"error": "GEMINI_API_KEY が未設定です"}, status_code=400)
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}&pageSize=100"
            resp = requests.get(url, timeout=8, proxies={"http": None, "https": None})
            resp.raise_for_status()
            data = resp.json()
            models = [m["name"].replace("models/", "") for m in data.get("models", [])
                      if "generateContent" in m.get("supportedGenerationMethods", [])]

        elif type == "openai":
            api_key = raw.get("OPENAI_API_KEY", "")
            if not api_key:
                return JSONResponse({"error": "OPENAI_API_KEY が未設定です"}, status_code=400)
            resp = requests.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=8,
                proxies={"http": None, "https": None},
            )
            resp.raise_for_status()
            data = resp.json()
            models = sorted([m["id"] for m in data.get("data", []) if "gpt" in m["id"] or m["id"].startswith("o")])

        else:
            return JSONResponse({"error": f"未対応のtype: {type}"}, status_code=400)

        return JSONResponse({"models": models})
    except requests.HTTPError as e:
        return JSONResponse({"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


class SetupSaveRequest(BaseModel):
    providers: list = []  # 統合プロバイダーリスト（新形式）
    agent: dict
    gitlab: dict
    searxng: dict
    responses_api: dict = {}
    rag_embed: dict = {}
    image_gen: dict = {}
    email_notify: dict = {}
    obsidian: dict = {}


@app.post("/setup/save")
async def setup_save(req: SetupSaveRequest):
    """フォームの値を .env に書き込んでサービスを再起動する"""
    env_path = Path(__file__).parent / ".env"

    # 既存 .env を全キー辞書として読み込む（フォールバック用）
    existing_raw: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing_raw[k.strip()] = v.strip()

    # 既存 .env を読んで「既知キー以外のコメント行・カスタム行」を保持
    existing_lines = []
    known_prefixes = (
        "AZURE_OPENAI_", "FOUNDRY", "GEMINI_", "OPENAI_", "AGENT_NAME", "ALLOWED_WORK_DIR",
        "COMMAND_TIMEOUT_SECONDS", "GITLAB_", "SEARXNG_", "TAVILY_", "RESPONSES_API_",
        "RAG_ENABLED", "RAG_EMBED_",
        "IMAGE_",
        "NOTIFY_",
        "OBSIDIAN_",
        "no_proxy", "NO_PROXY",
    )
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                existing_lines.append(line)  # コメント・空行は保持
            elif not any(stripped.startswith(p) for p in known_prefixes):
                existing_lines.append(line)  # 未知キーも保持

    def env_val(new_val: str, env_key: str, default: str = "") -> str:
        """新値が空・未送信の場合は既存 .env 値 → デフォルト値の順でフォールバック。
        APIキーのマスク値（***）も既存値で補完する。"""
        if new_val and "***" not in new_val:
            return new_val
        return existing_raw.get(env_key, default)

    def api_key_val(new_val: str, key_in_env: str) -> str:
        return env_val(new_val, key_in_env, "")

    lines = ["# AI Code Agent 設定ファイル（/setup で生成）", ""]

    # providers リストを種別ごとに分類して .env に書き込む
    foundry_count = 0
    for prov in req.providers:
        ptype = prov.get("type", "")
        if ptype == "azure_openai":
            lines += [
                "# Azure OpenAI",
                f"AZURE_OPENAI_NAME={prov.get('name','')}",
                f"AZURE_OPENAI_API_KEY={api_key_val(prov.get('api_key',''), 'AZURE_OPENAI_API_KEY')}",
                f"AZURE_OPENAI_ENDPOINT={prov.get('endpoint','')}",
                f"AZURE_OPENAI_DEPLOYMENTS={prov.get('deployments','')}",
                f"AZURE_OPENAI_DEPLOYMENT={prov.get('deployments','').split(',')[0].strip() if prov.get('deployments') else ''}",
                f"AZURE_OPENAI_API_VERSION={prov.get('api_version','2025-01-01-preview')}",
                "",
            ]
        elif ptype == "azure_foundry":
            prefix = "FOUNDRY" if foundry_count == 0 else f"FOUNDRY_{foundry_count + 1}"
            label = "" if foundry_count == 0 else f" {foundry_count + 1}"
            lines += [
                f"# Azure AI Foundry{label}",
                f"{prefix}_NAME={prov.get('name','')}",
                f"{prefix}_ENDPOINT={prov.get('endpoint','')}",
                f"{prefix}_API_KEY={api_key_val(prov.get('api_key',''), prefix+'_API_KEY')}",
                f"{prefix}_MODELS={prov.get('models','')}",
                f"{prefix}_MODEL={prov.get('models','').split(',')[0].strip() if prov.get('models') else prov.get('model','')}",
                f"{prefix}_API_VERSION={prov.get('api_version','2024-12-01-preview')}",
                "",
            ]
            foundry_count += 1
        elif ptype == "gemini":
            # model（選択値）が先頭になるよう models を整理
            sel_model = prov.get('model', '')
            models_str = prov.get('models', '')
            if sel_model and models_str:
                parts = [m.strip() for m in models_str.split(',') if m.strip()]
                ordered = [sel_model] + [m for m in parts if m != sel_model]
                models_str = ','.join(ordered)
            elif sel_model:
                models_str = sel_model
            lines += [
                "# Google Gemini",
                f"GEMINI_API_KEY={api_key_val(prov.get('api_key',''), 'GEMINI_API_KEY')}",
                f"GEMINI_MODELS={models_str}",
                "",
            ]
        elif ptype == "openai":
            sel_model = prov.get('model', '')
            models_str = prov.get('models', '')
            if sel_model and models_str:
                parts = [m.strip() for m in models_str.split(',') if m.strip()]
                ordered = [sel_model] + [m for m in parts if m != sel_model]
                models_str = ','.join(ordered)
            elif sel_model:
                models_str = sel_model
            lines += [
                "# 本家 OpenAI",
                f"OPENAI_API_KEY={api_key_val(prov.get('api_key',''), 'OPENAI_API_KEY')}",
                f"OPENAI_MODEL={sel_model}",
                f"OPENAI_MODELS={models_str}",
                "",
            ]

    # エージェント設定
    ag = req.agent
    lines += [
        "# エージェント設定",
        f"AGENT_NAME={ag.get('name','')}",
        f"ALLOWED_WORK_DIR={ag.get('workdir','./workspace')}",
        f"COMMAND_TIMEOUT_SECONDS={ag.get('timeout','30')}",
        "",
    ]

    # GitLab
    gl = req.gitlab
    lines += [
        "# GitLab 連携",
        f"GITLAB_USER={gl.get('user','')}",
        f"GITLAB_PAT={api_key_val(gl.get('pat',''), 'GITLAB_PAT')}",
        "",
    ]
    # git config --global user.name / user.email を更新
    git_user = gl.get('user', '').strip()
    git_email = gl.get('email', '').strip()
    if git_user:
        subprocess.run(["git", "config", "--global", "user.name", git_user], check=False)
    if git_email:
        subprocess.run(["git", "config", "--global", "user.email", git_email], check=False)
        repo_dir = str(Path(__file__).parent)
        subprocess.run(["git", "-C", repo_dir, "config", "--unset", "user.email"], check=False)

    # SearXNG / 検索バックエンド
    sx = req.searxng
    tavily_key = api_key_val(sx.get("tavily_api_key", ""), "TAVILY_API_KEY")
    lines += [
        "# SearXNG 検索バックエンド",
        f"SEARXNG_BASE_URL={sx.get('url','http://localhost:8888')}",
        f"SEARXNG_ENABLED={sx.get('enabled','false')}",
        "# Tavily Search API (省略可・無料1000クエリ/月・カード不要)",
    ]
    if tavily_key:
        lines.append(f"TAVILY_API_KEY={tavily_key}")
    lines += [
        f"WEB_RESEARCH_PROVIDER={sx.get('web_research_provider', 'tavily')}",
        "",
    ]

    # Responses API サブエージェント
    ra = req.responses_api
    if ra:
        ra_key = api_key_val(ra.get("api_key", ""), "RESPONSES_API_KEY")
        lines += [
            "# Responses API サブエージェント（コード生成特化モデル）",
            f"RESPONSES_API_ENABLED={ra.get('enabled', 'false')}",
            f"RESPONSES_API_ENDPOINT={ra.get('endpoint', '')}",
        ]
        if ra_key:
            lines.append(f"RESPONSES_API_KEY={ra_key}")
        lines += [
            f"RESPONSES_API_MODEL={ra.get('model', '')}",
            f"RESPONSES_API_VERSION={ra.get('api_version', '')}",
            "",
        ]

    # RAG 埋め込みモデル
    re = req.rag_embed
    if re:
        re_key = api_key_val(re.get("api_key", ""), "RAG_EMBED_API_KEY")
        lines += [
            "# RAG 埋め込みモデル",
            f"RAG_ENABLED={re.get('enabled', 'true')}",
            f"RAG_EMBED_MODE={re.get('mode', 'default')}",
            f"RAG_EMBED_ENDPOINT={re.get('endpoint', '')}",
            f"RAG_EMBED_DEPLOYMENT={re.get('deployment', '')}",
            f"RAG_EMBED_API_VERSION={re.get('api_version', '2024-02-01')}",
        ]
        if re_key:
            lines.append(f"RAG_EMBED_API_KEY={re_key}")
        lines.append("")

    # 画像生成設定
    ig = req.image_gen or {}
    ig_inherit = ig.get("inherit", "true")
    lines += [
        "# 画像生成設定",
        f"IMAGE_PROVIDER={env_val(ig.get('provider',''), 'IMAGE_PROVIDER', 'openai')}",
        f"IMAGE_MODEL={env_val(ig.get('model',''), 'IMAGE_MODEL', 'gpt-image-2')}",
        f"IMAGE_QUALITY={env_val(ig.get('quality',''), 'IMAGE_QUALITY', 'medium')}",
        f"IMAGE_SIZE={env_val(ig.get('size',''), 'IMAGE_SIZE', '1024x1024')}",
        f"IMAGE_INHERIT={ig_inherit if ig_inherit else 'true'}",
        f"WATERMARK_ENABLED={ig.get('watermark_enabled', 'false')}",
        f"WATERMARK_TEXT={ig.get('watermark_text', 'AI Generated')}",
        f"WATERMARK_POSITION={ig.get('watermark_position', 'bottomright')}",
        f"WATERMARK_COLOR={ig.get('watermark_color', '#ffffff')}",
        f"WATERMARK_OPACITY={ig.get('watermark_opacity', '0.6')}",
        f"WATERMARK_FONT_SIZE={ig.get('watermark_font_size', '0')}",
    ]
    if ig.get("azure_endpoint"):
        lines.append(f"IMAGE_AZURE_ENDPOINT={ig['azure_endpoint']}")
    if ig.get("azure_api_version"):
        lines.append(f"IMAGE_AZURE_API_VERSION={ig['azure_api_version']}")
    if ig.get("foundry_endpoint"):
        lines.append(f"IMAGE_FOUNDRY_ENDPOINT={ig['foundry_endpoint']}")
    if ig.get("foundry_api_version"):
        lines.append(f"IMAGE_FOUNDRY_API_VERSION={ig['foundry_api_version']}")
    ig_openai_key = env_val(ig.get("openai_api_key", ""), "IMAGE_OPENAI_API_KEY")
    if ig_openai_key:
        lines.append(f"IMAGE_OPENAI_API_KEY={ig_openai_key}")
    ig_gemini_key = env_val(ig.get("gemini_api_key", ""), "IMAGE_GEMINI_API_KEY")
    if ig_gemini_key:
        lines.append(f"IMAGE_GEMINI_API_KEY={ig_gemini_key}")
    ig_azure_key = env_val(ig.get("azure_api_key", ""), "IMAGE_AZURE_API_KEY")
    if ig_azure_key:
        lines.append(f"IMAGE_AZURE_API_KEY={ig_azure_key}")
    ig_foundry_key = env_val(ig.get("foundry_api_key", ""), "IMAGE_FOUNDRY_API_KEY")
    if ig_foundry_key:
        lines.append(f"IMAGE_FOUNDRY_API_KEY={ig_foundry_key}")
    lines.append("")

    # メール通知
    em = req.email_notify
    em_password = api_key_val(em.get("password", ""), "NOTIFY_EMAIL_PASSWORD")
    lines += [
        "# メール通知",
        f"NOTIFY_EMAIL_ENABLED={em.get('enabled', 'false')}",
    ]
    if em.get("address"):
        lines.append(f"NOTIFY_EMAIL={em['address']}")
    if em_password:
        lines.append(f"NOTIFY_EMAIL_PASSWORD={em_password}")
    if em.get("to"):
        lines.append(f"NOTIFY_EMAIL_TO={em['to']}")
    lines.append("")

    # Obsidian 連携
    ob = req.obsidian
    if ob.get("vault_path"):
        lines.append("# Obsidian 連携")
        lines.append(f"OBSIDIAN_VAULT_PATH={ob['vault_path']}")
        if ob.get("inbox_enabled") in ("true", "false"):
            lines.append(f"OBSIDIAN_INBOX_ENABLED={ob['inbox_enabled']}")
        if ob.get("inbox_poll_sec"):
            lines.append(f"OBSIDIAN_INBOX_POLL_SEC={ob['inbox_poll_sec']}")
        lines.append("")
    if ob.get("mcp_enabled") in ("true", "false"):
        _set_mcp_enabled("obsidian", ob["mcp_enabled"] == "true")

    # プロキシ行（既存から保持）
    proxy_lines = [l for l in existing_lines if "proxy" in l.lower() or "PROXY" in l]
    if proxy_lines:
        lines += ["# プロキシバイパス"] + proxy_lines + [""]

    env_path.write_text("\n".join(lines))

    # サービスを再起動
    if sys.platform == "win32":
        # Windows: tray.py の _monitor が停止を検知して自動再起動する
        import os
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return JSONResponse({"status": "ok"})
    else:
        # Linux/WSL: systemd 経由で再起動
        try:
            subprocess.Popen(["sudo", "systemctl", "restart", "ai-codeagent"])
            return JSONResponse({"status": "ok"})
        except Exception as e:
            return JSONResponse({"status": "ok", "warning": f"再起動失敗: {e}"})


@app.get("/setup/browse-dir")
async def browse_dir(path: str = ""):
    """ディレクトリブラウザ用: 指定パス(WindowsパスまたはWSLパス)の直下フォルダ一覧を返す"""
    from config import _normalize_to_wsl_path
    def _to_win(wsl_str: str) -> str:
        """WSLパス → Windowsパス表記（/mnt/c/... → C:\...）"""
        if wsl_str.startswith("/mnt/") and len(wsl_str) > 5 and wsl_str[5] != "/":
            drive = wsl_str[5].upper()
            rest  = wsl_str[6:].replace("/", "\\")
            return f"{drive}:{rest or chr(92)}"
        return wsl_str

    if not path:
        # ルート: Windowsドライブ一覧 + WSLホーム
        entries = []
        home = Path.home()
        entries.append({"name": f"ホーム ({home})", "wsl_path": str(home), "win_path": str(home)})
        mnt = Path("/mnt")
        if mnt.exists():
            for p in sorted(mnt.iterdir()):
                if p.is_dir() and len(p.name) == 1 and p.name.isalpha():
                    drive = p.name.upper()
                    entries.append({"name": f"ドライブ {drive}:\\", "wsl_path": str(p), "win_path": f"{drive}:\\"})
        return JSONResponse({"entries": entries, "current_wsl": "", "current_win": "", "parent_wsl": ""})

    try:
        import os as _os
        wsl_path = _normalize_to_wsl_path(path)
        wsl_str  = str(wsl_path)
        entries  = []
        try:
            names = _os.listdir(wsl_str)
        except PermissionError:
            names = []
        for name in sorted(names, key=str.lower):
            if name.startswith("."):
                continue
            full = _os.path.join(wsl_str, name)
            try:
                if _os.path.isdir(full):
                    entries.append({"name": name, "wsl_path": full, "win_path": _to_win(full)})
            except OSError:
                pass
        parent = str(wsl_path.parent) if wsl_str not in ("/", "") else ""
        return JSONResponse({
            "entries": entries,
            "current_wsl": wsl_str,
            "current_win": _to_win(wsl_str),
            "parent_wsl": parent,
        })
    except Exception as e:
        return JSONResponse({"entries": [], "error": str(e), "current_wsl": path, "current_win": path, "parent_wsl": ""})


@app.get("/setup/ansible-creds")
async def ansible_creds_get():
    """workspace/.azure_creds を読んでフィールドごとに返す"""
    from tools.ansible_tools import CREDS_FILE, _parse_creds
    creds = _parse_creds(CREDS_FILE)
    # AZURE_SECRET はマスク（存在チェックのみ）
    if "AZURE_SECRET" in creds:
        creds["AZURE_SECRET"] = "***"
    return JSONResponse(creds)


@app.post("/setup/ansible-creds/save")
async def ansible_creds_save(req: Request):
    """送られてきた値を workspace/.azure_creds に書き込む（再起動不要）"""
    from tools.ansible_tools import CREDS_FILE, _parse_creds
    body = await req.json()

    # AZURE_SECRET が *** のみなら既存値を維持
    existing = _parse_creds(CREDS_FILE)
    if not body.get("AZURE_SECRET") or body["AZURE_SECRET"] == "***":
        if "AZURE_SECRET" in existing:
            body["AZURE_SECRET"] = existing["AZURE_SECRET"]
        else:
            body.pop("AZURE_SECRET", None)

    lines = [
        "# Azure サービスプリンシパル クレデンシャル",
        "# このファイルは Git 管理外です。",
        "",
    ]
    key_order = ["AZURE_SUBSCRIPTION_ID", "AZURE_TENANT", "AZURE_CLIENT_ID",
                 "AZURE_SECRET", "AZURE_ENV_NAME"]
    for k in key_order:
        if k in body and body[k]:
            lines.append(f"{k}={body[k]}")
    # no_proxy / NO_PROXY
    noproxy = body.get("no_proxy", "")
    if noproxy:
        lines += ["", f"no_proxy={noproxy}", f"NO_PROXY={noproxy}"]
    lines.append("")

    try:
        CREDS_FILE.write_text("\n".join(lines), encoding="utf-8")
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/setup/ansible-creds/clear")
async def ansible_creds_clear():
    """workspace/.azure_creds を空にする"""
    from tools.ansible_tools import CREDS_FILE
    try:
        CREDS_FILE.write_text("# Azure サービスプリンシパル クレデンシャル\n# このファイルは Git 管理外です。\n", encoding="utf-8")
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})
