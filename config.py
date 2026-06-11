import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

APP_VERSION = "1.14.0"

try:
    load_dotenv(override=True, encoding='utf-8')
except Exception:
    # .env のエンコーディングが壊れている場合は無視して起動（/setup で再設定可能）
    pass


def _normalize_to_wsl_path(path_str: str) -> Path:
    """Windows パス・UNC パスを WSL の Linux パスに変換する。
    - C:\\Users\\foo  → /mnt/c/Users/foo
    - \\\\wsl.localhost\\Ubuntu\\home\\foo → /home/foo
    - 既に Linux パスならそのまま resolve
    """
    s = path_str.strip()
    # Windows ドライブパス: C:\ または C:/
    m = re.match(r'^([A-Za-z]):[/\\](.*)$', s)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2).replace('\\', '/')
        return Path(f'/mnt/{drive}/{rest}').resolve()
    # UNC WSL パス: \\wsl.localhost\Ubuntu\... または //wsl.localhost/Ubuntu/...
    normalized = s.replace('\\', '/')
    m2 = re.match(r'^//wsl(?:\.localhost)?/[^/]+(/.*)?$', normalized, re.IGNORECASE)
    if m2:
        rest = m2.group(1) or '/'
        return Path(rest).resolve()
    return Path(s).resolve()

# Azure OpenAI（未設定時は空文字。セットアップウィザードで設定可能）
AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")

# オプション: デフォルト値あり
AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
# カンマ区切りで複数デプロイ名を指定可（例: gpt-5-mini,gpt-4.1）
# 未設定時は AZURE_OPENAI_DEPLOYMENT のみ
_deployments_raw = os.getenv("AZURE_OPENAI_DEPLOYMENTS", "")
AZURE_OPENAI_DEPLOYMENTS: list[str] = (
    [d.strip() for d in _deployments_raw.split(",") if d.strip()]
    if _deployments_raw else [AZURE_OPENAI_DEPLOYMENT]
)
AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

# 作業ディレクトリ（複数対応）
# ALLOWED_WORK_DIRS=./workspace,C:\Users\foo\proj,/home/user/proj のようにカンマ区切りで指定
# 未設定時は ALLOWED_WORK_DIR（後方互換）を使用
_work_dirs_env = os.getenv("ALLOWED_WORK_DIRS", "")
_work_dir_env  = os.getenv("ALLOWED_WORK_DIR", "./workspace")

if _work_dirs_env:
    ALLOWED_WORK_DIRS: list[Path] = [
        _normalize_to_wsl_path(d.strip())
        for d in _work_dirs_env.split(",") if d.strip()
    ]
else:
    ALLOWED_WORK_DIRS = [_normalize_to_wsl_path(_work_dir_env)]

# デフォルト作業ディレクトリ（後方互換・リストの先頭）
ALLOWED_WORK_DIR: Path = ALLOWED_WORK_DIRS[0]
# デフォルトの workspace のみ自動作成（外部プロジェクトは存在前提）
ALLOWED_WORK_DIRS[0].mkdir(parents=True, exist_ok=True)

# Obsidian Vault パス（設定済みの場合は ALLOWED_WORK_DIRS に自動追加）
OBSIDIAN_VAULT_PATH: str = os.getenv("OBSIDIAN_VAULT_PATH", "")
if OBSIDIAN_VAULT_PATH:
    _vault_path = _normalize_to_wsl_path(OBSIDIAN_VAULT_PATH)
    if _vault_path not in ALLOWED_WORK_DIRS:
        ALLOWED_WORK_DIRS.append(_vault_path)

COMMAND_TIMEOUT_SECONDS: int = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "30"))

# ---- サーバーポート（単一ソース）----
# WSL版=systemd の ExecStart / Windows版=tray.py が参照する唯一の定義。
# 変更は .env の APP_PORT で行う（WSL版は ./setup.sh install で対話的に設定可能）。
# 既定はプラットフォーム別: Windows=8001（WSL版と同一PCで併用しても衝突しない）/ それ以外=8000。
APP_PORT: int = int(os.getenv("APP_PORT", "8001" if sys.platform == "win32" else "8000"))

# Obsidian inbox 監視（機能B）
OBSIDIAN_INBOX_ENABLED: bool = os.getenv("OBSIDIAN_INBOX_ENABLED", "false").lower() == "true"
OBSIDIAN_INBOX_POLL_SEC: int = max(60, min(86400, int(os.getenv("OBSIDIAN_INBOX_POLL_SEC", "900"))))

# Azure AI Foundry (省略可) — 後方互換用（= FOUNDRY_INSTANCES[0]）
FOUNDRY_ENDPOINT: str    = os.getenv("FOUNDRY_ENDPOINT", "")
FOUNDRY_API_KEY: str     = os.getenv("FOUNDRY_API_KEY", "")
FOUNDRY_MODEL: str       = os.getenv("FOUNDRY_MODEL", "")
FOUNDRY_API_VERSION: str = os.getenv("FOUNDRY_API_VERSION", "2024-12-01-preview")
_foundry_models_raw = os.getenv("FOUNDRY_MODELS", "")
FOUNDRY_MODELS: list[str] = (
    [m.strip() for m in _foundry_models_raw.split(",") if m.strip()]
    if _foundry_models_raw else ([FOUNDRY_MODEL] if FOUNDRY_MODEL else [])
)

# Azure AI Foundry インスタンス一覧（複数対応）
# 既存 FOUNDRY_* を instance 1 として扱い、FOUNDRY_2_*、FOUNDRY_3_* ... を追加可能
def _parse_foundry_instances() -> list[dict]:
    instances = []
    # インスタンス 1: 既存の FOUNDRY_* (後方互換)
    if FOUNDRY_ENDPOINT:
        instances.append({
            "id": "foundry_1",
            "name": os.getenv("FOUNDRY_NAME", "") or "Azure AI Foundry",
            "endpoint": FOUNDRY_ENDPOINT,
            "api_key": FOUNDRY_API_KEY,
            "models": FOUNDRY_MODELS,
            "default_model": FOUNDRY_MODEL or (FOUNDRY_MODELS[0] if FOUNDRY_MODELS else ""),
            "api_version": FOUNDRY_API_VERSION,
        })
    # インスタンス 2, 3, ... : FOUNDRY_N_ENDPOINT が続く限り読み込む
    n = 2
    while True:
        ep = os.getenv(f"FOUNDRY_{n}_ENDPOINT", "")
        if not ep:
            break
        models_raw = os.getenv(f"FOUNDRY_{n}_MODELS", "")
        default_model = os.getenv(f"FOUNDRY_{n}_MODEL", "")
        models = [m.strip() for m in models_raw.split(",") if m.strip()] if models_raw else ([default_model] if default_model else [])
        instances.append({
            "id": f"foundry_{n}",
            "name": os.getenv(f"FOUNDRY_{n}_NAME", "") or f"Azure AI Foundry {n}",
            "endpoint": ep,
            "api_key": os.getenv(f"FOUNDRY_{n}_API_KEY", ""),
            "models": models,
            "default_model": default_model or (models[0] if models else ""),
            "api_version": os.getenv(f"FOUNDRY_{n}_API_VERSION", "2024-12-01-preview"),
        })
        n += 1
    return instances

FOUNDRY_INSTANCES: list[dict] = _parse_foundry_instances()

# エージェント名（自己紹介時に使う架空の名前）
AGENT_NAME: str = os.getenv("AGENT_NAME", "")

# 本家 OpenAI (省略可)
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str   = os.getenv("OPENAI_MODEL", "gpt-5.4")
_openai_models_raw = os.getenv("OPENAI_MODELS", "")
OPENAI_MODELS: list[str] = (
    [m.strip() for m in _openai_models_raw.split(",") if m.strip()]
    if _openai_models_raw else []
)

# Google Gemini (省略可)
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
_gemini_models_raw = os.getenv("GEMINI_MODELS", "")
GEMINI_MODELS: list[str] = (
    [m.strip() for m in _gemini_models_raw.split(",") if m.strip()]
    if _gemini_models_raw else []
)

# GitLab 連携 (省略可)
GITLAB_USER: str = os.getenv("GITLAB_USER", "")
GITLAB_PAT: str  = os.getenv("GITLAB_PAT", "")

# SearXNG 検索バックエンド (省略可)
SEARXNG_BASE_URL: str = os.getenv("SEARXNG_BASE_URL", "http://localhost:8888")
SEARXNG_ENABLED: bool = os.getenv("SEARXNG_ENABLED", "false").lower() == "true"

# Tavily Search API (省略可・無料1000クエリ/月・カード不要)
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

# Web調査プロバイダー: "tavily" | "deep-research-o4-mini" | "deep-research-o3"
WEB_RESEARCH_PROVIDER: str = os.getenv("WEB_RESEARCH_PROVIDER", "tavily")

# Responses API サブエージェント (省略可・コード生成特化モデル用)
RESPONSES_API_ENABLED: bool = os.getenv("RESPONSES_API_ENABLED", "false").lower() == "true"
RESPONSES_API_ENDPOINT: str = os.getenv("RESPONSES_API_ENDPOINT", "")
RESPONSES_API_KEY: str      = os.getenv("RESPONSES_API_KEY", "")
RESPONSES_API_MODEL: str    = os.getenv("RESPONSES_API_MODEL", "")
RESPONSES_API_VERSION: str  = os.getenv("RESPONSES_API_VERSION", "")

# 保存時の自動構文チェック（検証ループ）。write_file/edit_file 後に構文チェックを走らせ結果を注入する
VERIFY_ON_WRITE_ENABLED: bool = os.getenv("VERIFY_ON_WRITE", "true").lower() == "true"

# RAG 埋め込みモデル設定 (省略可)
# RAG_EMBED_MODE: "default"（ChromaDB内蔵）または "azure"（Azure OpenAI text-embedding）
RAG_ENABLED: bool           = os.getenv("RAG_ENABLED", "true").lower() == "true"
RAG_EMBED_MODE: str         = os.getenv("RAG_EMBED_MODE", "default")
RAG_EMBED_ENDPOINT: str     = os.getenv("RAG_EMBED_ENDPOINT", "")
RAG_EMBED_API_KEY: str      = os.getenv("RAG_EMBED_API_KEY", "")
RAG_EMBED_DEPLOYMENT: str   = os.getenv("RAG_EMBED_DEPLOYMENT", "")
RAG_EMBED_API_VERSION: str  = os.getenv("RAG_EMBED_API_VERSION", "2024-12-01-preview")

# 画像生成設定（チャット用プロバイダーとは独立）
IMAGE_PROVIDER: str = os.getenv("IMAGE_PROVIDER", "openai")
IMAGE_MODEL: str    = os.getenv("IMAGE_MODEL", "gpt-image-2")
IMAGE_QUALITY: str  = os.getenv("IMAGE_QUALITY", "medium")
IMAGE_SIZE: str     = os.getenv("IMAGE_SIZE", "1024x1024")
# チャット設定を引き継ぐ場合は true（デフォルト）、別途指定する場合は false
IMAGE_INHERIT: bool = os.getenv("IMAGE_INHERIT", "true").lower() == "true"
# 引き継がない場合の画像生成専用設定
IMAGE_OPENAI_API_KEY: str    = os.getenv("IMAGE_OPENAI_API_KEY", "")
IMAGE_GEMINI_API_KEY: str    = os.getenv("IMAGE_GEMINI_API_KEY", "")
IMAGE_AZURE_ENDPOINT: str    = os.getenv("IMAGE_AZURE_ENDPOINT", "")
IMAGE_AZURE_API_KEY: str     = os.getenv("IMAGE_AZURE_API_KEY", "")
IMAGE_AZURE_API_VERSION: str = os.getenv("IMAGE_AZURE_API_VERSION", "")
IMAGE_FOUNDRY_ENDPOINT: str    = os.getenv("IMAGE_FOUNDRY_ENDPOINT", "")
IMAGE_FOUNDRY_API_KEY: str     = os.getenv("IMAGE_FOUNDRY_API_KEY", "")
IMAGE_FOUNDRY_API_VERSION: str = os.getenv("IMAGE_FOUNDRY_API_VERSION", "")
# ウォーターマーク設定
WATERMARK_ENABLED: bool   = os.getenv("WATERMARK_ENABLED", "false").lower() == "true"
WATERMARK_TEXT: str       = os.getenv("WATERMARK_TEXT", "AI Generated")
WATERMARK_POSITION: str   = os.getenv("WATERMARK_POSITION", "bottomright")
WATERMARK_COLOR: str      = os.getenv("WATERMARK_COLOR", "#ffffff")
WATERMARK_OPACITY: float  = float(os.getenv("WATERMARK_OPACITY", "0.6"))
WATERMARK_FONT_SIZE: int  = int(os.getenv("WATERMARK_FONT_SIZE", "0"))  # 0 = auto (短辺の4%)

# ---- 非同期エージェントジョブ設定 ----
ASYNC_MAX_JOBS: int = int(os.getenv("ASYNC_MAX_JOBS", "5"))
# BG エージェント1ジョブあたりの最大ターン数（手動⚡・inbox・定時タスク共通）
ASYNC_MAX_TURNS: int = int(os.getenv("ASYNC_MAX_TURNS", "60"))

# ---- 定時実行スケジューラー設定 ----
# サーバー起動中、指定時刻に登録タスクを自動発火する（in-process asyncio ループ）
SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
# 取りこぼし（サーバー停止中に過ぎた予定）をUIで確認する遡及窓（時間）
SCHEDULER_CATCHUP_HOURS: int = int(os.getenv("SCHEDULER_CATCHUP_HOURS", "12"))
# 発火ループのポーリング間隔（秒）
SCHEDULER_TICK_SECONDS: int = int(os.getenv("SCHEDULER_TICK_SECONDS", "30"))

# ---- マルチエージェント設定 ----
# モデル名は現在アクティブなプロバイダーで利用できるものを指定
MULTI_AGENT_MODEL_HIGH: str = os.getenv("MULTI_AGENT_MODEL_HIGH", "gpt-5.4")
MULTI_AGENT_MODEL_MID: str  = os.getenv("MULTI_AGENT_MODEL_MID",  "gpt-5.4-mini")
MULTI_AGENT_MODEL_LOW: str  = os.getenv("MULTI_AGENT_MODEL_LOW",  "gpt-5.4-nano")

# プリセット: 役割 → "high" | "mid" | "low" のマッピング
MULTI_AGENT_PRESETS: dict = {
    "quality": {
        "dispatcher": "high", "design": "high", "coding": "high",
        "debug": "high", "security": "high", "docs": "high",
        "research": "mid", "infra": "high",
    },
    "balance": {
        "dispatcher": "high", "design": "high", "coding": "mid",
        "debug": "high", "security": "high", "docs": "mid",
        "research": "mid", "infra": "high",
    },
    "economy": {
        "dispatcher": "mid", "design": "mid", "coding": "low",
        "debug": "mid", "security": "mid", "docs": "mid",
        "research": "low", "infra": "mid",
    },
}

MULTI_AGENT_MAX_ITERATIONS: int = int(os.getenv("MULTI_AGENT_MAX_ITERATIONS", "20"))
MULTI_AGENT_TIMEOUT_SEC: int    = int(os.getenv("MULTI_AGENT_TIMEOUT_SEC", "300"))
MULTI_AGENT_MAX_RETRIES: int    = int(os.getenv("MULTI_AGENT_MAX_RETRIES", "2"))

# ---- メール通知設定 ----
NOTIFY_EMAIL_ENABLED: bool = os.getenv("NOTIFY_EMAIL_ENABLED", "false").lower() == "true"
NOTIFY_EMAIL: str          = os.getenv("NOTIFY_EMAIL", "")
NOTIFY_EMAIL_PASSWORD: str = os.getenv("NOTIFY_EMAIL_PASSWORD", "")
NOTIFY_EMAIL_TO: str       = os.getenv("NOTIFY_EMAIL_TO", "")
