import asyncio
import json
import shutil
import subprocess
import requests
from datetime import datetime
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, UploadFile, File as FastAPIFile
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import AzureOpenAI, OpenAI, AsyncAzureOpenAI, AsyncOpenAI

from config import AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENTS, SEARXNG_ENABLED, GITLAB_PAT, GITLAB_USER, ALLOWED_WORK_DIR, FOUNDRY_ENDPOINT, FOUNDRY_API_KEY, FOUNDRY_MODEL, FOUNDRY_MODELS, FOUNDRY_API_VERSION, FOUNDRY_INSTANCES, GEMINI_API_KEY, GEMINI_MODELS
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
from tools.file_tools import read_file, write_file, edit_file, list_files, glob_files, grep
from tools.command_tools import run_command, BLOCKED_COMMANDS, LONG_RUNNING_CMDS, _split_shell_chain, _truncate_output, _run_bash_sandboxed, _is_permission_error
from tools.web_tools import web_search, web_fetch, web_research
from tools.code_tools import code_lint
from tools.todo_tools import todo_update, todo_read
from tools.workspace_tools import protected_list_read, protected_list_update, protected_list_replace, workspace_cleanup_preview, workspace_backup
from tools.manim_tools import render_manim
from tools.pdf_tools import read_pdf
from tools.ansible_tools import list_ansible_playbooks, run_ansible_playbook
from tools.windows_tools import run_powershell
from tools.background_tools import run_background, check_background, kill_background
from pydantic import BaseModel

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
    else:
        return AsyncOpenAI(
            base_url=_provider_config["url"].rstrip("/") + "/v1",
            api_key=_provider_config["api_key"] or "dummy",
            http_client=httpx.AsyncClient(trust_env=False),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時に一時ディレクトリを自動削除
    import shutil
    for tmp_name in ["_gp_tmp"]:
        tmp_path = ALLOWED_WORK_DIR / tmp_name
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
            print(f"[INFO] 起動時クリーンアップ: {tmp_path} を削除しました")

    # SearXNG を自動起動（Linux/WSL2 のみ。Windows では Docker 依存のためスキップ）
    if SEARXNG_ENABLED and sys.platform != "win32":
        compose_file = Path(__file__).parent / "docker-compose.searxng.yml"
        if compose_file.exists():
            result = subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "up", "-d"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"[WARN] SearXNG 起動失敗: {result.stderr.strip() or result.stdout.strip()}")
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

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
    "protected_list_update": protected_list_update,
    "protected_list_replace": protected_list_replace,
    "workspace_cleanup_preview": workspace_cleanup_preview,
    "render_manim": render_manim,
    "read_pdf": read_pdf,
    "list_ansible_playbooks": list_ansible_playbooks,
    "run_ansible_playbook": run_ansible_playbook,
    "run_powershell": run_powershell,
    "run_background": run_background,
    "check_background": check_background,
    "kill_background": kill_background,
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
]


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


def execute_tool(name: str, arguments: dict) -> str:
    if name not in TOOL_REGISTRY:
        return json.dumps({"error": f"未知のツール: {name}"}, ensure_ascii=False)
    try:
        result = TOOL_REGISTRY[name](**arguments)
        # 検索系ツールで結果が空の場合、LLMがハルシネーションしないよう明示的な警告を付与
        if name in ("web_search", "web_research") and isinstance(result, dict):
            items = result.get("results") or result.get("sources") or []
            if not items or "error" in result:
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
    timeout = 60 if name == "web_research" else 20
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(execute_tool, name, arguments),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return json.dumps(
            {"error": f"ツールがタイムアウトしました ({timeout}秒): {name}"},
            ensure_ascii=False,
        )


async def _stream_command(arguments: dict):
    """run_command をストリーミングで実行する async generator。
    {'type': 'line', 'line': str} を逐次 yield し、最後に {'type': 'result', 'result': str} を yield する。
    bash / ブロックコマンド / && チェーンも適切に処理する。
    """
    import shlex as _shlex
    import sys as _sys
    _ENCODING = "cp932" if _sys.platform == "win32" else "utf-8"

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
            line = line_bytes.decode(_ENCODING, errors="replace")
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
    stderr_str = stderr_bytes.decode(_ENCODING, errors="replace")

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


def _gather_auto_context() -> str:
    """workspace内のgit状態を自動収集する（Claude Code方式）"""
    parts = []

    # ワークスペース直下のgitリポジトリを探して状態を収集
    git_infos = []
    try:
        for p in sorted(ALLOWED_WORK_DIR.iterdir()):
            if not p.is_dir() or p.name.startswith('.'):
                continue
            git_dir = p / ".git"
            if not git_dir.exists():
                continue
            info_lines = [f"[{p.name}]"]
            # ブランチ名
            r = subprocess.run(["git", "branch", "--show-current"],
                capture_output=True, text=True, timeout=5, cwd=str(p))
            if r.returncode == 0 and r.stdout.strip():
                info_lines.append(f"branch: {r.stdout.strip()}")
            # git status --short
            r = subprocess.run(["git", "status", "--short"],
                capture_output=True, text=True, timeout=5, cwd=str(p))
            if r.returncode == 0 and r.stdout.strip():
                info_lines.append("status:\n" + r.stdout.strip()[:400])
            # git diff --stat
            r = subprocess.run(["git", "diff", "--stat"],
                capture_output=True, text=True, timeout=5, cwd=str(p))
            if r.returncode == 0 and r.stdout.strip():
                info_lines.append("diff --stat:\n" + r.stdout.strip()[:400])
            # git log --oneline -5
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

    # workspaceの1階層目ファイル一覧（gitなしのプロジェクトも含む）
    try:
        entries = sorted(ALLOWED_WORK_DIR.iterdir())
        names = []
        for e in entries:
            if e.name.startswith('.'):
                continue
            names.append(e.name + ("/" if e.is_dir() else ""))
        if names:
            parts.append("## Workspace\n" + "  ".join(names))
    except Exception:
        pass

    if not parts:
        return ""
    return "<auto_context>\n" + "\n\n".join(parts) + "\n</auto_context>"


async def agent_stream(user_message: str, history: list, images: list = None, bypass_approval: bool = False, no_think: bool = False):
    try:
        async for chunk in _agent_stream_inner(user_message, history, images or [], bypass_approval, no_think):
            yield chunk
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


async def _agent_stream_inner(user_message: str, history: list, images: list = None, bypass_approval: bool = False, no_think: bool = False):
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
    auto_ctx = _gather_auto_context()
    if auto_ctx:
        if isinstance(user_content, list):
            user_content = [{"type": "text", "text": auto_ctx}] + user_content
        else:
            user_content = f"{auto_ctx}\n\n{user_content}"
    system_prompt = get_system_prompt(bypass_approval)
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

        # tool_start イベントを全件先に送信
        for name, args, _ in parsed_calls:
            yield f"data: {json.dumps({'type': 'tool_start', 'name': name, 'args': args})}\n\n"

        # ツールを実行（run_commandはストリーミング、他は並列）
        _STREAMING_TOOLS = {"run_command"}
        if any(name in _STREAMING_TOOLS for name, _, _ in parsed_calls):
            # ストリーミングツールが含まれる場合は順次実行
            results = []
            for name, args, tc_id in parsed_calls:
                if name in _STREAMING_TOOLS:
                    result_str = None
                    async for chunk in _stream_command(args):
                        if chunk["type"] == "line":
                            yield f"data: {json.dumps({'type': 'tool_stdout', 'line': chunk['line'], 'tool_id': tc_id})}\n\n"
                        elif chunk["type"] == "result":
                            result_str = chunk["result"]
                    results.append(result_str or json.dumps({"error": "ストリーミング結果なし", "stdout": "", "stderr": "", "returncode": -1}))
                else:
                    results.append(await execute_tool_async(name, args))
        else:
            # ストリーミング不要ツールは並列実行
            results = list(await asyncio.gather(*[
                execute_tool_async(name, args) for name, args, _ in parsed_calls
            ]))

        # 結果を順番に処理してメッセージ履歴に追加
        pending_vision_images = []  # render_manim の画像をまとめてvision messageに注入するためのキュー
        for (name, args, tc_id), result in zip(parsed_calls, results):
            tool_result_for_msg = result  # LLM に渡す tool メッセージの内容

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
                "tool_call_id": tc_id,
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
    return StreamingResponse(
        agent_stream(req.message, req.history, req.images, req.bypass_approval, req.no_think),
        media_type="text/event-stream",
    )


@app.get("/skills")
async def get_skills():
    """登録済みスキルの一覧を返す"""
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
    return result


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


class CleanupRequest(BaseModel):
    paths: list


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
async def workspace_upload(file: UploadFile = FastAPIFile(...)):
    """ファイルをworkspaceにアップロードして保存する。バイナリファイル（PDF・Office等）対応。"""
    from tools.file_tools import _resolve_safe_path
    try:
        filename = Path(file.filename).name  # パストラバーサル防止
        target = _resolve_safe_path(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        target.write_bytes(content)
        return JSONResponse({"path": filename, "size": len(content), "error": None})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/workspace/write-raw")
async def workspace_write_raw(req: RawWriteRequest):
    """LLMを経由せずコンテンツをそのままworkspaceに書き込む"""
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


@app.get("/workspace/shells")
async def workspace_shells():
    """workspace内の .sh ファイルを再帰的に列挙する"""
    result = []
    try:
        for p in sorted(ALLOWED_WORK_DIR.rglob("*.sh")):
            rel = p.relative_to(ALLOWED_WORK_DIR)
            result.append(str(rel).replace("\\", "/"))
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


@app.get("/workspace/run-shell")
async def workspace_run_shell(path: str):
    """指定した .sh スクリプトをサンドボックスなし・ユーザー権限で実行し、出力をSSEストリームで返す"""
    from tools.file_tools import _resolve_safe_path
    try:
        resolved = _resolve_safe_path(path)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not resolved.exists() or not resolved.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)
    if resolved.suffix != ".sh":
        return JSONResponse({"error": ".sh ファイルのみ実行できます"}, status_code=400)

    async def stream():
        proc = await asyncio.create_subprocess_exec(
            "bash", str(resolved),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(resolved.parent),
        )
        yield f"data: {json.dumps({'type': 'start', 'path': path})}\n\n"
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip("\n")
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
    """任意のシェルコマンドをサンドボックスなし・ユーザー権限で実行し、出力をSSEストリームで返す"""
    from tools.file_tools import _resolve_safe_path
    if req.cwd:
        try:
            exec_cwd = str(_resolve_safe_path(req.cwd))
        except Exception:
            exec_cwd = str(ALLOWED_WORK_DIR)
    else:
        exec_cwd = str(ALLOWED_WORK_DIR)
    async def stream():
        import sys as _sys
        if _sys.platform == "win32":
            shell_args = ["powershell", "-NoProfile", "-Command", req.command]
        else:
            shell_args = ["bash", "-c", req.command]
        proc = await asyncio.create_subprocess_exec(
            *shell_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=exec_cwd,
        )
        yield f"data: {json.dumps({'type': 'start', 'command': req.command})}\n\n"
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip("\n")
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
    try:
        client = _make_client()
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=_provider_config["model"],
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
    """セッション一覧を取得（最新順、最大100件）"""
    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            turn_count = len([m for m in data.get("history", []) if m.get("role") == "user"])
            sessions.append({
                "session_id": data.get("session_id"),
                "title": data.get("title", "無題"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "turn_count": turn_count,
            })
        except Exception:
            pass
    return JSONResponse(sessions)


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
    return JSONResponse({"ok": True})


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """セッション内容を取得"""
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        return JSONResponse({"error": "invalid session_id"}, status_code=400)
    session_file = SESSIONS_DIR / f"{session_id}.json"
    if not session_file.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """セッションを削除"""
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        return JSONResponse({"error": "invalid session_id"}, status_code=400)
    session_file = SESSIONS_DIR / f"{session_id}.json"
    if session_file.exists():
        session_file.unlink()
    return JSONResponse({"ok": True})


@app.get("/")
async def index():
    return FileResponse("index.html")


@app.get("/setup")
async def setup_page():
    return FileResponse("setup.html")


def _get_git_email() -> str:
    """git config --global user.email を取得。git が PATH にない場合は空文字を返す"""
    try:
        return subprocess.run(
            ["git", "config", "--global", "user.email"],
            capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        return ""


@app.get("/setup/current")
async def setup_current():
    """現在の .env 値を返す（APIキーはマスク）"""
    def mask(v: str) -> str:
        """末尾4文字を残して *** でマスク（フィールドに値として表示し、未変更時に保持する）"""
        return "***" + v[-4:] if len(v) > 4 else ("***" if v else "")

    env_path = Path(__file__).parent / ".env"
    raw = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
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
            "email":   _get_git_email(),
        },
        "searxng": {
            "url":     raw.get("SEARXNG_BASE_URL", "http://localhost:8888"),
            "enabled": raw.get("SEARXNG_ENABLED", "false"),
            "tavily_api_key": mask(raw.get("TAVILY_API_KEY", "")),
            "tavily_api_key_set": bool(raw.get("TAVILY_API_KEY")),
        },
    })


@app.get("/setup/fetch-models")
async def setup_fetch_models(type: str, endpoint: str = ""):
    """セットアップ画面用: .env に保存済みのキーを使ってモデル/デプロイ一覧を取得"""
    env_path = Path(__file__).parent / ".env"
    raw: dict = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
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


@app.post("/setup/save")
async def setup_save(req: SetupSaveRequest):
    """フォームの値を .env に書き込んでサービスを再起動する"""
    env_path = Path(__file__).parent / ".env"

    # 既存 .env を読んで「既知キー以外のコメント行・カスタム行」を保持
    existing_lines = []
    known_prefixes = (
        "AZURE_OPENAI_", "FOUNDRY", "GEMINI_", "AGENT_NAME", "ALLOWED_WORK_DIR",
        "COMMAND_TIMEOUT_SECONDS", "GITLAB_", "SEARXNG_", "TAVILY_", "no_proxy", "NO_PROXY",
    )
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                existing_lines.append(line)  # コメント・空行は保持
            elif not any(stripped.startswith(p) for p in known_prefixes):
                existing_lines.append(line)  # 未知キーも保持

    def api_key_val(new_val: str, key_in_env: str) -> str:
        """新値が *** のみの場合は既存値を維持"""
        if "***" in new_val:
            # 既存 .env から取得
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith(key_in_env + "="):
                        return line.partition("=")[2].strip()
            return ""
        return new_val

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
    lines.append("")

    # プロキシ行（既存から保持）
    proxy_lines = [l for l in existing_lines if "proxy" in l.lower() or "PROXY" in l]
    if proxy_lines:
        lines += ["# プロキシバイパス"] + proxy_lines + [""]

    env_path.write_text("\n".join(lines))

    # systemd サービスを再起動
    try:
        if sys.platform == "win32":
            os._exit(0)  # setup.bat の再起動ループに委ねる
        else:
            subprocess.Popen(["sudo", "systemctl", "restart", "ai-codeagent"])
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"status": "ok", "warning": f"再起動失敗: {e}"})


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
