# セットアップ手順

## 動作確認済み環境

| 項目 | バージョン |
|---|---|
| OS | Windows 11 + WSL2 (Ubuntu 22.04) |
| Python | 3.10 以上 |
| ブラウザ | Chrome / Edge / Firefox (最新版) |

---

## 前提条件

### 1. WSL2 + Ubuntu のインストール (Windows の場合)

```powershell
# PowerShell (管理者) で実行
wsl --install -d Ubuntu
```

インストール後、Ubuntu を起動してユーザー名・パスワードを設定する。

### 2. Python 3.10+ の確認

```bash
python3 --version  # 3.10 以上であること
```

インストールされていない場合:

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
```

### 3. Azure OpenAI リソースの準備

Azure Portal で以下を用意する:

| 必要なもの | 確認場所 |
|---|---|
| API キー | Azure OpenAI → キーとエンドポイント |
| エンドポイント URL | 同上 (例: `https://xxx.openai.azure.com`) |
| デプロイ名 | Azure OpenAI Studio → デプロイ |
| API バージョン | `2025-01-01-preview` 推奨 |

---

## インストール手順

### Step 1: リポジトリをクローン

```bash
git clone https://gitlab.com/yuichi.matsuo/AI-Codeagent.git
cd AI-Codeagent
```

### Step 2: Python 仮想環境を作成

```bash
python3 -m venv venv
source venv/bin/activate        # Linux / WSL
# venv\Scripts\activate         # Windows (cmd) の場合
```

### Step 3: 依存パッケージをインストール

```bash
pip install -r requirements.txt
```

インストールされるパッケージ:

| パッケージ | 用途 |
|---|---|
| `fastapi` | Web API フレームワーク |
| `uvicorn` | ASGI サーバー |
| `openai` | Azure OpenAI クライアント |
| `python-dotenv` | .env ファイルの読み込み |
| `requests` | HTTP クライアント (web_fetch / web_search) |
| `beautifulsoup4` | HTML パース (web_fetch) |
| `ruff` | Python 静的解析 (code_lint) |

### Step 4: 環境変数ファイルを作成

`.env.example` をコピーして `.env` を作成:

```bash
cp .env.example .env
```

`.env` をテキストエディタで開いて値を設定:

```env
AZURE_OPENAI_API_KEY=your_api_key_here        # Azure の API キー
AZURE_OPENAI_ENDPOINT=https://xxx.openai.azure.com  # エンドポイント URL
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini          # デプロイ名
AZURE_OPENAI_API_VERSION=2025-01-01-preview   # API バージョン
ALLOWED_WORK_DIR=./workspace                  # エージェントの作業ディレクトリ
COMMAND_TIMEOUT_SECONDS=30                    # コマンドのタイムアウト秒数
```

> ⚠️ `.env` は `.gitignore` に含まれているため、Git にはコミットされません。
> 各 PC でそれぞれ作成してください。

### Step 5: 動作確認

```bash
source venv/bin/activate
uvicorn server:app --reload
```

以下のようなログが表示されれば起動成功:

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Application startup complete.
```

ブラウザで **http://localhost:8000** を開く。

---

## ディレクトリ構成

```
AI-Codeagent/
├── server.py          # FastAPI エントリポイント
├── config.py          # .env から設定読み込み
├── prompts.py         # システムプロンプト
├── tools/
│   ├── file_tools.py     # read_file / write_file / list_files
│   ├── command_tools.py  # run_command (shell=False + ホワイトリスト)
│   ├── web_tools.py      # web_search / web_fetch
│   └── code_tools.py     # code_lint (ruff)
├── index.html         # チャット UI
├── workspace/         # エージェントの作業ディレクトリ (自動作成)
├── .env               # 環境変数 ※各自作成 (Git 管理外)
├── .env.example       # .env のテンプレート
├── requirements.txt   # 依存パッケージ一覧
└── docs/
    ├── design.md      # 設計ドキュメント
    └── setup.md       # このファイル
```

---

## よくあるエラーと対処法

### `KeyError: 'AZURE_OPENAI_API_KEY'`

`.env` ファイルが存在しない、またはキーが未設定。

```bash
ls -la .env          # ファイルの存在確認
cat .env             # 内容確認 (API キーが入っているか)
```

### `ModuleNotFoundError`

仮想環境が有効になっていない、またはパッケージ未インストール。

```bash
source venv/bin/activate
pip install -r requirements.txt
```

### `Address already in use` (ポート 8000 が使用中)

```bash
# 使用中のプロセスを確認
lsof -i :8000
# または別ポートで起動
uvicorn server:app --reload --port 8080
```

### `ruff: command not found` (code_lint が動かない)

```bash
source venv/bin/activate
pip install ruff
```

---

## 別 PC への移行チェックリスト

- [ ] WSL2 + Ubuntu がインストールされている
- [ ] Python 3.10 以上がある
- [ ] `git clone` でリポジトリを取得した
- [ ] `python3 -m venv venv` で仮想環境を作成した
- [ ] `pip install -r requirements.txt` を実行した
- [ ] `.env` を作成して Azure の情報を設定した
- [ ] `uvicorn server:app --reload` でサーバーが起動した
- [ ] http://localhost:8000 でチャット画面が表示された
