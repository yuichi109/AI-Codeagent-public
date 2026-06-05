"""
Standalone agent runner for background (async) jobs.
Output goes to a callback instead of SSE streaming.

Usage:
    await run_agent(
        job_id="abc123",
        message="...",
        provider_config={...},
        on_chunk=my_async_callback,
        max_turns=30,
    )
"""
import asyncio
import json
import subprocess
from pathlib import Path

import httpx
from openai import AsyncAzureOpenAI, AsyncOpenAI

from config import (
    ALLOWED_WORK_DIR,
    WEB_RESEARCH_PROVIDER,
    RESPONSES_API_ENABLED,
    RESPONSES_API_MODEL,
    AZURE_OPENAI_API_VERSION,
    FOUNDRY_API_VERSION,
    VERIFY_ON_WRITE_ENABLED,
)
from tools.verify_tools import augment_tool_result_with_verify
from prompts import get_system_prompt
from tools.file_tools import read_file, write_file, edit_file, copy_file, move_file, delete_file, list_files, glob_files, grep
from tools.command_tools import run_command, _truncate_output
from tools.web_tools import web_search, web_fetch, web_research
from tools.code_tools import code_lint
from tools.todo_tools import todo_update, todo_read
from tools.workspace_tools import (
    protected_list_read, protected_list_update, protected_list_replace,
    workspace_cleanup_preview, workspace_backup, archive_workspace,
)
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
from tools.rag_tools import rag_save, rag_search, rag_update_status, rag_list
from tools.image_tools import generate_image, edit_image, watermark_image

# -----------------------------------------------------------------------
# Tool registry (no show_mermaid_batch_refine_dialog — server-only UI tool)
# -----------------------------------------------------------------------
TOOL_REGISTRY: dict = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "copy_file": copy_file,
    "move_file": move_file,
    "delete_file": delete_file,
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
    "generate_image": generate_image,
    "edit_image": edit_image,
    "watermark_image": watermark_image,
}

# -----------------------------------------------------------------------
# Tools schema (LLM function-calling spec)
# Mirrors server.py's TOOLS list except show_mermaid_batch_refine_dialog
# -----------------------------------------------------------------------
TOOLS: list = [
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
            "description": "ファイル内の特定文字列を別の文字列に置換します。write_file より安全で効率的です。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "編集するファイルのパス (作業ディレクトリ相対)"},
                    "old_str": {"type": "string", "description": "置換前の文字列"},
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
            "name": "copy_file",
            "description": "ファイルまたはディレクトリをコピーします。ディレクトリは配下を丸ごと再帰コピー（既存にはマージ）。スコープをまたぐコピーに使用してください。src・dst は workspace ルート相対パスで指定すること。",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "コピー元のファイル/ディレクトリのパス（workspace ルート相対）"},
                    "dst": {"type": "string", "description": "コピー先のパス（workspace ルート相対）"},
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "ファイルまたはディレクトリを移動（リネーム）します。ディレクトリは配下を丸ごと移動（移動先が空いている場合のみ）。スコープをまたぐ移動や同スコープ内のリネームに使用してください。src・dst は workspace ルート相対パスで指定すること。",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "移動元のファイル/ディレクトリのパス（workspace ルート相対）"},
                    "dst": {"type": "string", "description": "移動先のパス（workspace ルート相対）"},
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "ファイルまたはディレクトリを削除します。ディレクトリは配下を再帰的に削除します。元に戻せないため必ずユーザーに確認を求めます。path は workspace ルート相対パスで指定すること。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "削除するファイル/ディレクトリのパス（workspace ルート相対）"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "ディレクトリ内のファイル一覧をツリー形式で取得します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "ディレクトリパス。省略するとworkspaceルート。"},
                    "pattern": {"type": "string", "description": "globパターン。例: **/*.py"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "任意のコマンドを実行します（システム破壊コマンドのみ禁止）",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "実行するコマンド"},
                    "work_dir": {"type": "string", "description": "作業ディレクトリ（省略可）"},
                    "description": {"type": "string", "description": "この実行の目的を日本語で一言説明"},
                    "env": {"type": "object", "description": "追加・上書きする環境変数"},
                    "timeout_minutes": {"type": "number", "description": "タイムアウト時間（分）。0で無制限。"},
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
            "description": "検索→上位ページを自動取得→まとめて返す高レベル調査ツール。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "調査クエリ"},
                    "max_sources": {"type": "integer", "description": "取得するソース数 (デフォルト: 3)"},
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
            "description": "glob パターンでファイルパスを検索します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob パターン (例: **/*.py)"},
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
            "description": "ファイル内容を正規表現で検索し、マッチした行をファイルパス・行番号付きで返します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "検索する正規表現パターン"},
                    "path": {"type": "string", "description": "検索ベースディレクトリ (デフォルト: .)"},
                    "file_pattern": {"type": "string", "description": "対象ファイルのglobパターン"},
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
                    "code": {"type": "string", "description": "直接コードを渡す場合"},
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
            "description": "作業タスクリストを作成・更新します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "タスクの配列",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "failed"]},
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
            "description": "現在のタスクリストを読み取ります。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_backup",
            "description": "ワークスペースをバックアップします。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_workspace",
            "description": "現在の作業ディレクトリを Obsidian vault にアーカイブします。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "アーカイブするディレクトリ名"},
                },
                "required": ["scope"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "protected_list_read",
            "description": "ワークスペースの保護リストを読み取ります。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "protected_list_update",
            "description": "保護リストにパスを追加します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "protected_list_replace",
            "description": "保護リストを完全に置き換えます。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_cleanup_preview",
            "description": "ワークスペースを掃除する前の確認リストを生成します。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_manim",
            "description": "Manim コードをレンダリングして PNG 画像を返します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "file_path": {"type": "string"},
                    "scene_name": {"type": "string"},
                    "quality": {"type": "string", "enum": ["l", "m", "h"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_pdf",
            "description": "PDF ファイルのテキストを抽出します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pages": {"type": "string"},
                    "extract_tables": {"type": "boolean"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_pdf",
            "description": "Markdown 風テキストから PDF ファイルを生成します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "title": {"type": "string"},
                    "font_size": {"type": "integer"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_docx",
            "description": "Word ファイル (.docx) のテキストを読み取ります。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_docx",
            "description": "Word ファイル (.docx) を新規作成・上書きします。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "title": {"type": "string"},
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
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
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
                    "path": {"type": "string"},
                    "sheet": {"type": "string"},
                    "max_rows": {"type": "integer"},
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
                    "path": {"type": "string"},
                    "data": {"type": "array", "items": {"type": "array", "items": {}}},
                    "sheet": {"type": "string"},
                    "headers": {"type": "array", "items": {"type": "string"}},
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
                    "path": {"type": "string"},
                    "sheet": {"type": "string"},
                    "cell": {"type": "string"},
                    "row": {"type": "integer"},
                    "col": {"type": "integer"},
                    "value": {"type": "string"},
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
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_pptx",
            "description": "PowerPoint ファイル (.pptx) を新規作成・上書きします。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "slides": {"type": "array", "items": {"type": "object"}},
                    "title": {"type": "string"},
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
                    "path": {"type": "string"},
                    "slide_number": {"type": "integer"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "slide_number", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_ansible_playbooks",
            "description": "Ansible プレイブック一覧を取得します。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_ansible_playbook",
            "description": "指定したプレイブックを実行します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "playbook": {"type": "string"},
                },
                "required": ["playbook"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_powershell",
            "description": "WSL2 から Windows の PowerShell コマンドを実行します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_seconds": {"type": "integer"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gather_host_info",
            "description": "Windows / Linux ホストの情報を一括収集します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "os_type": {"type": "string"},
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "key_file": {"type": "string"},
                    "port": {"type": "integer"},
                    "use_ssl": {"type": "boolean"},
                    "timeout_seconds": {"type": "integer"},
                },
                "required": ["host", "os_type", "username"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "winrm_command",
            "description": "WinRM 経由でリモート Windows に PowerShell コマンドを実行します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "command": {"type": "string"},
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "port": {"type": "integer"},
                    "use_ssl": {"type": "boolean"},
                    "transport": {"type": "string"},
                    "timeout_seconds": {"type": "integer"},
                },
                "required": ["host", "command", "username", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_background",
            "description": "コマンドをバックグラウンドで起動し、即座にジョブIDを返します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "work_dir": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_background",
            "description": "バックグラウンドジョブの状態と出力を確認します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kill_background",
            "description": "バックグラウンドジョブを強制停止します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_save",
            "description": "知見をRAGデータベースに保存します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "record_type": {"type": "string", "enum": ["success", "prohibited", "caution"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "record_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": "RAGデータベースから関連する知見を検索します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "record_type": {"type": "string", "enum": ["success", "prohibited", "caution"]},
                    "n_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_update_status",
            "description": "RAGデータベースの記録を deprecated に変更します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "new_status": {"type": "string", "enum": ["active", "deprecated"]},
                    "reason": {"type": "string"},
                },
                "required": ["record_id", "new_status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_list",
            "description": "RAG記録一覧を表示します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_type": {"type": "string", "enum": ["success", "prohibited", "caution"]},
                    "status": {"type": "string", "enum": ["active", "deprecated", "all"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "テキストプロンプトから画像を生成します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "size": {"type": "string", "enum": ["1024x1024", "1024x1536", "1536x1024"]},
                    "quality": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_image",
            "description": "ワークスペース内の画像を編集・清書します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "prompt": {"type": "string"},
                    "size": {"type": "string", "enum": ["1024x1024", "1024x1536", "1536x1024"]},
                },
                "required": ["image_path", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watermark_image",
            "description": "画像にテキストウォーターマークを追加します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "text": {"type": "string"},
                    "position": {"type": "string", "enum": ["topleft", "topright", "bottomleft", "bottomright", "center"]},
                    "opacity": {"type": "number"},
                },
                "required": ["image_path", "text"],
            },
        },
    },
]

if RESPONSES_API_ENABLED:
    from tools.responses_tools import call_responses_api
    TOOL_REGISTRY["call_responses_api"] = call_responses_api
    TOOLS.append({
        "type": "function",
        "function": {
            "name": "call_responses_api",
            "description": f"コード生成特化モデル（{RESPONSES_API_MODEL or 'Responses API'}）を呼び出してコードを生成します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                },
                "required": ["prompt"],
            },
        },
    })

if WEB_RESEARCH_PROVIDER.startswith("deep-research"):
    _dr_label = WEB_RESEARCH_PROVIDER.replace("deep-research-", "").upper()
    for _tool in TOOLS:
        _fn = _tool.get("function", {})
        if _fn.get("name") == "web_research":
            _fn["description"] = (
                f"【Deep Research ({_dr_label}) 使用中】OpenAI Deep Researchを使った高精度Web調査。"
                f"1ターンにつき必ず1回のみ呼び出すこと。"
            )


# -----------------------------------------------------------------------
# LLM client factory
# -----------------------------------------------------------------------
def _make_async_client(provider_config: dict):
    ptype = provider_config.get("type", "azure")
    if ptype in ("azure", "foundry"):
        return AsyncAzureOpenAI(
            azure_endpoint=provider_config["url"],
            api_key=provider_config["api_key"],
            api_version=provider_config.get("api_version") or (
                FOUNDRY_API_VERSION if ptype == "foundry" else AZURE_OPENAI_API_VERSION
            ),
            http_client=httpx.AsyncClient(trust_env=False),
        )
    elif ptype == "gemini":
        return AsyncOpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=provider_config["api_key"],
            http_client=httpx.AsyncClient(trust_env=False),
        )
    elif ptype == "openai":
        return AsyncOpenAI(
            api_key=provider_config["api_key"],
            http_client=httpx.AsyncClient(trust_env=False),
        )
    else:
        return AsyncOpenAI(
            base_url=provider_config["url"].rstrip("/") + "/v1",
            api_key=provider_config.get("api_key") or "dummy",
            http_client=httpx.AsyncClient(trust_env=False),
        )


# -----------------------------------------------------------------------
# Auto context (git status of workspace)
# -----------------------------------------------------------------------
def _gather_auto_context(workspace_scope: str = "") -> str:
    parts = []
    try:
        if workspace_scope:
            targets = [ALLOWED_WORK_DIR / workspace_scope]
        else:
            targets = [p for p in sorted(ALLOWED_WORK_DIR.iterdir())
                       if p.is_dir() and not p.name.startswith('.')]
    except Exception:
        targets = []

    git_infos = []
    for p in targets:
        if not p.is_dir():
            continue
        if not (p / ".git").exists():
            continue
        info_lines = [f"[{p.name}]"]
        for cmd, label in [
            (["git", "branch", "--show-current"], "branch"),
            (["git", "status", "--short"], "status"),
            (["git", "log", "--oneline", "-3"], "recent commits"),
        ]:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, cwd=str(p))
                if r.returncode == 0 and r.stdout.strip():
                    info_lines.append(f"{label}:\n{r.stdout.strip()[:300]}")
            except Exception:
                pass
        if len(info_lines) > 1:
            git_infos.append("\n".join(info_lines))

    if git_infos:
        parts.append("## Git Status\n" + "\n\n".join(git_infos[:5]))

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


# -----------------------------------------------------------------------
# MCP proxy registration (called by async_worker at startup)
# -----------------------------------------------------------------------
_MCP_PROXY_URL: str = ""


def register_mcp_proxy(proxy_url: str, extra_tools: list) -> None:
    """Register MCP tools so the BG agent can call them via HTTP proxy."""
    global _MCP_PROXY_URL
    _MCP_PROXY_URL = proxy_url.rstrip("/")
    for schema in extra_tools:
        tool_name = schema.get("function", {}).get("name", "")
        if tool_name and tool_name not in TOOL_REGISTRY:
            TOOL_REGISTRY[tool_name] = None  # placeholder; executed via proxy
            TOOLS.append(schema)


# -----------------------------------------------------------------------
# Tool executor
# -----------------------------------------------------------------------
def _execute_tool(name: str, arguments: dict) -> str:
    if name not in TOOL_REGISTRY:
        return json.dumps({"error": f"未知のツール: {name}"}, ensure_ascii=False)
    if TOOL_REGISTRY[name] is None and "__" in name and _MCP_PROXY_URL:
        # MCP tool: call via server proxy (synchronous httpx)
        try:
            resp = httpx.post(
                f"{_MCP_PROXY_URL}/async-agent/call-mcp-tool",
                json={"name": name, "arguments": arguments},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                return json.dumps({"error": data["error"]}, ensure_ascii=False)
            return json.dumps(data["result"], ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"error": f"MCPプロキシエラー: {e}"}, ensure_ascii=False)
    try:
        result = TOOL_REGISTRY[name](**arguments)
        if name in ("web_search", "web_research") and isinstance(result, dict):
            items = result.get("results") or result.get("sources") or []
            has_report = bool(result.get("report"))
            if not has_report and (not items or "error" in result):
                result["_warning"] = (
                    "【重要】検索結果が見つかりませんでした。"
                    "推測や作り話は絶対にしないこと。"
                )
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({
            "error": f"ツール実行エラー: {type(e).__name__}: {e}",
        }, ensure_ascii=False)


async def _execute_tool_async(name: str, arguments: dict) -> str:
    if name in ("generate_image", "edit_image", "watermark_image"):
        _HIGH_RES = {"1536x1024", "1024x1536", "1792x1024", "1024x1792"}
        timeout = 600 if arguments.get("size") in _HIGH_RES else 300
    elif name == "web_research" and WEB_RESEARCH_PROVIDER.startswith("deep-research"):
        timeout = 3600
    elif name == "web_research":
        timeout = 120
    elif name == "render_manim":
        timeout = 130  # 内部タイムアウト120秒 + バッファ
    elif name == "run_ansible_playbook":
        timeout = 310  # 内部タイムアウト300秒 + バッファ
    elif name in ("run_powershell", "winrm_command"):
        timeout = int(arguments.get("timeout_seconds", 30)) + 10
    elif name == "gather_host_info":
        timeout = int(arguments.get("timeout_seconds", 60)) + 10
    elif name == "run_command":
        if "timeout_minutes" in arguments:
            timeout = int(arguments["timeout_minutes"] * 60) + 10
        else:
            timeout = 120
    else:
        timeout = 120  # ハング検出用デフォルト（BGモードは長めに）
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_execute_tool, name, arguments),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return json.dumps({"error": f"ツールがタイムアウトしました ({timeout}秒): {name}"}, ensure_ascii=False)


# -----------------------------------------------------------------------
# Main agent loop
# -----------------------------------------------------------------------
async def run_agent(
    job_id: str,
    message: str,
    provider_config: dict,
    on_chunk,           # async callable(job_id, seq, ctype, content)
    max_turns: int = 30,
    history: list | None = None,
    workspace_scope: str = "",
):
    """
    Run the agent loop for a background job.
    Emits chunks via on_chunk(job_id, seq, ctype, content).

    ctype values:
      "text"       - answer text fragment
      "tool_start" - JSON {"name": ..., "args": ...}
      "tool_end"   - JSON {"name": ..., "result_preview": ...}
      "done"       - job completed normally
      "max_turns"  - max turn limit reached
      "error"      - error occurred
    """
    _seq = 0

    async def emit(ctype: str, content: str):
        nonlocal _seq
        await on_chunk(job_id, _seq, ctype, content)
        _seq += 1

    system_prompt = get_system_prompt(bypass_approval=True)
    auto_ctx = _gather_auto_context(workspace_scope)
    user_content = f"{auto_ctx}\n\n{message}" if auto_ctx else message

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_content})

    tools_enabled = provider_config.get("tools_enabled", True)
    is_local = provider_config.get("type") not in ("azure", "foundry", "gemini", "openai")
    if is_local:
        tools_enabled = provider_config.get("tools_enabled", False)

    client = _make_async_client(provider_config)

    for _turn in range(max_turns):
        await asyncio.sleep(0)  # cooperative yield for cancellation

        create_kwargs: dict = dict(
            model=provider_config["model"],
            messages=messages,
            stream=True,
        )
        if tools_enabled:
            create_kwargs["tools"] = TOOLS
            create_kwargs["tool_choice"] = "auto"

        try:
            stream = await client.chat.completions.create(**create_kwargs)
        except Exception as e:
            await emit("error", f"LLM API エラー: {e}")
            raise

        content_parts: list[str] = []
        tool_calls_map: dict[int, dict] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if getattr(delta, "reasoning_content", None):
                continue

            if delta.content:
                content_parts.append(delta.content)
                await emit("text", delta.content)

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

        if not tool_calls_map:
            await emit("done", "")
            return

        # Build tool call list and update message history
        tool_calls_list = [
            {
                "id": tool_calls_map[i]["id"],
                "type": "function",
                "function": {
                    "name": tool_calls_map[i]["name"],
                    "arguments": tool_calls_map[i]["arguments"],
                },
            }
            for i in sorted(tool_calls_map)
        ]
        messages.append({
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "tool_calls": tool_calls_list,
        })

        parsed_calls = [
            (tc["function"]["name"],
             json.loads(tc["function"]["arguments"] or "{}"),
             tc["id"])
            for tc in tool_calls_list
        ]

        # Notify tool starts
        for name, args, _ in parsed_calls:
            await emit("tool_start", json.dumps({"name": name, "args": args}, ensure_ascii=False))

        # Execute tools concurrently
        _IMAGE_TOOLS = {"generate_image", "edit_image", "watermark_image"}
        tasks = [
            asyncio.create_task(_execute_tool_async(
                name,
                {**args, "_workspace_scope": workspace_scope}
                if name in _IMAGE_TOOLS and workspace_scope and "_workspace_scope" not in args
                else args
            ))
            for name, args, _ in parsed_calls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Add tool results to message history
        for (name, args, tc_id), result in zip(parsed_calls, results):
            if isinstance(result, Exception):
                result = json.dumps({"error": str(result)}, ensure_ascii=False)
            # base64画像データはトークン肥大化を防ぐため除去
            meta = {}
            try:
                r = json.loads(result)
                if isinstance(r, dict):
                    if "image_base64" in r:
                        r.pop("image_base64")
                        result = json.dumps(r, ensure_ascii=False)
                    # 画像系メタは200文字切り詰めで欠落しないよう専用フィールドで渡す
                    for k in ("provider", "model", "saved_path"):
                        if r.get(k):
                            meta[k] = r[k]
            except Exception:
                pass
            # 保存時の自動構文チェック（検証ループ）: 構文エラーを結果に注入してモデルへ突き返す
            if VERIFY_ON_WRITE_ENABLED:
                result, verdict = augment_tool_result_with_verify(name, args, result)
                if verdict:
                    meta["syntax_check"] = verdict
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result,
            })
            preview = result[:200] if len(result) > 200 else result
            await emit("tool_end", json.dumps({"name": name, "result_preview": preview, "meta": meta}, ensure_ascii=False))

    await emit("max_turns", f"最大ターン数 ({max_turns}) に達しました。")
