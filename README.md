# AI Code Agent

Azure OpenAI (gpt-5-mini) を使ったコードエージェント。
Web チャット UI からコードの生成・編集・レビュー・実行・GitLab 連携ができる。

## 機能

| ツール | 説明 |
|---|---|
| 📄 `read_file` | ファイルの読み取り |
| ✏️ `write_file` | ファイルへの書き込み (上書き/追記) |
| 📁 `list_files` | ディレクトリ一覧 (glob パターン対応) |
| ⚡ `run_command` | コマンド実行 (ホワイトリスト制限あり) |
| 🔍 `web_search` | Web 検索 (SearXNG → DuckDuckGo → Wikipedia フォールバック) |
| 🌐 `web_fetch` | URL のテキスト取得 |
| 🔬 `code_lint` | 静的解析 (Python: ruff / JS・TS: eslint) |

## セットアップ

→ **[docs/setup.md](docs/setup.md)** を参照

## 起動

```bash
cd ~/AI-Codeagent
source venv/bin/activate
uvicorn server:app --reload
# ブラウザで http://localhost:8000 を開く
```

> `SEARXNG_ENABLED=true` の場合、SearXNG コンテナが自動起動します。

## 停止

### uvicorn の停止

ターミナルで **Ctrl+C**、またはバックグラウンド起動の場合：

```bash
pkill -f "uvicorn server:app"
# または
fuser -k 8000/tcp
```

### SearXNG コンテナの停止

uvicorn を止めてもコンテナは動き続けます。明示的に止める場合：

```bash
cd ~/AI-Codeagent
docker compose -f docker-compose.searxng.yml down
```
