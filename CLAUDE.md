# AI Code Agent プロジェクト

Azure OpenAI を使った高機能コードエージェント。
Web チャット UI からコード生成・編集・実行・GitLab 連携ができる。

- **GitLab**: https://gitlab.com/yuichi.matsuo/AI-Codeagent（main ブランチ）
- **詳細ドキュメント**: [実装済み機能](docs/changelog.md) / [ロードマップ](docs/roadmap.md) / [テスト](docs/test-checklist.md)

---

## 作業再開時の手順

サーバーは **systemd サービス** として動作している。手動で uvicorn を起動してはいけない。

```bash
# サーバー再起動（コード変更後は必ずこれを使う）
wsl -d Ubuntu -- sudo systemctl restart ai-codeagent

# 状態確認
wsl -d Ubuntu -- systemctl status ai-codeagent

# ログ確認
wsl -d Ubuntu -- journalctl -u ai-codeagent -n 50
```

※ Claude Code のターミナルは Windows 環境のため `wsl -d Ubuntu --` プレフィックスが必要。
※ `SEARXNG_ENABLED=true` の場合、サーバー起動時に SearXNG コンテナが自動起動する。
  手動で起動する場合: `docker compose -f docker-compose.searxng.yml up -d`

確認事項:
1. `.env` が存在するか (`cp .env.example .env` して値を設定)
2. `bubblewrap` がインストール済みか (`which bwrap`)
3. GitLab PAT が有効か (`.env` の `GITLAB_PAT`)
4. Docker が起動しているか (`docker info`)

### 初回インストール・設定変更は `setup.sh` を使う

```bash
./setup.sh install   # 初回セットアップ
sudo ./setup.sh proxy  # プロキシ切り替え（社内 ⇔ 社外）
./setup.sh service   # サービス操作
```

---

## 重要なパス

| パス | 役割 |
|---|---|
| `~/AI-Codeagent/` | プロジェクトルート |
| `~/AI-Codeagent/workspace/` | エージェントの作業ディレクトリ |
| `~/AI-Codeagent/tools/` | ツール実装 |
| `~/AI-Codeagent/skills/` | スキルファイル（`スキル名/SKILL.md`） |
| `~/AI-Codeagent/docs/` | ドキュメント（changelog / roadmap / test-checklist） |

---

## アーキテクチャ概要

```
server.py           ← FastAPI + SSE ストリーミング、TOOL_REGISTRY、SearXNG自動起動
config.py           ← .env 読み込み (Azure / GitLab / SearXNG / workspace設定)
prompts.py          ← 自律エージェント用システムプロンプト（行動原則・完了定義）
tools/
  file_tools.py     ← read_file / write_file / edit_file / list_files / glob_files / grep
  command_tools.py  ← run_command + _run_bash_sandboxed (bubblewrap)
  web_tools.py      ← web_search / web_fetch / web_research (Tavily優先 → ddgs → SearXNG)
  code_tools.py     ← code_lint (ruff / eslint)
  windows_tools.py  ← run_powershell (WSL2 → Windows操作)
  office_tools.py   ← read/write/edit_docx/xlsx/pptx
  pdf_tools.py      ← read_pdf (pdfplumber)
index.html          ← チャット UI (Catppuccin テーマ、ストリーミング・tool履歴対応)
skills/             ← スキルファイル（再起動不要で即反映）
workspace/          ← エージェントの作業ディレクトリ (Git管理外)
```

---

## 重要な注意事項（壊してはいけない設計）

- **ローカルモデルへの tools 渡しを無効化している**: Qwen3等のJinjaテンプレートが壊れたtool_callsを生成して暴走するため。**元に戻してはいけない**。
- **`run_command` は `shell=False`**: `cd && コマンド` は不可。`work_dir` パラメータを使う。
- **`_recent_head_unsafe()` の境界ロジック**: `SUMMARY_KEEP_RECENT` を変更する際は孤立メッセージ問題が再発しないよう注意。
- **`/get-proj` スキルで `work_dir` 使用禁止**: `workspace/workspace/` の入れ子になるバグが発生する。
- **git push で 407 が出る場合**: `git -c http.proxy='' push origin main` で回避。

---

## スキル一覧

| スキル | 説明 |
|---|---|
| `/commit` | ステージ済み変更にコミットメッセージを付けてコミット（push は明示指示時のみ） |
| `/get-proj` | まとめリポジトリから特定ディレクトリを取り出して独立化 |
| `/save` | コードブロック内容を変更なしで指定ファイルに保存 |
| `/ansible` | プレイブック一覧チェックボックス選択 → ansible-playbook 実行 |
| `/boost` | 一時ファイル削除・ごみ箱クリア・DNS キャッシュクリア・メモリ解放 |

スキルファイル: `skills/スキル名/SKILL.md`（追加・編集後は再起動不要で即反映）

---

## .env に必要な設定

```env
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://xxx.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini
AZURE_OPENAI_DEPLOYMENTS=gpt-5-mini,gpt-4.1-mini,gpt-4.1
AZURE_OPENAI_API_VERSION=2025-01-01-preview
# Azure AI Foundry（インスタンス1）
FOUNDRY_NAME=Azure AI Foundry
FOUNDRY_ENDPOINT=https://xxx.cognitiveservices.azure.com/
FOUNDRY_API_KEY=...
FOUNDRY_MODEL=gpt-4.1-mini
FOUNDRY_MODELS=gpt-5-mini,gpt-4.1-mini
FOUNDRY_API_VERSION=2024-12-01-preview
# Azure AI Foundry（インスタンス2以降）— FOUNDRY_2_*, FOUNDRY_3_* と続けて定義可
# エージェント名（自己紹介時に使う名前）
AGENT_NAME=SPEC-AI
ALLOWED_WORK_DIR=./workspace
COMMAND_TIMEOUT_SECONDS=30
GITLAB_USER=yuichi.matsuo
GITLAB_PAT=glpat-...
# プロキシバイパス（社内環境）
no_proxy=*.azure.com,*.openai.azure.com,gitlab.com,...
NO_PROXY=*.azure.com,*.openai.azure.com,gitlab.com,...
# SearXNG
SEARXNG_BASE_URL=http://localhost:8888
SEARXNG_ENABLED=true
# Tavily Search API (省略可・無料1000クエリ/月・カード不要)
TAVILY_API_KEY=tvly-dev-...
```
