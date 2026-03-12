# AI Code Agent プロジェクト

Azure OpenAI (gpt-4.1-mini) を使った高機能コードエージェント。
Web チャット UI からコード生成・編集・実行・GitLab 連携ができる。

---

## 作業再開時の手順

```bash
cd ~/AI-Codeagent
source venv/bin/activate
uvicorn server:app --reload
# → http://localhost:8000 をブラウザで開く
```

※ `SEARXNG_ENABLED=true` の場合、サーバー起動時に SearXNG コンテナが自動起動する。
  手動で起動する場合: `docker compose -f docker-compose.searxng.yml up -d`

確認事項:
1. `.env` が存在するか (`cp .env.example .env` して値を設定)
2. `bubblewrap` がインストール済みか (`which bwrap`)
3. GitLab PAT が有効か (`.env` の `GITLAB_PAT`)
4. Docker が起動しているか (`docker info`)

---

## 重要なパス

| パス | 役割 |
|---|---|
| `~/AI-Codeagent/` | プロジェクトルート |
| `~/AI-Codeagent/workspace/` | エージェントの作業ディレクトリ |
| `~/AI-Codeagent/tools/` | ツール実装 |
| `~/AI-Codeagent/docs/design.md` | 設計ドキュメント |
| `~/AI-Codeagent/docs/setup.md` | セットアップ手順（別PC向け） |

---

## アーキテクチャ概要

```
server.py           ← FastAPI + SSE ストリーミング、TOOL_REGISTRY、SearXNG自動起動
config.py           ← .env 読み込み (Azure / GitLab / SearXNG / workspace設定)
prompts.py          ← システムプロンプト (今日の日付・GitLab ワークフロー含む)
tools/
  file_tools.py     ← read_file / write_file / list_files
  command_tools.py  ← run_command + _run_bash_sandboxed (bubblewrap)
  web_tools.py      ← web_search / web_fetch / web_research (SearXNG優先)
  code_tools.py     ← code_lint (ruff / eslint)
index.html          ← チャット UI (Catppuccin テーマ)
workspace/          ← エージェントの作業ディレクトリ (Git管理外)
docker-compose.searxng.yml  ← SearXNG コンテナ定義 (ポート8888)
searxng-settings/   ← SearXNG 設定 (JSON形式有効化)
```

---

## 実装済み機能 ✅

### セキュリティ
- [x] API キーを `.env` で管理（ハードコード排除）
- [x] `shell=False` + コマンドホワイトリスト（`run_command`）
- [x] パストラバーサル防止（`_resolve_safe_path` / `Path.resolve()`）
- [x] **bubblewrap サンドボックス**（`bash script.sh` 実行時）
  - FS 全体読み取り専用、workspace のみ書き込み可
  - ネットワーク完全遮断（`--unshare-net`）
  - Claude Code (Linux/WSL2) と同じ方式
- [x] SSRF 防止（`web_fetch` でプライベート IP をブロック）

### ツール
- [x] `read_file` / `write_file` / `list_files`（パストラバーサル対策済み）
- [x] `run_command`（ホワイトリスト + work_dir をworkspace相対で解決）
- [x] `bash script.sh`（bubblewrap サンドボックス経由）
- [x] `web_search`（SearXNG優先 → DuckDuckGo API → Wikipedia API フォールバック）
- [x] `web_fetch`（BeautifulSoup テキスト抽出、SSRF 対策）
- [x] `web_research`（検索→複数ページ自動取得→まとめて返す高レベルツール）
- [x] `code_lint`（Python: ruff、JS/TS: eslint）

### GitLab 連携
- [x] `.env` に `GITLAB_PAT` / `GITLAB_USER` を設定
- [x] システムプロンプトに GitLab ワークフローを明記
  - `curl` で API 呼び出し → プロジェクト作成
  - `git init / add / commit / push`（`work_dir` をサブディレクトリ指定）
- [x] **実証済み**：TEST1 / TEST2 プロジェクトを自律作成・push 完了

### UI (index.html)
- [x] Catppuccin テーマのチャット画面
- [x] **localStorage 履歴永続化**（ページリロードで復元）
- [x] **ターン折りたたみ**（古いターンを `<details>` に格納、MAX=5）
- [x] **API 履歴切り捨て**（クライアント・サーバー両側、最新20件）
- [x] 「履歴クリア」ボタン + ターンカウント表示
- [x] **ツール実行の説明表示**（紫イタリック体）
  - `run_command`: AI が書いた `description` を優先、なければ自動推定
  - `write_file` / `web_search` 等: 引数から自動生成
- [x] **git diff カラー表示**（`run_command` の stdout を diff 判定し色付けレンダリング）
- [x] **GitLab プロジェクトパネル**（🦊ボタン → スライドインパネル）
  - `GET /gitlab/projects` で参加プロジェクト一覧を取得（最終アクティビティ順、最大50件）
  - インクリメンタル絞り込み検索
  - クリックで「workspace にクローンして」メッセージを入力欄に自動セット
  - GITLAB_PAT 未設定時はボタン非表示

### バグ修正
- [x] `list_files("workspace")` → `_normalize_path()` で workspace二重問題を解決
- [x] `work_dir` の相対パス解決: Python CWD 基準 → ALLOWED_WORK_DIR 基準に修正
- [x] `git init` をサブディレクトリで実行するようシステムプロンプトを整備
- [x] 社内プロキシ対応（`no_proxy` / `NO_PROXY` を `.env` に追加、`load_dotenv(override=True)` で確実に適用）
- [x] **SearXNG 自動起動**（`SEARXNG_ENABLED=true` 時、uvicorn 起動と同時に `docker compose up -d`）
- [x] **今日の日付をシステムプロンプトに動的付与**（時事情報の検索クエリに正確な日付を使用）

---

## 残タスク・改善候補

### Claude Code との差を埋める改善（優先度順）
- [ ] **`edit_file` ツールの追加**（★★★）: 現在は全体上書きのみ。特定文字列の置換ができるとトークン節約・ミス減少
- [ ] **`grep` / `glob` ツールの追加**（★★★）: コードベース横断検索。「この関数がどこで使われているか」を調べるのに必須
- [ ] **ストリーミング回答**（★★）: 現在は最終回答が一括表示。`stream=True` + `delta.content` を逐次 yield でリアルタイム表示
- [ ] **tool メッセージの履歴保持**（★★）: 現在は `user`/`assistant` のみ保存。`tool` ロールを含めないとマルチターンで前回のツール結果が消える
- [ ] **モデルを gpt-4.1 に変更**（★）: `gpt-4.1-mini` → `gpt-4.1` でコード生成精度向上（コスト増に注意）
  - ※ `gpt-5.1-codex-mini` は Responses API 専用のため Chat Completions ベースの現構成では使用不可

### 機能追加
- [ ] ツール結果のエラー時に UI 上でわかりやすく表示（赤文字など）※低優先度

### 品質・テスト
- [ ] 各ツールの単体テスト（pytest）を書く
- [ ] bubblewrap サンドボックスの脱出テスト
- [ ] 長いプロンプトでのトークン上限テスト
- [ ] 別 PC（社内プロキシあり）での動作確認

### ドキュメント
- [ ] `docs/setup.md` の移行チェックリストに bubblewrap を追記
- [ ] `docs/design.md` を現在の実装に合わせて更新

---

## テスト項目チェックリスト

### 基本動作
- [ ] `uvicorn server:app --reload` で正常起動
- [ ] `http://localhost:8000` でチャット UI 表示
- [ ] メッセージ送信 → AI 応答が返ってくる

### ツール動作確認
- [ ] `list_files` → workspace のファイル一覧が返る
- [ ] `write_file` → workspace にファイルが作成される
- [ ] `read_file` → ファイル内容が返る
- [ ] `run_command("python3 --version")` → バージョンが返る
- [ ] `bash script.sh` → bubblewrap で実行される
- [ ] `web_search "FastAPI"` → 検索結果が返る
- [ ] `web_fetch "https://httpbin.org/get"` → コンテンツが返る
- [ ] `code_lint` → ruff が動作する

### セキュリティ確認
- [ ] `read_file("../../etc/passwd")` → エラーで拒否される
- [ ] `run_command("rm -rf /")` → ホワイトリスト拒否
- [ ] `bash -c "rm -rf /"` → 形式エラーで拒否
- [ ] bash スクリプト内の `curl` → bubblewrap でネットワーク遮断

### GitLab 連携
- [ ] `curl` で GitLab プロジェクト作成 → 成功
- [ ] `git init` + `git push` → GitLab に反映
- [ ] `.env` の PAT が切れた場合のエラーメッセージ確認

### UI
- [ ] ページリロードで履歴が復元される
- [ ] 5ターン超えで古いターンが折りたたまれる
- [ ] 「履歴クリア」で localStorage が消える
- [ ] ツール実行ブロックに説明が表示される

---

## .env に必要な設定

```env
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://xxx.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini
AZURE_OPENAI_API_VERSION=2025-01-01-preview
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
```

---

## GitLab リポジトリ

- **このプロジェクト**: https://gitlab.com/yuichi.matsuo/AI-Codeagent
- **ブランチ**: main
- **最終更新**: 2026-03-12（GitLabパネル・diffカラー表示・SearXNG統合）
