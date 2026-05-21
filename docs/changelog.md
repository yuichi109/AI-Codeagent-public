# AI Code Agent — 実装済み機能・変更履歴

> このファイルは AGENT.md から分離した実装済み機能の詳細記録です。
> 作業中の参照用。新規実装時はここに追記してください。

---

## 2026-05-21（セッション5）

### 設計議論・イシュー登録（コード変更なし）

#### GitLab イシュー #54 作成：Mermaid図パイプライン
- チャット内でMermaidをレンダリングして図として表示（mermaid.js導入）
- AIがビジョン機能でPNG画像を自己チェックし、文字被り・重なりを自律修正してからユーザーに提示
- ユーザーが「完成」と言うまで修正ループを繰り返す
- 完成時に「そのまま保存 / OpenAI Imageで清書して差し替え」を毎回確認
- 縦横比は指示があれば従う（固定ではない）
- Mermaidのレイアウトズレ・文字被りはほぼ毎回発生するため、AI自己チェックが必須

#### GitLab イシュー #55 作成：インフラドキュメント自動生成マルチエージェント
- 依存: #54完了が前提（Mermaid図パイプラインを内部で呼び出す）
- 入力パターン: A=Ansibleコード+実機情報 / B=実機情報+経緯メモ / C=混在
- エージェント構成:
  - ディスパッチャー（全体計画・割り当て）
  - 情報収集エージェント（Ansible解析・SSH/WinRM実機接続・メモ読み取り）← 新規追加役割
  - ドキュメント生成エージェント群（基本設計書・仕様書・パラメーターシート、並列は後回し）
- アウトプット: MD形式、設計書・仕様書には図解多数含む
- 現行マルチエージェントの改良・拡張（別物ではない）

#### 設計方針メモ
- 並列実行（asyncio.gather）は後回しでOK。逐次でも手作業より十分速い
- #34（vSphere VM情報収集）は別物として共存
- #19はGitLabに存在しなかった（roadmap.mdのみの記載）

---

## 2026-05-20（セッション4）

### バグ修正

**変更ファイル:** index.html

- **インラインチャット DeploymentNotFound 修正**
  - `_icPopulateSelect` で `localStorage` に保存されたモデルが現在のデプロイメント一覧に存在しない場合、自動的に先頭の有効なデプロイメントにリセットするよう修正
  - プロバイダーや `.env` のモデル設定変更後に古いモデル名が残り続けることで発生する 404 エラーを解消

### その他

- `feature/multi-agent` ブランチを削除（main と同一内容のため）
- **GitLab イシュー #53 作成**: Canva 連携（生成画像をワンクリックでアップロード→編集URL取得）
  - OAuth 2.0 フロー + Canva Assets API + setup.html への設定追加が実装内容
  - Canva Magic Layers（画像をレイヤー分解）との組み合わせが主なユースケース
  - 日本語ロケールは現時点で Magic Layers 未対応のため様子見

---

## 2026-05-19（セッション3）

### 設計ドキュメント整備

**変更ファイル:** docs/multi-agent-dispatch-design.md / memory

- インフラAI 3段階ロードマップを整理・確定
  - Stage 1: ローカル/Dockerのみ（現在）
  - Stage 2: 登録済みOS環境へSSH接続（次段階）
  - Stage 3: Azure/vSphereスナップショット付きクリーン環境（最終形）
- Azure スナップショット実装方針を追記（bk-yuichi.matsuoのAnsibleコードを改変して使用）
- vSphere スナップショット実装方針を追記（community.vmwareで数行）
- RAG＋コード生成によるAnsible/PowerShell調達フローを追記
  - RAGヒット→既存コード活用、RAGミス→コーディングAIが新規生成
- プロジェクト最終ビジョン（3段階）をメモリに記録

---

## 2026-05-19（セッション2）

### マルチエージェント Phase 1 追加実装

**変更ファイル:** server.py / prompts.py / index.html / tools/multi_agent_tools.py（新規）

#### 計画確認フロー（Plan-then-Execute）
- ディスパッチャーが計画を立てた後に一度停止し「この流れで実行してよいですか？」と確認
- チャット内に「▶ 実行する」「✕ キャンセル」ボタンを直接埋め込み（変数消失リスク回避）
- 自然言語での返答に対応：
  - 「実行して」→ そのまま実行
  - 「インフラAIも追加して」→ 計画修正して再確認（`_interpret_plan_response` でLLM判定）
  - 「キャンセル」→ 中止
- `ChatRequest` に `resume_job_id` フィールド追加
- `plan.json` / `original_task.txt` をジョブディレクトリに保存（再計画時に参照）

#### ディスパッチャー制御タイムアウト
- ディスパッチャーがタスクの複雑さに応じて `timeout_sec` を設定
- `run_sub_agent()` へ `timeout_sec` を渡す配線を追加（server.py）
- プロンプトに複雑さ別タイムアウト基準表を追加（Docker 600s、Ansible 1800s 等）

#### TTS ストリーミング読み上げ
- 全文受信後ではなく文単位（`。！？\n`）でリアルタイム読み上げ
- キュー方式（`_ttsQueue`）で順番に再生、次の文を即時キューイング

#### バグ修正
- `_interpret_plan_response` の `max_tokens` → `max_completion_tokens`（gpt-5.4系対応）
- `multi_agent_stream` 内の `config.ALLOWED_WORK_DIR` → `ALLOWED_WORK_DIR`（NameError修正）

---

## 2026-05-20

### セッション履歴キーワード検索機能（#41）

**変更ファイル:** server.py / index.html

- `GET /sessions/search?q=&archive=` エンドポイント追加
- スニペット抽出：user/assistant ロールのみ対象、ターン番号・ロール（あなた/AI）付きで表示
- 検索パネル：入力欄内 ✕ クリアボタン・「アーカイブも」チェックボックス
- 検索結果：タイトル・スニペットのキーワードをハイライト（黄色）
- セッションを開くとチャット内でもキーワードをハイライト・最初のマッチへ自動スクロール
- ✕ クリアでチャット内ハイライトも同時解除
- **#41** クローズ済み・main / for_windows push 済み

### TTS（音声読み上げ）機能

**変更ファイル:** index.html のみ

- ツールバーに「🔊 読み上げ」トグルボタン追加（ON/OFF・localStorage永続化）
- AIの返答完了時に自動読み上げ（`speechSynthesis` ブラウザ標準API）
- コードブロック→「コード省略」、URL→「リンク」、インラインコードはそのまま読む
- 読み上げ中はボタンが緑でゆっくり点滅、ボタン再押しで即停止

### Web調査プロバイダー切り替え機能

**変更ファイル:** config.py / tools/web_tools.py / setup.html / server.py

- セットアップ画面「検索バックエンド」セクションにプロバイダー選択プルダウン追加
  - Tavily（デフォルト）/ Deep Research o4-mini / Deep Research o3
- OpenAI未登録時は赤色警告メッセージを表示
- Deep Research選択時の事前注意（OpenAI組織の本人確認が必要）をヒントとして表示
- `WEB_RESEARCH_PROVIDER` 環境変数で制御、`web_research` ツール呼び出し時に自動振り分け
- OpenAIプロバイダー登録済みのキーを自動使用（別途キー入力不要）

### マルチエージェント設計書更新

**変更ファイル:** docs/multi-agent-dispatch-design.md

- 役割一覧にリサーチ・セキュリティレビュー・テスト生成の3エージェントを追加

---

## 2026-05-19

### Draw.io ダークモード問題修正（Windows）

- `index.html`: Draw.io iframe に `style="color-scheme: light"` を追加。WindowsのEdge/ChromeがOSのダークモード設定をiframeに強制する問題を解消

### Windows版 セットアップ保存後の再起動バグ修正 (#51)

- `server.py`: `sys.platform == "win32"` で分岐。Windows では `threading.Timer(0.5, os._exit)` で自己終了し、`tray.py` の `_monitor` が自動再起動する
- `setup.html`: `warning` フィールドがある場合にオレンジ色で手動再起動を案内（従来は無視して成功と誤表示していた）
- Linux/WSL は従来通り `systemctl restart` を使用（動作変化なし）

---

## 2026-05-18（追記3）

### 生成元画像モーダル改善・Draw.io 組み込み

**変更ファイル:** index.html のみ

#### 生成元画像モーダル — クリップボード貼り付け対応
- **📋 クリップボードから貼り付けボタン**追加: `navigator.clipboard.read()` で画像を取得してドロップゾーンに反映
- **Ctrl+V ペースト対応**: モーダルが開いている間 `document` の `paste` イベントを拾って自動反映
- ドロップゾーンヒントに「Ctrl+V で貼り付け」を追記
- **ファイル名重複防止**: 貼り付け時のファイル名を `clipboard_<timestamp>.png` 形式に（複数回貼っても上書きされない）
- **参照解除ボタン（×）追加**: 生成元画像が設定済みのとき「🖼 生成元画像」ボタン右隣に × を表示。クリックでアクティブ状態・チャット欄のノート両方をクリア

#### Draw.io 組み込み（iframe embed 方式）
- **`✏️ Draw.io` ボタン**をトップバーに追加
- フルスクリーンパネルで `embed.diagrams.net` を iframe 表示（`ui=kennedy` でライトテーマ固定）
- **新規**: 空のダイアグラムをロード
- **開く**: ワークスペース内の `.drawio` ファイルをピッカーで選択してロード
- **💾 保存**: 現在の XML を `.drawio` ファイルとしてワークスペースに保存（ファイル名未入力時はプロンプト）
- **Draw.io 内「終了」ボタン**でパネルを閉じる（`exit` イベントをハンドル）
- **パネル再オープン時は白紙から開始**（前回の図面が残らない）
- **AIが生成した Draw.io XML を自動検出**（`<mxGraphModel>` を含むコードブロックに「✏️ Draw.io で開く」ボタンを追加）
- **`\n` バグ修正**: AI出力の literal `\n` を Draw.io 改行エスケープ `&#xa;` に自動変換
- ネットなし環境ではパネルが真っ白になるだけで他の機能に影響なし

---

## 2026-05-18（追記2）

### ウォーターマーク機能・生成元画像UI（Issue #52）

コミット: 6f1c66b / a3c350e（main・for_windows push 済み）

**変更ファイル:** tools/image_tools.py / server.py / config.py / setup.html / index.html

#### ウォーターマーク機能
- **`watermark_image` ツール追加**（`tools/image_tools.py`）: 画像にテキスト透かしを重畳。パラメータ: `image_path` / `text` / `position`（topleft/topright/bottomleft/bottomright/center）/ `color`（#rrggbb）/ `opacity`（0.0〜1.0）/ `font_size`（0=自動）。白文字＋黒影で視認性確保。保存先: `AI_Output_Images/watermarked_*.png`
- **自動ウォーターマーク適用**: `generate_image` / `edit_image` の結果に自動適用する `apply_auto_watermark()` を追加。`WATERMARK_ENABLED=true` のとき画像生成後に自動で焼き込む
- **`config.py` に `WATERMARK_*` 変数追加**: `WATERMARK_ENABLED` / `WATERMARK_TEXT` / `WATERMARK_POSITION` / `WATERMARK_COLOR` / `WATERMARK_OPACITY` / `WATERMARK_FONT_SIZE`（0=自動）
- **セットアップ画面に設定UI追加**（画像生成セクション末尾）: ON/OFFトグル・テキスト・位置・文字色（カラーピッカー）・不透明度スライダー・文字サイズスライダー（0=自動）

#### 生成元画像UI（`index.html`）
- **「🖼 生成元画像」ボタン**を入力欄左下に追加
- **専用モーダル**でD&Dまたはクリック選択。モーダルが開いている間はウィンドウレベルのdrop/dragoverを無効化（通常の添付処理と混在しない）
- **アップロード済みサムネイル一覧**（TEMP内の画像を新しい順に表示）。クリックで即選択・切り替え可能
- **保存先**: スコープ選択中は `workspace/{scope}/TEMP/`、未選択は `workspace/TEMP/`
- **`/workspace/upload`** に `folder` クエリパラメータを追加（既存の添付アップロードに影響なし）
- **`/workspace/temp-images`** エンドポイント追加: スコープ配下のTEMPフォルダ内画像一覧をJSON返却
- 確定時、チャット欄に `edit_image` 使用を明示するノートを挿入。再選択時は差し替え

#### GitLab
- **#52** ウォーターマーク機能 → **クローズ済み**

---

## 2026-05-18（追記）

### 画像生成プロバイダー改善（コミット: 7db17a3）

**変更ファイル:** setup.html / tools/image_tools.py / server.py / index.html

#### 主な変更内容
- **Azure/Foundry モデル名をテキスト入力化**: setup.html で Azure/Foundry 選択時はプルダウンではなく自由テキスト入力（任意のデプロイ名を指定可）
- **Azure/Foundry の Bearer 認証対応**: `_make_client()` で `azure_ad_token` を使用（Global Standard デプロイメントに対応）
- **`max_retries=0` 追加**: 認証エラー等が即座に返るよう修正（従来は300秒タイムアウトまで待っていた）
- **`edit_image` を Azure/Foundry でも使用可能に**: プロバイダーガードを撤廃
- **生成画像にプロバイダー/モデルバッジ表示**: チャット画面・リロード復元時ともに表示（例: `azure / gpt-image-1.5`）
- **ファイルツリーで画像ファイルをクリック → 新タブで表示**: Monaco エディタで文字化けしていた問題を解消（png/jpg/jpeg/gif/webp/bmp/svg/ico 対応）
- **`/workspace/image` に `Content-Disposition: inline; filename` を付与**: 右クリック保存時に正しいファイル名・拡張子で保存されるよう修正

---

## 2026-05-18

### 画像生成機能の強化（Issue #50 完了）

コミット: 02506be（main push 済み）

**変更ファイル:** config.py / server.py / setup.html / index.html / tools/image_tools.py

#### 主な変更内容
- **ワークスペーススコープ対応**: 画像保存先をスコープ配下に変更（例: `GRAAA/AI_Output_Images/generated_xxx.png`）。`_save_to_workspace()` に `workspace_scope` パラメータ追加、server.py でツール実行前に注入
- **保存ディレクトリ名変更**: `images/` → `AI_Output_Images/`（プロジェクト変数との衝突回避）
- **セッション履歴にツールブロック永続表示**: リロード後もツール呼び出し・結果・画像がすべて復元される。`_restoreToolBlocks()` ヘルパー追加、`loadHistory()`・`_renderSessionContent()` を更新
- **`/workspace/image` エンドポイント追加**: PNG等バイナリファイルを `FileResponse` で配信
- **セットアップ画面「引き継ぐ」トグル**: 全プロバイダー共通。ON=チャット設定流用、OFF=画像生成専用エンドポイント・APIキーを別途指定。Azure/Foundry はエンドポイント・APIキー・APIバージョンも独立設定可能
- **タイムアウト調整**: 高解像度（1536×1024・1024×1536・1792×1024・1024×1792）は600秒、それ以外は300秒。ステータスラインに「画像生成中...（タイムアウト: 300秒）」表示

#### config.py
- `IMAGE_INHERIT`・`IMAGE_OPENAI_API_KEY`・`IMAGE_GEMINI_API_KEY`・`IMAGE_AZURE_*`・`IMAGE_FOUNDRY_*` を追加

#### tools/image_tools.py
- `_make_client()` を `IMAGE_INHERIT` フラグで分岐（引き継ぐ/別途指定）
- `_save_to_workspace()` にスコープ対応を追加
- `generate_image()`・`edit_image()` に `_workspace_scope` パラメータ追加

### GitLab イシュー
- **#50** 画像生成対応 → **クローズ予定**（テスト確認済み）
- **ウォーターマーク機能** → 新規イシュー登録予定（Pillow で後処理焼き込み）

---

### write_pptx 画像埋め込み対応

コミット: （本セッション末尾、main push 済み）

**変更ファイル:** tools/office_tools.py / server.py

#### office_tools.py
- `write_pptx()` に画像埋め込みを追加。スライドごとに以下の3レイアウトに対応:
  - `image_path`（または `image`）のみ → 画像中央配置
  - `content`（または `text`）+ `image_path` → 左テキスト・右画像
  - `content` のみ → 従来のテキストスライド
  - `elements` 配列形式（`{"type":"image","path":"..."}` のモデル独自形式）もフォールバックで対応
- `read_pptx()` に `image_count` フィールドを追加（`shape_type == 13` で判定）

#### server.py
- `write_pptx` ツール説明を更新（image_path フィールドの使い方を明記）

---

## 2026-05-15

### 本家 OpenAI API プロバイダー対応（Issue #46）

コミット: c31d883（main / for_windows 両ブランチ push 済み）

**変更ファイル:** config.py / server.py / setup.html / index.html / .env.example

#### config.py
- `OPENAI_API_KEY` / `OPENAI_MODEL`（デフォルト: `gpt-5.4`）/ `OPENAI_MODELS` を追加

#### server.py
- `_OPENAI_DEFAULT_MODELS` 定数追加: `["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-4.5", "gpt-4o", "gpt-4o-mini", "o3", "o4-mini"]`
- `_make_client()` / `_make_async_client()` に `openai` タイプを追加（`OpenAI` / `AsyncOpenAI` を `trust_env=False` で初期化）
- `/providers/presets`・`/providers/preset`・`/providers/config`・`/providers/deployments`・`/providers/deployment` に openai ケースを追加
- `/setup/current`・`/setup/fetch-models`（`/v1/models` を呼び出し gpt*/o* に絞り込み）・`/setup/save` に openai 対応を追加

#### setup.html
- プロバイダータイプに `openai`（OpenAI (api.openai.com)）を追加
- OpenAI SVG ロゴ追加
- `renderFields()` に openai ケース追加（API キーフィールド + モデル選択 + 🔍 自動取得ボタン）

#### index.html
- OpenAI SVG ロゴ追加
- `loadFoundryInstances()` / `loadCurrentProvider()` / `presetToType()` / `syncPresetUI()` / `onPresetChange()` に openai ケースを追加
- `switchToOpenAIPreset()` 関数追加（Gemini プリセット切り替えと同パターン）
- プロバイダー表示ラベルに "OpenAI" を追加

#### .env.example
- `OPENAI_API_KEY` / `OPENAI_MODEL=gpt-5.4` / `OPENAI_MODELS` のコメント付きサンプルを追加

### バグ修正: OpenAI プロバイダー使用時のインラインチャット モデル選択不可

コミット: 053378d（main / for_windows 両ブランチ push 済み）

- `index.html` の `loadInlineChatModels()`（旧 line 3568）で openai タイプを早期 return の除外対象に追加
  - 修正前: `if (pv.type !== 'azure' && pv.type !== 'foundry' && pv.type !== 'gemini') return;`
  - 修正後: `... && pv.type !== 'openai') return;`

### GitLab イシュー整理

- **#46** OpenAI プロバイダー対応 → **クローズ**（本セッションで実装完了）
- **#47** Anthropic プロバイダー → **クローズ**（要件なし・OpenRouter 経由でも Anthropic API 単体追加のメリット薄）
- **#50** 画像生成対応（gpt-image-2）→ **新規登録**（テキスト→画像生成 + img2img ユースケース含む）
- **#51** Windows版 セットアップ保存後の再起動が正常動作しない →  **新規登録**
- **#46, #48, #49, #50, #51** にラベル追加（enhancement / bug + priority::low/medium）

### 調査・確認メモ

- **OpenAI API 利用開始**: 従量課金（クレジット先払い方式）。Tier 1 は gpt-5.4 が 10,000 TPM 上限。システムプロンプトが大きい用途には gpt-5.4-nano か gpt-4o-mini 推奨
- **モデル日付サフィックス**: `gpt-5.4-nano-2026-03-17` のような日付付きモデルはスナップショット（固定バージョン）。日付なし = 最新エイリアス（OpenAI 公式確認済み）
- **Azure Model Router**: OpenRouter.ai とは別物。Azure 独自のモデルルーティング機能（East US 2 / Sweden Central のみ）。追加料金は 15% プレミアム（Azure 公式価格ページで確認済み）
- **gpt-image-2 料金**: 低品質 $0.006 / 中品質 $0.053 / 高品質 $0.211（1024×1024 per image）。img2img（元画像 → 清書・スタイル変換）にも対応

---

## 2026-05-14（セッション3）

### Windows版 シェルパネル・エディタ 文字化け修正（`for_windows` ブランチのみ）

コミット: a236816, e6aab5a（for_windows ブランチ push 済み）

- **シェルパネル出力の文字化け修正**: PowerShell 実行前に `[Console]::OutputEncoding = UTF8` を設定。コマンド入力欄・`.ps1` ファイル実行両方に適用
- **`.ps1` ファイルの文字化け修正**: エディタから保存時に UTF-8 BOM 付き（`utf-8-sig`）で書き込むよう変更。PowerShell 5.1 が BOM を見て UTF-8 と認識するため日本語スクリプトが正常動作
- `.ps1` 以外のファイルへの影響なし

---

## 2026-05-14（セッション2）

### エディタ機能強化・シェルパネル改善（`index.html` / `server.py`）

コミット: 3cd5494（main / 5373820・for_windows 両ブランチ push 済み）

#### エディタ ファイルツリー 右クリックメニュー

- **✏ 名前変更**: `/workspace/rename` エンドポイント（新規）を呼び出し。同名ファイルは 409 エラー
- **📋 複製**: `/workspace/copy` エンドポイント（新規）。`filename_copy.txt`、重複時は `_copy2`…
- **⬇ ダウンロード**: Monaco モデルの内容から Blob URL を生成してローカル保存（ファイルのみ）
- **🗑 削除**: 既存の `/workspace/cleanup` を流用。保護対象はサーバー側でブロック
- フォルダ右クリック時は「複製」「ダウンロード」を非表示

#### エディタ ツールバー

- **LF/CRLF ボタン**: ファイルを開くと現在の改行コードを表示。クリックで切り替え・保存に反映
- **🔍 検索ボタン**: Monaco の find ウィジェットを起動（Ctrl+F と同等）。ファイル未選択時はグレーアウト

#### シェルパネル コマンド履歴

- ↑キーで過去のコマンドを遡る、↓キーで戻る
- 実行時に履歴に追加（直前と同じコマンドは重複追加しない）
- 最大200件保持。編集途中の内容は ↓ で最下段に戻ると復元

#### Windows版 シェルパネル PowerShell 対応（`for_windows` ブランチのみ）

コミット: 5d6e336

- `IS_WINDOWS` フラグで Linux/Windows を分岐
- コマンド入力欄: `powershell.exe -Command` で実行（`ls` `dir` `pwd` `mkdir` 等が動く）
- `.ps1` ファイル: `powershell.exe -File` で実行・スクリプト一覧にも表示
- `.sh` ファイル: Git Bash があれば実行、なければエラーメッセージ表示

#### GitLab イシュークローズ

- **#42** Monaco インラインチャット（2026-05-13 実装済み）→ クローズ
- **#39** セッション履歴の自動アーカイブ（2026-05-07 実装済み）→ クローズ

---

## 2026-05-14（セッション1）

### インラインチャット キーワードモデル自動切り替え（`index.html`）

コミット: 9571fbe, 1c265db

#### 設定UI（⚙ボタン）

- ヘッダーに ⚙ ボタンを追加。クリックで設定パネルを展開/折りたたみ
- 設定パネルに「デフォルトモデル」と「上位モデル（キーワード検出時）」の2つのセレクトを表示
- 選択肢は `/providers/deployments` から自動取得
- 選択値は `icDefaultModel` / `icSmartModel` として localStorage に保存
- **バグ修正**: 初回ロード時（未変更）は localStorage に値が書かれず切り替えが発火しなかった問題を修正

#### キーワード自動切り替え

- `_IC_SMART_KW` 正規表現: 追加・修正・変更・書いて・作って・実装・直して・書き換え・リファクタ・改善・移動・削除など
- キーワード検出 → 上位モデルで1回のみ送信 → 返答後にデフォルトへ自動復帰
- デフォルトと上位が同じモデルなら切り替え無効

#### 強制モードボタン

- 入力エリア下部に「自動 / 上位▲ / 下位▼」の3ボタンを常時表示
- 上位▲: キーワード関係なく常に上位モデル
- 下位▼: キーワード検出されてもデフォルトモデルのまま
- 選択状態は `icForceMode` として localStorage に保存・チャット開閉をまたいで維持

#### 返答ラベル

- AIの返答バブル上部に使用モデル名をグレー（`.ic-model-label`）で表示
- 実際にどのモデルが応答したか常に確認できる

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

---

## 2026-05-13（設計議論・コード変更なし）

### インラインチャット キーワードモデル切り替え 設計確定

次回実装に向けた設計方針を確定（`docs/inline-chat-design.md` に次回追記予定）。

**確定事項:**
- モデルが2種類以上定義済みの場合のみキーワード切り替えを有効化（1種類のみの場合は無効）
- デフォルトモデルはユーザーが変更可能
- モデルは `{ provider, model }` ペアで管理（将来のクロスプロバイダー対応のため）

**フェーズ分け:**
- フェーズ1（次回実装）: 同一プロバイダー内（Azure OpenAI の DEPLOYMENTS 内）で切り替え
- フェーズ2（将来）: プロバイダーをまたいで切り替え — `azure` / `foundry` / `gemini` / `local` / `anthropic`
  - OpenRouter は対象外（ローカルLLMまで含めるなら自前クロスプロバイダー対応が適切）

**現在使用中のモデル（参考）:**
`gpt-5.4-nano, gpt-5.4-mini, gpt-4.1-mini, gpt-5-mini, gpt-4.1`（主力は5.4系）

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
