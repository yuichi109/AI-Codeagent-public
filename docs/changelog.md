# AI Code Agent — 実装済み機能・変更履歴

> このファイルは AGENT.md から分離した実装済み機能の詳細記録です。
> 作業中の参照用。新規実装時はここに追記してください。

---

## 2026-05-13

### Monaco エディタ インラインチャット機能を実装（#42）（`server.py` / `index.html`）

GitLab Issue #42 の実装。エディタ横でAIと往復しながらコードを書ける「隣のプログラマー仲間」機能。

#### バックエンド（`server.py`）

- `InlineChatRequest` モデル追加（`messages / current_code / language / filename / model / is_selection`）
- `POST /editor/chat` エンドポイント追加
  - ツールなし・シンプルな ChatCompletion（ファイル編集には `is_selection` フラグで文脈を使い分け）
  - 選択範囲ありの場合: 選択範囲のみをシステムプロンプトに注入し「選択範囲全体を変更して返して」と指示
  - 選択範囲なしの場合: ファイル全体（最大6000文字）＋行数を明示し「必ず全行含むファイル全体を返せ」と強制
  - モデル指定があればそれを使用・なければプロバイダーデフォルトを使用
  - `max_completion_tokens: 2000`

#### フロントエンド（`index.html`）

**UIモード:**
- **サイドモード**: エディタ右に340px固定ペインとして表示
- **フロートモード**: 画面内を自由に移動できるフローティングウィンドウ
- モード切り替えボタン（⇄）で即時切り替え・localStorage に保存
- Ctrl+Shift+K（Monaco keybinding）でトグル

**リサイズ・ドラッグ:**
- サイドモード: ペイン左端5pxハンドル（col-resize）でドラッグリサイズ（最小200px・最大70vw）
- フロートモード: ヘッダーをドラッグして移動・右下コーナーの3点グリップで se-resize
- localStorage にサイズ・位置を保存

**送信・ショートカット:**
- Ctrl+Enter: メッセージ送信
- Ctrl+Shift+Enter: 最後の「✓ 適用」ボタンを即クリック

**文脈の渡し方:**
- コードを選択中 → 選択範囲のみを文脈として送信（`is_selection: true`）
- 選択なし → ファイル全体を文脈として送信

**コード提案の適用:**
- AIの返答にコードブロックがあれば「✓ 適用」ボタンを表示
- 適用は選択範囲があればその範囲のみ・なければファイル全体を置換
- **安全チェック**: 返ってきたコードが元ファイルの50%未満の行数なら confirm ダイアログで警告

**トークン効率化:**
- 会話履歴（`messages[]`）はコードブロックを除去してから送信（`_icStripCodeBlocks()`）
- 毎ターン最新のエディタ内容を system prompt に再注入するため history にコードを残す必要がない

**その他:**
- `💬 チャット` ボタンをエディタツールバーに追加
- ペイン内にモデル選択プルダウン（インラインチャット専用・チャットとは独立）
- ゴミ箱ボタンで会話履歴クリア

**未実装（次回セッション候補）:**
- タスクタイプ自動モデル切り替え（「追加/修正/書いて」→上位モデル、質問・説明→nanoモデル、キーワード検出で自動判定）

- `main` / `for_windows` 両ブランチに反映済み

---

## 2026-05-12

### エディタに Markdown プレビュー機能を追加（`index.html`）

- `.md` ファイルを開いたときのみツールバーに「👁 プレビュー」ボタンを表示
- クリックで右半分にリアルタイムプレビューを分割表示（Monaco エディタ + プレビューペイン）
- 編集内容を `onDidChangeModelContent` でリアルタイムに反映（既存の `renderMarkdown()` を流用）
- タブ切り替え時に自動でボタン表示/非表示を制御。`.md` 以外のファイルに切り替えるとプレビューも自動クローズ
- `main` / `for_windows` 両ブランチに反映済み

---

## 2026-05-10

### Ansible Azure リファレンス追加（`docs/ansible-azure-reference.md`）

- `spec2325705/bk-yuichi.matsuo` にあるAzureスナップショット関連Ansibleコードの場所を記録
- Windows単体VM用に改変する際のフロー・対応表を整理
- GitLab APIでの検索方法も記載（グローバル検索無効のためプロジェクトID指定が必要）

### マルチエージェント設計書（`docs/multi-agent-dispatch-design.md`）大幅加筆

コード変更なし・設計ドキュメントのみ。

- **インフラ担当AIの段階設計**を追加
  - Windows版はコード生成・ドキュメントまで（インフラ担当なし）
  - WSL版 Phase 1：インフラ Lite（Docker・Windows Sandbox）
  - WSL版 Phase 2：インフラ Full（Azure VM・vSphere・既存ホスト）
- **フルオートパイプラインのビジョン**を追記（設計→実装→環境構築→テストが全自動）
- **接続先プロファイル設計**：Azure VM / vSphere / 既存ホスト（IP＋管理ID登録）の使い分け
- **vSphere方針**：既存Ansibleプレイブックを活用・ただし将来使えなくなる可能性があるため優先度低
- **Windows Sandboxの位置づけ**：Windows 11限定・VM環境では厳しい・メインに据えない
- **Windowsテスト環境の選択フロー**を整理（Sandbox非対応時の代替案内）
- **「使えない」で終わらないUX方針**：非対応検出時は次の選択肢を順番に案内する
- **社内向け**：構成済みUbuntuイメージ（Ansible・Docker入り）で即フルスタック
- **WSL版の動作要件**：Ubuntu（20.04/22.04/24.04）限定を明記

---

## 2026-05-08

### Monaco エディタ大幅改善（コミット: e276f29〜1c8e976）

- 空ディレクトリをファイルツリーに表示（`_isDir` フラグ付与）
- エディタを開くたびにタブ・状態をリセット（前回ファイルが残らない）
- ディレクトリ選択 → 新規ファイル作成時にパスプレフィックスを自動入力
- ファイル保存後にファイルツリーを自動更新
- 同名ファイル作成時に上書き確認ダイアログを表示
- **マルチタブ対応**: `renderTabs()` / `switchTab()` / `closeTab()`、Monaco の `createModel()` / `setModel()` でタブごとにモデルを管理
- **ドラッグ＆ドロップ移動**: ファイルツリー内で D&D によるファイル/フォルダ移動。`window.ftIsDragging` フラグでチャット添付ハンドラとの衝突を回避
- `/workspace/move` エンドポイント追加（`server.py`、`shutil.move()` + パス検証）
- **別タブで表示**: エディタボタンを `window.open('?editor=1', '_blank')` に変更
- **未保存警告**: `beforeunload` イベントで未保存タブがあればブラウザ標準の確認ダイアログ

### README 全面改訂・MIT ライセンス追加（コミット: b07113b, b40ad58, b63962a）

- `README.md`: モデル構成・Windows版・全ツール・Monaco エディタ・RAG・セッション管理等を網羅した内容に全面書き直し
- `LICENSE`: MIT ライセンスファイル新規追加（Copyright 2026 yuichi.matsuo）
- `README.md` に shields.io バッジ追加（License / Python / FastAPI / Azure OpenAI / Platform）

### エディタ補完モデル選択（チャットとは独立）（コミット: 5fd7cb4, a203c60）

- エディタツールバーに補完用モデルのプルダウンを追加（「🤖 AI補完: ON」の隣）
- チャット側のモデル設定とは完全独立、`/providers/deployments` から選択肢を自動取得
- 選択は localStorage に保存。`server.py` の `EditorCompleteRequest` に `model` フィールドを追加

### GitLab イシュー

- **#42**: Monaco エディタ内インラインチャット機能（Cursor の Inline Chat 相当）登録

---

## 2026-05-07

### セッション履歴アーカイブ・保護機能（コミット: 954efdf, 1750324）

- セッション保存時に自動アーカイブ（sessions/ 20件超 → archive/、archive/ 100件超 → 削除）
- ★/☆ 保護ボタン追加（保護フラグ付きセッションはアーカイブ対象外）
- 📦 アーカイブボタンで履歴↔アーカイブ画面を切り替え
- アーカイブセッションは読み取り専用・「↩ この会話を再開」ボタンで復元

### RAG 知見データベースを main にマージ（コミット: a33306c）

- `tools/rag_tools.py`: rag_save / rag_search / rag_update_status / rag_list
- `setup.html`: RAG 有効化トグル追加
- `.rag_db` を `.gitignore` に追加（社内情報が GitLab に同期されない）
- rag_list に通し番号・短縮 ID を追加

### RAG バグ修正（コミット: fcb0e19）

- `prompts.py`: 明示指示なしに `rag_update_status` を呼ばないルールを追加
- `tools/rag_tools.py`: 関連度 0.3 未満のヒットを除外

### ALLOWED_WORK_DIRS 複数ディレクトリ対応（コミット: 0e8a17a）

- `config.py`: `_normalize_to_wsl_path()` 追加（Windows/UNC パスを WSL パスに自動変換）
- `tools/file_tools.py` / `tools/command_tools.py`: 複数許可ディレクトリに対応

### ワークスペーススコープ固定機能（コミット: cb14829, b7f7b03）

- ヘッダーにスコープバー常時表示、フォルダ選択モーダル
- スコープ設定時にシステムプロンプトへ操作制限を注入
- localStorage に保存（/clear・リロードでも維持）

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

### Claude Code CLI版のセットアップ（環境整備）

- Windows PowerShell に `npm install -g @anthropic-ai/claude-code` でインストール
- WSL からも `/mnt/c/...` マウント経由で参照可能（追加インストール不要・既定の動作）
- Google アカウント（claude.ai Pro）でログイン済み・追加課金なし
- Windows Terminal（`winget install Microsoft.WindowsTerminal`）もインストール済み
- このプロジェクトの作業はデスクトップ版継続推奨（memory自動読み込み・GUIパネルのため）

### Ubuntu apt 障害メモ（2026-04-30〜）

- `archive.ubuntu.com` が DDoS 攻撃の影響で断続的に不安定
- 回避策：`sudo sed -i 's|http://archive.ubuntu.com|https://ftp.udx.icscoe.jp/Linux/ubuntu|g' /etc/apt/sources.list`
- 復旧後は `sudo sed -i 's|https://ftp.udx.icscoe.jp/Linux/ubuntu|http://archive.ubuntu.com|g' /etc/apt/sources.list` で元に戻す

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
| 2026-05-08 | Monaco エディタ大幅改善（マルチタブ・D&D・別タブ・未保存警告）、README全面改訂・MITライセンス、エディタ補完モデル独立選択 |
| 2026-05-07 | セッション履歴アーカイブ・保護、RAGをmainにマージ、ALLOWED_WORK_DIRS複数対応、スコープ固定機能 |
| 2026-05-05 | Responses APIサブエージェント対応 |
| 2026-05-02 | write_pdf / Officeファイルツール追加（main移植） |
| 2026-05-01 | AGENT.md / MEMORY.md設計・/memoryスキル、セッション履歴検索#41登録 |
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
