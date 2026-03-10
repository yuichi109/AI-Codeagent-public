# AI Code Agent

Azure OpenAI (gpt-4.1-mini) を使ったコードエージェント。
Web チャット UI からコードの生成・編集・レビュー・実行ができる。

## 機能

| ツール | 説明 |
|---|---|
| 📄 `read_file` | ファイルの読み取り |
| ✏️ `write_file` | ファイルへの書き込み (上書き/追記) |
| 📁 `list_files` | ディレクトリ一覧 (glob パターン対応) |
| ⚡ `run_command` | コマンド実行 (ホワイトリスト制限あり) |
| 🔍 `web_search` | Web 検索 (DuckDuckGo → Wikipedia フォールバック) |
| 🌐 `web_fetch` | URL のテキスト取得 |
| 🔬 `code_lint` | 静的解析 (Python: ruff / JS・TS: eslint) |

## セットアップ

→ **[docs/setup.md](docs/setup.md)** を参照

## 起動

```bash
source venv/bin/activate
uvicorn server:app --reload
# ブラウザで http://localhost:8000 を開く
```
