import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

# 必須: 未設定時は起動時に KeyError で即時エラー
AZURE_OPENAI_API_KEY: str = os.environ["AZURE_OPENAI_API_KEY"]
AZURE_OPENAI_ENDPOINT: str = os.environ["AZURE_OPENAI_ENDPOINT"]

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

# 作業ディレクトリ (絶対パスに正規化)
_work_dir_raw = os.getenv("ALLOWED_WORK_DIR", "./workspace")
ALLOWED_WORK_DIR: Path = Path(_work_dir_raw).resolve()
ALLOWED_WORK_DIR.mkdir(parents=True, exist_ok=True)

COMMAND_TIMEOUT_SECONDS: int = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "30"))

# GitLab 連携 (省略可)
GITLAB_USER: str = os.getenv("GITLAB_USER", "")
GITLAB_PAT: str  = os.getenv("GITLAB_PAT", "")

# SearXNG 検索バックエンド (省略可)
SEARXNG_BASE_URL: str = os.getenv("SEARXNG_BASE_URL", "http://localhost:8888")
SEARXNG_ENABLED: bool = os.getenv("SEARXNG_ENABLED", "false").lower() == "true"
