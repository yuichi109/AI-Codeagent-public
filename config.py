import os
import re
from pathlib import Path
from dotenv import load_dotenv

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

COMMAND_TIMEOUT_SECONDS: int = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "30"))

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

# Responses API サブエージェント (省略可・コード生成特化モデル用)
RESPONSES_API_ENABLED: bool = os.getenv("RESPONSES_API_ENABLED", "false").lower() == "true"
RESPONSES_API_ENDPOINT: str = os.getenv("RESPONSES_API_ENDPOINT", "")
RESPONSES_API_KEY: str      = os.getenv("RESPONSES_API_KEY", "")
RESPONSES_API_MODEL: str    = os.getenv("RESPONSES_API_MODEL", "")
RESPONSES_API_VERSION: str  = os.getenv("RESPONSES_API_VERSION", "")

# RAG 埋め込みモデル設定 (省略可)
# RAG_EMBED_MODE: "default"（ChromaDB内蔵）または "azure"（Azure OpenAI text-embedding）
RAG_ENABLED: bool           = os.getenv("RAG_ENABLED", "true").lower() == "true"
RAG_EMBED_MODE: str         = os.getenv("RAG_EMBED_MODE", "default")
RAG_EMBED_ENDPOINT: str     = os.getenv("RAG_EMBED_ENDPOINT", "")
RAG_EMBED_API_KEY: str      = os.getenv("RAG_EMBED_API_KEY", "")
RAG_EMBED_DEPLOYMENT: str   = os.getenv("RAG_EMBED_DEPLOYMENT", "")
RAG_EMBED_API_VERSION: str  = os.getenv("RAG_EMBED_API_VERSION", "2024-12-01-preview")
