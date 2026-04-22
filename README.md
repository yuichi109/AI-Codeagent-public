# AI Code Agent

Azure OpenAI を使ったコードエージェント。
Web チャット UI からコードの生成・編集・レビュー・実行・GitLab 連携ができる。

## ブランチ構成

| ブランチ | 対象環境 | 特徴 |
|---|---|---|
| `main` | Linux / WSL2 | systemd サービス・bubblewrap サンドボックス・SearXNG 対応 |
| `for_windows` | Windows ネイティブ | WSL・Docker 不要。`setup.bat` で起動。サンドボックスなし |

### Linux / WSL2 で使う場合

```bash
git clone https://gitlab.com/yuichi.matsuo/AI-Codeagent
cd AI-Codeagent
./setup.sh install
```

### Windows ネイティブで使う場合（WSL 不要）

```bash
git clone -b for_windows https://gitlab.com/yuichi.matsuo/AI-Codeagent
cd AI-Codeagent
```

`setup.bat` をダブルクリックして起動。ブラウザで http://localhost:8001 を開く。

> **注意**: Windows 版では以下の機能が利用できません。
> - bash スクリプトのサンドボックス実行（bubblewrap 非対応）
> - SearXNG（Docker 依存）→ ddgs / Tavily で代替

---

## 機能

| ツール | 説明 |
|---|---|
| 📄 `read_file` | ファイルの読み取り |
| ✏️ `write_file` | ファイルへの書き込み |
| 📁 `list_files` | ディレクトリ一覧 |
| ⚡ `run_command` | コマンド実行 |
| 🔍 `web_search` | Web 検索（Tavily → ddgs → SearXNG）|
| 🌐 `web_fetch` | URL のテキスト取得 |
| 🔬 `code_lint` | 静的解析（Python: ruff / JS・TS: eslint）|

詳細なセットアップ手順 → **[docs/setup.md](docs/setup.md)**
