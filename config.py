import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 必須: 未設定時は起動時に KeyError で即時エラー
AZURE_OPENAI_API_KEY: str = os.environ["AZURE_OPENAI_API_KEY"]
AZURE_OPENAI_ENDPOINT: str = os.environ["AZURE_OPENAI_ENDPOINT"]

# オプション: デフォルト値あり
AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

# 作業ディレクトリ (絶対パスに正規化)
_work_dir_raw = os.getenv("ALLOWED_WORK_DIR", "./workspace")
ALLOWED_WORK_DIR: Path = Path(_work_dir_raw).resolve()
ALLOWED_WORK_DIR.mkdir(parents=True, exist_ok=True)

COMMAND_TIMEOUT_SECONDS: int = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "30"))

# GitLab 連携 (省略可)
GITLAB_USER: str = os.getenv("GITLAB_USER", "")
GITLAB_PAT: str  = os.getenv("GITLAB_PAT", "")
