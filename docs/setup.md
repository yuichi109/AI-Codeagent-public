# セットアップ手順

## ブランチ構成

| ブランチ | 対象環境 | 特徴 |
|---|---|---|
| `main` | Linux / WSL2 | systemd サービス・bubblewrap サンドボックス・SearXNG 対応 |
| `for_windows` | Windows ネイティブ | WSL・Docker 不要。`setup.bat` で起動。サンドボックスなし |

---

## Windows ネイティブ版（for_windows ブランチ）

WSL・Docker・管理者権限なしで動作します。

### 動作確認済み環境

| 項目 | バージョン |
|---|---|
| OS | Windows 10 / 11 |
| Python | 3.10 以上（未インストールでも自動導入） |
| Git | 任意（未インストールでも自動導入） |
| ブラウザ | Chrome / Edge / Firefox (最新版) |

### インストール手順

**Step 1: リポジトリをクローン**

Git が未インストールの場合は先にインストールしてください（PowerShell を管理者で実行）：

```powershell
winget install -e --id Git.Git --source winget --accept-package-agreements --accept-source-agreements
```

インストール後は PowerShell を閉じて開き直してから以下を実行してください。

```powershell
git clone -b for_windows https://gitlab.com/yuichi.matsuo/AI-Codeagent.git
cd AI-Codeagent
```

> プライベートリポジトリの場合、ユーザー名に `oauth2`、パスワードに GitLab PAT を入力してください。

**Step 2: setup.bat をダブルクリック**

`setup.bat` が以下を自動で行います:

1. Python / Git が未インストールなら **winget** で自動インストール
2. Python 仮想環境（venv）を作成
3. `requirements.txt` のパッケージをインストール
4. `.env` を `.env.example` から生成
5. サーバーを起動（ポート 8001）

> **winget** は Windows 10 1709 以降 / Windows 11 に標準搭載されています。
> Python や Git が既にインストール済みの場合はスキップされます。

**Step 3: ブラウザで設定**

```
http://localhost:8001/setup
```

Azure OpenAI の API キー・エンドポイント・デプロイ名を入力して保存。
保存後にサーバーが自動再起動して設定が反映されます。

**Step 4: チャット画面を開く**

```
http://localhost:8001
```

### Windows 版の制限事項

| 機能 | 状況 |
|---|---|
| bash スクリプトのサンドボックス実行（bubblewrap） | 非対応 |
| SearXNG（Docker依存） | 非対応 → ddgs / Tavily で代替 |
| systemd 自動起動 | 非対応 → setup.bat を手動起動 |

### サーバーの停止・再起動

- 停止: setup.bat のウィンドウで `Ctrl+C`
- 再起動: setup.bat を再度ダブルクリック（venv 再作成はスキップ）

### ZIPで入れた場合のGit管理への切り替え

ZIP でダウンロードした場合、`git pull` で更新できません。以下の手順で Git 管理に切り替えると以降は `git pull` 一発で更新できます。

**PowerShell でフォルダ内に移動して実行：**

```powershell
git init
git remote add origin https://gitlab.com/yuichi.matsuo/AI-Codeagent-public.git
git fetch origin
git checkout -b for_windows origin/for_windows
```

> ⚠️ `git checkout` を実行すると `.env` 以外のファイルがリポジトリの内容で上書きされます。
> `.env` は Git 管理外のため上書きされません。

**初回のみ（setup.bat を取得して実行）：**

```powershell
git fetch origin
git reset --hard origin/for_windows
setup.bat
```

**以降の更新方法：**

```powershell
git pull
setup.bat
```

### よくあるエラー（Windows版）

**「Python not found」が表示されてインストールが始まらない**

winget が使えない環境（古いWindows 10）の場合:
- https://www.python.org/downloads/ から手動インストール
- インストール時に「Add Python to PATH」にチェック
- インストール後に setup.bat を再実行

**インストール後に「再実行してください」と表示される**

winget でインストール直後は PATH が反映されないことがあります。
setup.bat のウィンドウを閉じて再度ダブルクリックしてください。

**ポート 8001 が使用中**

```powershell
netstat -ano | findstr :8001   # PID を確認
taskkill /PID <PID> /F         # プロセスを終了
```

---

## Linux / WSL2 版（main ブランチ）

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

**HTTPS（推奨・初回）:**

```bash
# 別マシンのWSL2上で
git clone https://gitlab.com/yuichi.matsuo/AI-Codeagent.git
cd AI-Codeagent
./setup.sh install
```

**SSH（SSHキー配置済みの場合）:**

```bash
git clone git@gitlab.com:yuichi.matsuo/AI-Codeagent.git
cd AI-Codeagent
./setup.sh install
```

`setup.sh install` を実行すると、venv作成・依存パッケージインストール・systemdサービス登録まで自動で行われます。
完了後、ブラウザで **http://localhost:8000/setup** を開いて API キーや GitLab PAT などの設定を行ってください。

---

> 以下は手動セットアップの手順です。`setup.sh` を使う場合はスキップ可。

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

ブラウザで **http://localhost:8000/setup** を開いて設定するか、手動で `.env` を作成:

```bash
touch .env
```

`.env` に以下を設定:

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
│   ├── code_tools.py     # code_lint (ruff)
│   ├── workspace_tools.py # protected_list / workspace_cleanup_preview
│   ├── todo_tools.py     # todo_update / todo_read
│   └── manim_tools.py    # render_manim（Manim アニメーション生成）
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

## 社内プロキシ環境での設定

社内ネットワークのプロキシが有効な場合、Azure OpenAI や GitLab への通信がブロックされることがある。
その場合は `.env` に以下を追記してプロキシを迂回させる。

```env
# プロキシバイパス対象ドメイン
no_proxy=*.azure.com,*.openai.azure.com,gitlab.com,api.duckduckgo.com,ja.wikipedia.org,en.wikipedia.org,localhost,127.0.0.1
NO_PROXY=*.azure.com,*.openai.azure.com,gitlab.com,api.duckduckgo.com,ja.wikipedia.org,en.wikipedia.org,localhost,127.0.0.1
```

> **注意:** `no_proxy` (小文字) と `NO_PROXY` (大文字) の両方を書くこと。
> ツール (Python の `requests` ライブラリなど) によって参照する変数が異なるため。

プロキシ経由で通信したいドメインがある場合は、`HTTP_PROXY` / `HTTPS_PROXY` も `.env` に追記する:

```env
HTTP_PROXY=http://proxy.your-company.com:8080
HTTPS_PROXY=http://proxy.your-company.com:8080
```

`.env.example` にも同じ設定がテンプレートとして含まれているので参照すること。

---

## Manim アニメーション生成（オプション）

エージェントに「アニメーションを作って」と頼むと `render_manim` ツールを使ってレンダリング結果を視覚確認しながら改善します。

### 前提システムパッケージ

```bash
sudo apt install -y libcairo2-dev libpango1.0-dev ffmpeg
```

### manim のインストール

```bash
pip install manim
```

> `requirements.txt` には含まれていないため、別途インストールが必要です。
> インストールしない場合でも他の機能は正常に動作します。

### 動作確認

```bash
manim --version
# Manim Community v0.x.x
```

---

## シェルスクリプトのサンドボックス実行 (bubblewrap)

エージェントは `bash script.sh` を **bubblewrap (bwrap)** でサンドボックス化して実行します。
Claude Code が Linux/WSL2 で採用しているのと同じ方式です。

### bubblewrap のインストール

```bash
sudo apt install bubblewrap
```

### サンドボックスの保護範囲

| 保護内容 | 詳細 |
|---|---|
| ファイルシステム | `workspace/` 以外は読み取り専用 |
| ネットワーク | 完全遮断 (`--unshare-net`) |
| `/tmp` | サンドボックス内の独立した tmpfs（ホストに漏れない）|
| プロセス隔離 | 新しいセッション・親プロセス終了で子も終了 |

### 制約

- `bash script.sh` の形式のみ許可（`bash -c "..."` は不可）
- スクリプトは `workspace/` 内に存在する必要がある
- スクリプト内からのネットワークアクセスは不可

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

## systemd による自動起動

WSL2 で systemd が有効な場合（Ubuntu 22.04+）、サーバーを自動起動できます。

### インストール

```bash
sudo cp ~/AI-Codeagent/ai-codeagent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ai-codeagent   # 自動起動を有効化
sudo systemctl start ai-codeagent    # 今すぐ起動
```

### 状態確認・操作

```bash
systemctl status ai-codeagent        # 状態確認
sudo systemctl restart ai-codeagent  # 再起動
sudo systemctl stop ai-codeagent     # 停止
journalctl -u ai-codeagent -f        # ログをリアルタイム表示
```

### WSL2 で systemd を有効にする方法

`/etc/wsl.conf` に以下を追加して WSL2 を再起動：

```ini
[boot]
systemd=true
```

```powershell
# PowerShell で WSL2 を再起動
wsl --shutdown
```

---

## Docker コンテナ管理

エージェントは Docker Compose でコンテナを管理できます。

### ルール
- 各サービスは `workspace/<サービス名>/docker-compose.yml` に配置
- エージェントへの指示例: `「Portainer を workspace に作って起動して」`

### Portainer（Docker 管理 Web UI）

```bash
# エージェントが自動作成・起動済みの場合
# http://localhost:9000 でアクセス（初回は管理者アカウントを作成）
```

### 不要なイメージ・ボリュームの削除

```bash
docker system prune -a    # 未使用イメージ・コンテナをすべて削除
docker volume prune       # 未使用ボリュームを削除
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
- [ ] (任意) `bubblewrap` をインストールした (`sudo apt install bubblewrap`)
- [ ] (任意) systemd サービスを登録して自動起動を設定した
- [ ] (任意) Docker をインストールして Portainer 等のコンテナ管理 UI を起動した
- [ ] (任意) Manim をインストールした (`sudo apt install libcairo2-dev libpango1.0-dev ffmpeg && pip install manim`)
- [ ] (任意) Ansible を使用する場合はインストールした
- [ ] (任意) Azure を Ansible で操作する場合は azcollection をインストールした

---

## Ansible を使用する場合

### Ansible のインストール

```bash
sudo apt install -y ansible
```

### 動作確認

```bash
ansible --version
```

---

## Ansible で Azure を操作する場合

Azure リソースグループ・VM・VNET 等を Ansible から操作するには `azure.azcollection` と依存パッケージが必要です。

### Azure コレクションのインストール

```bash
# azure.azcollection をインストール
ansible-galaxy collection install azure.azcollection --force

# Python 依存パッケージをインストール
pip3 install -r ~/.ansible/collections/ansible_collections/azure/azcollection/requirements.txt --break-system-packages
```

### 認証設定

環境変数でサービスプリンシパルまたはマネージド ID を指定します。

```bash
export AZURE_SUBSCRIPTION_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export AZURE_CLIENT_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export AZURE_SECRET="your-client-secret"
export AZURE_TENANT="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

または Playbook の `vars` に直接記載するか、`~/.azure/credentials` ファイルを使用します。

### Playbook 実行例

```bash
ansible-playbook -i localhost, your_playbook.yml
```

### エージェントからのインストール指示例

エージェントのチャットに以下のように指示すれば自動でインストールされます：

```
Ansibleをインストールして、Azure コレクション（azure.azcollection）と依存パッケージも入れて
```

