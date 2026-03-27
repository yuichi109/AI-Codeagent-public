# AI Code Agent プロジェクト

Azure OpenAI (gpt-5-mini) を使った高機能コードエージェント。
Web チャット UI からコード生成・編集・実行・GitLab 連携ができる。

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
cd ~/AI-Codeagent

# 初回セットアップ（venv作成・.env設定・systemd登録 等）
./setup.sh install       # または ./setup.sh でメニュー表示

# プロキシ切り替え（社内 ⇔ 社外）
sudo ./setup.sh proxy

# サービス操作
./setup.sh service
```

> ※ 旧 `setup-proxy.sh` は `setup.sh` に統合済み（削除済み）

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
prompts.py          ← 自律エージェント用システムプロンプト（行動原則・完了定義・先読み指示）
tools/
  file_tools.py     ← read_file / write_file / edit_file / list_files / glob_files / grep
  command_tools.py  ← run_command + _run_bash_sandboxed (bubblewrap)
  web_tools.py      ← web_search / web_fetch / web_research (SearXNG優先)
  code_tools.py     ← code_lint (ruff / eslint)
index.html          ← チャット UI (Catppuccin テーマ、ストリーミング・tool履歴対応)
workspace/          ← エージェントの作業ディレクトリ (Git管理外)
docker-compose.searxng.yml  ← SearXNG コンテナ定義 (ポート8888)
searxng-settings/   ← SearXNG 設定 (JSON形式有効化)
```

---

## 実装済み機能 ✅

### セキュリティ
- [x] API キーを `.env` で管理（ハードコード排除）
- [x] `shell=False` + コマンドブラックリスト（`run_command`）: mkfs/fdisk/dd/shutdown等のみ拒否
- [x] パストラバーサル防止（`_resolve_safe_path` / `Path.resolve()`）
- [x] **bubblewrap サンドボックス**（`bash script.sh` 実行時）
  - FS 全体読み取り専用、workspace のみ書き込み可
  - ネットワーク完全遮断（`--unshare-net`）
  - Claude Code (Linux/WSL2) と同じ方式
- [x] SSRF 防止（`web_fetch` でプライベート IP をブロック）

### ツール
- [x] `read_file` / `write_file` / `list_files`（パストラバーサル対策済み）
- [x] **`edit_file`**（old_str → new_str 部分置換、件数不一致エラー検出）
- [x] **`glob_files`**（再帰 glob パターン検索）
- [x] **`grep`**（正規表現・行番号付き横断検索、case_sensitive / max_results オプション）
- [x] `run_command`（ブラックリスト方式 + work_dir をworkspace相対で解決）
- [x] `bash script.sh`（bubblewrap サンドボックス経由）
- [x] `web_search`（SearXNG優先 → DuckDuckGo API → Wikipedia API フォールバック）
- [x] `web_fetch`（BeautifulSoup テキスト抽出、SSRF 対策）
- [x] `web_research`（検索→複数ページ自動取得→まとめて返す高レベルツール）
- [x] `code_lint`（Python: ruff、JS/TS: eslint）
- [x] **`render_manim`**（Manim コードをレンダリングして最終フレーム PNG を返す。LLM が視覚的に確認して自己修正できる）（2026-03-19）

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
- [x] **ストリーミング回答**（`answer_chunk` SSE イベントで delta を逐次表示）
- [x] **tool メッセージ履歴保持**（`history_messages` SSE イベントでターン間引き継ぎ）
- [x] **ツール結果折りたたみ表示**（`<details>/<summary>` 形式、デフォルト非表示）
- [x] **ツールグループ折りたたみ UI**（ターン内の全ツールを1行にまとめて折りたたむ。「N個のツールを実行 · run_command ×3」形式）（2026-03-19）
- [x] **render_manim 画像インライン表示**（ツールグループ内に PNG をインライン表示）（2026-03-19）
- [x] **LLMプロバイダー切り替えパネル**（⚙️ボタン → スライドインパネル）（2026-03-16）
  - URL入力 → `GET /providers/models` でモデル一覧取得 → ドロップダウン
  - 適用 / Azureに戻す / 切り替え時履歴リセットオプション
  - 現在のプロバイダー名をヘッダーに表示
- [x] **生成中断ボタン**（■ 停止）（2026-03-16）
  - 生成中は送信ボタンが■停止に切り替わり、`AbortController` でfetchをキャンセル
- [x] **画像添付機能**（マルチモーダル対応）（2026-03-16）
  - 📎ボタン or Ctrl+V でクリップボード画像を貼り付け
  - サムネイルプレビュー表示、Vision API（`image_url` content type）でLLMに渡す
- [x] **textarea 入力欄**（2026-03-16）
  - Shift+Enter で改行、Enter で送信
  - 入力内容に応じて高さ自動リサイズ（最大200px）

### その他の実装済み機能（2026-03-16 追加分）
- [x] **run_command をホワイトリスト → ブラックリスト方式に変更**（`tools/command_tools.py`）: 任意コマンド実行可、mkfs/dd/shutdown等のみ拒否（2026-03-18）
- [x] **systemd サービスファイル**（`ai-codeagent.service`）: WSL2 systemd 自動起動対応
- [x] **URLオートコンプリート**（index.html）: LLM設定パネルのエンドポイントURL入力欄に `<datalist>` で最大5件の履歴補完
- [x] **ローカルモデルへの tools 渡しを無効化**（server.py）: Qwen3等のJinjaテンプレートが壊れたtool_callsを生成して暴走するため。Phase 2（delegate_to_azure）で解決予定。**元に戻してはいけない**
- [x] **`_sanitize_history()`**: トリミング後に孤立したrole:toolメッセージを除去（Azure 400対策）
- [x] **ローリングサマリー**（server.py + index.html）: 履歴16件超で自動圧縮。`_recent_head_unsafe()` で境界を安全位置にスライド
- [x] **SSE done:true バッファ修正**（index.html）: ストリーム終端でbuf残留データを処理してから break
- [x] **`stripThink()` ヘルパー**（index.html）: localStorage復元時にも `<think>` タグを除去

### バグ修正
- [x] `list_files("workspace")` → `_normalize_path()` で workspace二重問題を解決
- [x] `work_dir` の相対パス解決: Python CWD 基準 → ALLOWED_WORK_DIR 基準に修正
- [x] `git init` をサブディレクトリで実行するようシステムプロンプトを整備
- [x] 社内プロキシ対応（`no_proxy` / `NO_PROXY` を `.env` に追加、`load_dotenv(override=True)` で確実に適用）
- [x] **SearXNG 自動起動**（`SEARXNG_ENABLED=true` 時、uvicorn 起動と同時に `docker compose up -d`）
- [x] **今日の日付をシステムプロンプトに動的付与**（時事情報の検索クエリに正確な日付を使用）
- [x] **httpx プロキシバイパス**（2026-03-16）: `trust_env=False` で社内プロキシを迂回（httpx 0.28+ で `proxies={}` 廃止 → `trust_env=False` に変更）
- [x] **AzureOpenAI に trust_env=False 追加**（2026-03-18）: `_make_client()` の Azure ブランチにも `http_client=httpx.Client(trust_env=False)` を追加（407修正）
- [x] **承認バイパストグル**（2026-03-18）: 🔒/🔓ボタンで即実行モード切替。`bypass_approval` フラグを ChatRequest → agent_stream → システムプロンプト + ユーザーメッセージ先頭注入で安定化
- [x] **SSE JSON parse エラー修正**（2026-03-16）: `reader.read()` チャンク境界対策としてバッファリング実装
- [x] **`<think>` タグ非表示**（2026-03-16）: Qwen の Chain-of-Thought を UI に表示しないよう 2段階 regex で除去（完結 + 未完結ブロック）
- [x] **プロバイダー設定の永続化**（2026-03-16）: `.provider_config.json` へ保存し `--reload` 後も設定維持
- [x] **停止ボタン表示バグ修正**（2026-03-16）: `style.display = ''` が CSS の `display:none` に戻る問題 → `'inline-block'` に変更
- [x] **textarea onchange 属性修正**（2026-03-16）: sed による編集で閉じクォート欠落 → HTML パーサーがボタンを飲み込む問題を修正
- [x] **ローリングサマリー実装**（2026-03-16）: 履歴16件超で古い部分をLLMが自動要約・圧縮
  - `SUMMARY_TRIGGER=16`, `SUMMARY_KEEP_RECENT=4`
  - `history_compressed` SSE でクライアントの localStorage も更新
  - **既知の設計上の注意**: `recent_part` 先頭が `role:tool` / `assistant+tool_calls` になると孤立メッセージ問題が再発する。`_recent_head_unsafe()` で境界を安全な位置まで自動スライドして対策済み。将来 `SUMMARY_KEEP_RECENT` を変更する際はこの境界ロジックを壊さないよう注意。
- [x] **履歴復元バグ修正**（2026-03-19）: WSL再起動後にツール呼び出しありのターンでAI回答が消える問題を修正（index.html `loadHistory()`）
  - 原因: `i+=2` の固定ペア方式が `[user, assistant(tool_calls), tool, ..., assistant(最終)]` 構造に対応できていなかった
  - 修正: user起点で次userまで走査し最後の assistant content を取得する方式に変更
- [x] **`/no_think` オプション追加**（2026-03-23）: ⚙️パネルに「/no_think をプロンプトに付加」チェックボックス追加
  - ON にするとユーザーメッセージ先頭に `/no_think\n` を自動挿入（Qwen3 Thinking抑制用）
  - localStorage 永続化。`ChatRequest.no_think` → server.py で挿入
  - LM Studio の "Enable Thinking" ON でもプロンプトレベルで有効（モデルが reasoning ≈ 空で応答）
- [x] **ツール結果の `\n` 文字化け修正**（2026-03-23）: ツール結果表示で `\n` がそのまま表示される問題を修正（index.html）

---

## 残タスク・改善候補

### Claude Code との差を埋める改善
- [x] **`edit_file` ツールの追加**（★★★）: old_str → new_str 部分置換（2026-03-13）
- [x] **`grep` / `glob` ツールの追加**（★★★）: 正規表現横断検索・再帰 glob（2026-03-13）
- [x] **ストリーミング回答**（★★）: `stream=True` + `delta.content` 逐次 yield（2026-03-13）
- [x] **tool メッセージの履歴保持**（★★）: `history_messages` SSE でターン間引き継ぎ（2026-03-13）
- [x] **モデルを gpt-5-mini に変更**（★）: `gpt-4.1-mini` → `gpt-5-mini`（2026-03-12）
  - ※ `gpt-5.1-codex-mini` は Responses API 専用のため Chat Completions ベースの現構成では使用不可
- [x] **自律エージェント用システムプロンプト刷新**（★★★）: 行動原則・完了定義・先読み指示（2026-03-13）
- [x] **ツール結果折りたたみ表示**（★★）: `<details>` 形式でチャット画面をすっきり保つ（2026-03-13）
- [x] **Bash 完全アクセス**（★★★）: ホワイトリスト廃止 → ブラックリスト方式（mkfs/dd/shutdown等のみ拒否）、任意コマンド実行可（2026-03-18）✅ GitLab #13 クローズ済み
- [x] **承認バイパスボタン**（★★★）: Claude Code の「許可をバイパス」相当。🔒/🔓トグル、localStorage 永続化、システムプロンプト + メッセージ注入で安定動作（2026-03-18）

### LLMプロバイダー切り替え機能
- [x] **Phase 1: 手動切り替え**（★★）（2026-03-16）
  - URL入力 → `/v1/models` を叩いてモデル一覧をドロップダウン表示
  - Azure OpenAI / LM Studio（Qwen3.5 9B 想定）を1クリックで切り替え可能に
  - APIキーもブラウザから入力できる形に（セッション内メモリ方式）
  - ローカルネットワーク内 HTTP 接続対応
  - 切り替え時に履歴リセットオプションも追加
- [ ] **Phase 2: ハイブリッドモード**（★★★）（#27）
  - **Qwen3.5-4B** をオーケストレーター（日本語会話・判断担当）として動作
  - `delegate_to_azure` ツールを追加 — コード・ツール作業と判断したらgpt-4.1に委譲
  - Qwen3.5-4B を採用する理由: VRAM 8GB余裕・CJK特化・旧7〜8B超の論理性能・MoEのJSON問題も回避
  - 思考モードは `/no_think` でOFF推奨（会話用途では速度・品質が向上）
  - 推奨サーバー: Ollama（LM StudioはQwen3.5のテンプレート不一致問題あり）
  - 実装順: #25（ツールトグル）でQwen3.5-4B + Ollama の動作を検証後に着手

### Claude Code との差を埋める改良ロードマップ

現時点で Claude Code の約 70〜75% 相当。以下が主な残差。

#### ローカルLLM関連
- [x] **ローカルLLM ツール ON/OFFスイッチ**（#25）: ⚙️パネルに「ツールを渡す」チェックボックス追加。Azure=デフォルトON、ローカル=デフォルトOFF。手動でONに切り替え可能、永続化対応（2026-03-20）

#### 大きな差（優先度 ★★★）
- [ ] **並列ツール実行**（#19）: 現在は逐次実行のみ。Claude Code は複数ツールを同時実行して高速化。`asyncio.gather` 等で並列化する仕組みが必要
- [ ] **インタラクティブプロセス管理**（#20）: バックグラウンド実行・stdin 送信・長時間プロセスの制御が弱い。`run_background` / `send_input` ツールの追加が必要
- [ ] **自動コンテキスト収集**（#21）: Claude Code はファイルツリーや git diff を自動でコンテキストに含める。現在はモデルが自分でツールを呼ぶ必要があり非効率

#### 中程度の差（優先度 ★★）
- [ ] **スラッシュコマンド**（#22）: `/clear` `/compact` `/help` 等のショートカット。入力欄で `/` から始まるコマンドを検出してUI操作に変換
- [ ] **CLAUDE.md 自動読み込み**（#23）: 作業ディレクトリの `CLAUDE.md` を自動検出してシステムプロンプトに注入。プロジェクトごとに指示を切り替え可能に
- [x] **ツール失敗時の自己修正強化**（#24）: エラー発生時に原因分析→修正→再実行のループを明示的にサポート。`execute_tool` にエラー種別ヒント追加、todo に `failed` ステータス追加、プロンプトにエラー種別対処表を追記（2026-03-21）
- [x] **過去セッション履歴パネル**（#29）: 左ペインに過去セッション一覧を表示（Claude Code スタイル）。セッションはサーバー側 JSON ファイルに保存（localStorage の容量制限回避）。過去セッション閲覧は現在の API 履歴に影響しない（トークン消費ゼロ）（2026-03-24）
- [x] **GitLabイシュー専用エンドポイント**（#16部分対応）: `GET /gitlab/issues` を追加。state パラメータで opened/closed/all を切り替え可能。per_page=100 で全件取得（2026-03-24）
- [x] **シンタックスハイライト**（index.html）: highlight.js (atom-one-dark テーマ) を CDN で追加。コードブロックを自動ハイライト（2026-03-24）
- [x] **コードブロック折り返し**（index.html）: `white-space: pre-wrap` + `word-break: break-word` で横スクロールを廃止（2026-03-24）
- [x] **タイトル・アイコン変更**（index.html）: 「Azure Code Agent」→「AI Code Agent」、アイコンを ⚛️ に変更、SVG ファビコン追加（2026-03-24）
- [x] **セッションファイルをGit管理外に**（.gitignore）: `sessions/` ディレクトリを .gitignore に追加（2026-03-24）
- [x] **prompts.py イシュー表示指示改善**（2026-03-24）: イシュー一覧を全件・Markdown表形式で出力、コードブロックで囲まない指示を追加
- [x] **複数 Azure AI Foundry インスタンス対応**（2026-03-26）: `FOUNDRY_N_*` 環境変数で N 個のインスタンスを定義可能。`FOUNDRY_INSTANCES` リスト（config.py）、`/providers/presets` の `foundry_instances` 配列、⚙️パネルの動的 `<select>` 生成
- [x] **AGENT_NAME 設定**（2026-03-26）: `.env` の `AGENT_NAME` をシステムプロンプトに注入。自己紹介時に任意の名前を名乗らせる（現在: SPEC-AI）
- [x] **セットアップウィザード `/setup`**（2026-03-26）: `setup.html` + server.py の `/setup/current`・`/setup/save` エンドポイント。ブラウザから `.env` を GUI 編集して保存・サービス自動再起動。⚙️パネル下部にリンクを追加
- [x] **Foundry api_version バグ修正**（2026-03-26）: `cognitiveservices.azure.com` URL を `openai_compatible` に誤分類していた問題を修正。正しく `foundry` タイプ・`FOUNDRY_API_VERSION` を使用

### 品質・テスト
- [ ] 各ツールの単体テスト（pytest）を書く
- [ ] bubblewrap サンドボックスの脱出テスト
- [ ] 長いプロンプトでのトークン上限テスト
- [ ] 別 PC（社内プロキシあり）での動作確認

### スキルシステム（#30）
- [x] **スキルファイル基盤**（★★★）: `skills/スキル名/SKILL.md` を定義・リクエストごとに動的読み込み（再起動不要）（2026-03-25）
- [x] **`/` オートコンプリートUI**（#22と連携）: 入力欄で `/` を打つとスキル候補をポップアップ表示（↑↓選択・Tab/Enter補完・Esc閉じる）（2026-03-25）
- [x] **エージェントによるスキル自己管理**: 会話からスキルを生成・編集・削除できる（2026-03-25）
- [x] **初期スキルセット**: `/commit` `/help` `/get-proj`（2026-03-25）

#### 登録済みスキル詳細

| スキル | 説明 | 備考 |
|---|---|---|
| `/commit` | ステージ済み変更にコミットメッセージを付けてコミット | push は明示指示時のみ |
| `/help` | スキルシステムの使い方を表示 | |
| `/get-proj` | まとめリポジトリから特定ディレクトリを取り出して独立化 | 下記注意事項参照 |

**`/get-proj` 実装上の注意事項（2026-03-25 試行錯誤で確定）:**
- `run_command` に `work_dir` を指定すると `workspace/workspace/` の入れ子になるバグが発生 → **`work_dir` 使用禁止**
- `/tmp/` は bubblewrap サンドボックス外で使えない → **workspace 内のみで作業**
- sparse checkout・GitLab アーカイブ API は不安定 → **`git clone --depth 1` + `cp` + `rm -rf` の3ステップに統一**
- `echo $HOME` でホームディレクトリの絶対パスを取得してから全コマンドに使用
- 失敗時はリトライ禁止・即エラー報告（リトライループで10コマンド以上走る問題の対策）

#### スキルシステムの使い方

| 操作 | 方法 |
|---|---|
| スキルを呼び出す | `/スキル名` と入力して送信 |
| 候補を表示 | 入力欄で `/` を打つ（↑↓選択・Tab/Enterで補完） |
| スキル一覧 | 「スキル一覧を見せて」 |
| スキル新規作成 | 「今やった手順を `/スキル名` というスキルとして覚えて」 |
| スキル修正 | 「`/スキル名` を修正して、〇〇するように変えて」 |
| スキル削除 | 「`/スキル名` スキルを削除して」 |

#### スキルファイル構造
```
skills/
  commit/SKILL.md   ← /commit スキル（git add → commit）
  help/SKILL.md     ← /help スキル（使い方表示）
```

- `skills/スキル名/SKILL.md` にフロントマター（name/description/trigger）＋手順を記述
- 追加・編集後は**再起動不要**で即反映
- `GET /skills` エンドポイントでスキル一覧をJSON取得可能

### 配布・チーム展開（#15）
- [x] **setup.sh による別PCデプロイ**（2026-03-27）: WSL2 Ubuntu への一撃セットアップ
  - 実行権限を git に記録（clone後に chmod 不要）
  - 改行コード CRLF→LF（`.gitattributes`）
  - python3-venv / bubblewrap / Docker を自動インストール
  - Docker グループにユーザー追加（sudo不要化）
  - 対話入力を廃止 → ブラウザ `/setup` で設定する設計に一本化
  - セットアップ完了時に `http://localhost:8000/setup` を表示
- [ ] **Ansibleプレイブック**（`setup.yml`）: 未着手（setup.sh で代替中）
- [ ] **Docker化オプション**（WSL2なし環境向け）: bubblewrapをオプション化し `SANDBOX=none` で無効化可能に

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
- [ ] `run_command("dd if=/dev/zero of=/dev/sda")` → ブラックリスト拒否
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
AZURE_OPENAI_DEPLOYMENT=gpt-5-mini
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
# FOUNDRY_2_NAME=別リージョン
# FOUNDRY_2_ENDPOINT=https://xxx2.cognitiveservices.azure.com/
# ...
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
```

---

## GitLab リポジトリ

- **このプロジェクト**: https://gitlab.com/yuichi.matsuo/AI-Codeagent
- **ブランチ**: main
- **最終更新**: 2026-03-26（複数Foundryインスタンス対応・AGENT_NAME・セットアップウィザード）
