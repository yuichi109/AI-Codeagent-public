# AI Code Agent — 実装済み機能・変更履歴

> このファイルは AGENT.md から分離した実装済み機能の詳細記録です。
> 作業中の参照用。新規実装時はここに追記してください。

---

## 2026-05-05

### Responses API サブエージェント対応（コミット: c4670f7）

- `tools/responses_tools.py` 新規作成
  - `call_responses_api(model, input, tools, instructions, previous_response_id)` ツール追加
  - メインエージェントがサブタスクを別モデルに委譲するマルチエージェント構成を実現
- `setup.html`: Responses API 有効/無効トグルを設定画面に追加
- `server.py`: `call_responses_api` を TOOL_REGISTRY・TOOLS に登録
- `config.py`: Responses API 関連の設定値を追加
- `prompts.py`: Responses API ツールの使い方をシステムプロンプトに追記

---

## 2026-04-30

### WSL版: プロジェクト指示ファイルを CLAUDE.md → AGENT.md に改名
- `prompts.py` の読み込み対象ファイル名を `CLAUDE.md` から `AGENT.md` に変更
- Claude Code の `CLAUDE.md` と名前が衝突して紛らわしかったため
- `workspace/AGENT.md`（全体共通）・`workspace/<プロジェクト>/AGENT.md`（プロジェクト固有）の両方を読み込む動作は変わらず

### Windows版（for_windowsブランチ）: git clone の認証・パス問題を修正
- **認証エラー修正**: `git -c credential.helper=""` を追加し Windows Credential Manager をバイパス。URL に埋め込んだ PAT が直接使われるようになった
- **パス修正**: クローン先を `~/AI-Codeagent/workspace/リポジトリ名` から `リポジトリ名`（相対パス）に変更。Git for Windows が `~` をホームディレクトリに展開するため workspace 外にクローンされていた問題を解消

### WSL版・Windows版: シェルパネルのコマンド実行後に入力欄がクリアされないリグレッション修正
- `index.html` の `shellExec()` の `finally` ブロックに `input.value = ''` と `input.style.height = 'auto'` を追加
- 過去に修正済みだったが再発していた（`aad98ba` のリグレッション）

### 未対応（次回対応予定）
- **Windows版シェルパネルの文字化け**: PowerShell 出力が CP932 でエンコードされているため `ls` 等で日本語が文字化けする。WSL版で過去に同様の修正済み（CP932→UTF-8変換）

---

## 実装済み機能

### セキュリティ
- API キーを `.env` で管理（ハードコード排除）
- `shell=False` + コマンドブラックリスト（`run_command`）: mkfs/fdisk/dd/shutdown等のみ拒否
- パストラバーサル防止（`_resolve_safe_path` / `Path.resolve()`）
- **bubblewrap サンドボックス**（`bash script.sh` 実行時）
  - FS 全体読み取り専用、workspace のみ書き込み可
  - ネットワーク完全遮断（`--unshare-net`）
- SSRF 防止（`web_fetch` でプライベート IP をブロック）

### ツール
- `read_file` / `write_file` / `list_files`（パストラバーサル対策済み）
- `edit_file`（old_str → new_str 部分置換、件数不一致エラー検出）
- `glob_files`（再帰 glob パターン検索）
- `grep`（正規表現・行番号付き横断検索、case_sensitive / max_results オプション）
- `run_command`（ブラックリスト方式 + work_dir をworkspace相対で解決、出力を先頭+末尾各4000文字に切り捨て）
- `run_background` / `check_background` / `kill_background`（バックグラウンドプロセス管理 #20）
- `bash script.sh`（bubblewrap サンドボックス経由）
- `web_search`（Tavily優先 → ddgs → SearXNG → DuckDuckGo API → Wikipedia フォールバック）
- `web_fetch`（BeautifulSoup テキスト抽出、SSRF 対策）
- `web_research`（検索→複数ページ自動取得→まとめて返す高レベルツール）
- `code_lint`（Python: ruff、JS/TS: eslint）
- `render_manim`（Manim コードをレンダリングして最終フレーム PNG を返す）
- `run_powershell`（WSL2 から Windows PowerShell を操作）
- `read_pdf`（pdfplumber、ページ指定・テーブルMarkdown変換対応）
- Officeファイルツール（`tools/office_tools.py`）: read_docx / write_docx / edit_docx / read_xlsx / write_xlsx / edit_xlsx / read_pptx / write_pptx / edit_pptx

### GitLab 連携
- `.env` に `GITLAB_PAT` / `GITLAB_USER` を設定
- GitLab プロジェクトパネル（🦊ボタン → スライドインパネル）
- GitLab イシューパネル（`GET /gitlab/issues`、state パラメータ対応）
- `curl` で API 呼び出し → プロジェクト作成、`git init / add / commit / push` 対応

### UI (index.html)
- Catppuccin テーマのチャット画面
- localStorage 履歴永続化 + ターン折りたたみ（MAX=5）
- ストリーミング回答（`answer_chunk` SSE イベントで delta を逐次表示）
- **run_command リアルタイムストリーミング**（`tool_stdout` SSE イベントで行ごとに逐次表示、tool-group を自動展開）
- ツール結果折りたたみ表示（`<details>/<summary>` 形式）
- ツールグループ折りたたみ UI（「N個のツールを実行 · run_command ×3」形式）
- LLMプロバイダー切り替えパネル（⚙️ボタン）
- 生成中断ボタン（■ 停止、AbortController）
- 画像添付機能（マルチモーダル対応、📎ボタン or Ctrl+V）
- textarea 入力欄（Shift+Enter 改行・Enter 送信・高さ自動リサイズ）
- git diff カラー表示
- シンタックスハイライト（highlight.js, atom-one-dark テーマ）
- 過去セッション履歴パネル（サーバー側 JSON ファイルに保存）
- ローリングサマリー（`SUMMARY_TRIGGER=25` 超で自動圧縮）
- `/no_think` オプション（⚙️パネル）
- 承認バイパストグル（🔒/🔓）
- URLオートコンプリート（LLM設定パネル、最大5件）
- Monaco Editor テキストエディタ（📝ボタン、AI補完・ファイルツリー・ゴーストテキスト補完）
- シェル実行パネル（🖥ボタン、.shファイル一覧・直接コマンド入力・ディレクトリナビ）
- ドラッグアンドドロップ・バイナリファイルアップロード（PDF/Office）
- `/setup` セットアップウィザード（ブラウザから `.env` を GUI 編集）
- 複数 Azure AI Foundry インスタンス対応（`FOUNDRY_N_*` 環境変数）
- **for_windows: タスクトレイ常駐**（`start.bat` → `tray.py`、🤖アイコン、右クリックで再起動/停止、初回セットアップ自動実行）

### スキルシステム
- `skills/スキル名/SKILL.md` に定義（再起動不要で即反映）
- `GET /skills` エンドポイントでスキル一覧をJSON取得可能
- 入力欄で `/` を打つとスキル候補をポップアップ表示（↑↓選択・Tab/Enter補完）
- 登録済みスキル: `/commit` `/get-proj` `/save` `/ansible` `/boost` `/help`

---

## 重要なバグ修正・設計メモ

### ローリングサマリーの境界ロジック（要注意）
`recent_part` 先頭が `role:tool` / `assistant+tool_calls` になると孤立メッセージ問題が再発する。
`_recent_head_unsafe()` で境界を安全な位置まで自動スライドして対策済み。
将来 `SUMMARY_KEEP_RECENT` を変更する際はこの境界ロジックを壊さないよう注意。

### LLMストリーミング非同期化（2026-04-21）
`for chunk in stream:` が asyncio イベントループを完全にブロックしていた問題を修正。
`AsyncAzureOpenAI` / `AsyncOpenAI` + `async for chunk in stream:` に変更。

### web_fetch タイムアウト（2026-04-21）
`stream=True` + `timeout=15` の組み合わせでは HTTP 本文読み込み中タイムアウトが無効化される。
→ `stream=True` を削除、`timeout=(10, 20)` に変更（接続10秒/読み込み20秒）。

### プロキシ対応
- `no_proxy` / `NO_PROXY` を `.env` に追加、`load_dotenv(override=True)` で適用
- `httpx`: `trust_env=False` で社内プロキシを迂回（AzureOpenAI・AsyncAzureOpenAI 両方）
- `git push` で 407 が出る場合: `git -c http.proxy='' push origin main` で回避

### /get-proj スキル（実装上の注意）
- `run_command` に `work_dir` を指定すると `workspace/workspace/` の入れ子になるバグ → **`work_dir` 使用禁止**
- `/tmp/` は bubblewrap サンドボックス外で使えない → **workspace 内のみで作業**
- `git clone --depth 1` + `cp` + `rm -rf` の3ステップで統一

---

## 変更履歴（主要マイルストーン）

| 日付 | 内容 |
|---|---|
| 2026-04-28 | run_command 出力切り捨て改善（先頭+末尾）、リアルタイムストリーミング、バックグラウンドプロセス管理、タスクトレイ常駐（for_windows）、/setup UTF-8修正 |
| 2026-04-24 | read_pdf ツール追加、/compact バグ修正、SUMMARY_TRIGGER 25に変更、setup.bat CRLF修正 |
| 2026-04-23 | setup.bat winget 自動インストール、/boost スキル、docs/setup.md Windows版手順追加 |
| 2026-04-22 | for_windows ブランチ作成、Officeファイルツール追加、ドラッグアンドドロップアップロード |
| 2026-04-21 | LLMストリーミング同期ブロッキング修正、web_fetchタイムアウトバグ修正、ツールタイムアウト追加 |
| 2026-04-03 | 検索エンジン刷新（Tavily/ddgs追加）、検索ハルシネーション抑制 |
| 2026-04-02 | run_powershell ツール追加（WSL2→Windows操作） |
| 2026-03-30 | Monaco Editor追加、シェル実行パネル追加、Ansibleウィジェット |
| 2026-03-27 | setup.sh による別PCデプロイ対応 |
| 2026-03-26 | 複数Foundryインスタンス対応、AGENT_NAME設定、/setupウィザード |
| 2026-03-25 | スキルシステム基盤、/compact・/オートコンプリートUI |
| 2026-03-24 | 過去セッション履歴パネル、シンタックスハイライト、GitLabイシュー |
| 2026-03-21 | ツール失敗時の自己修正強化 |
| 2026-03-20 | ローカルLLM ツール ON/OFFスイッチ |
| 2026-03-19 | render_manim ツール、ツールグループ折りたたみUI |
| 2026-03-18 | ブラックリスト方式、承認バイパスボタン |
| 2026-03-16 | LLMプロバイダー切り替えパネル、生成中断ボタン、画像添付 |
| 2026-03-13 | edit_file / grep / glob ツール、ストリーミング回答 |
