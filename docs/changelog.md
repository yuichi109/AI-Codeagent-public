# AI Code Agent — 実装済み機能・変更履歴

> このファイルは AGENT.md から分離した実装済み機能の詳細記録です。
> 作業中の参照用。新規実装時はここに追記してください。

---

## 2026-05-30（セッション21）

### Windows版 start.bat 新規PCセットアップ安定化 + Playwright MCP スクショ問題の根本解決（v1.4.3）

#### 背景（一日中ループした問題）

新規Windows PCで `start.bat` 実行後、チャットUIで「google.comのスクショ撮って」が
`Browser "chrome-for-testing" is not installed` で失敗し続けた。
エージェントが自力で `npx @playwright/mcp install-browser` を実行すると
**ダウンロード100%完了後にフリーズ**（npx経由のみ発生する既知のWindowsバグ）。

#### 根本原因（npmレジストリで実物確認）

`@playwright/mcp` は**全バージョンが playwright の alpha 版に依存**しており、
chromium リビジョンが playwright のマイナー版ごとに変わる。`@latest` だと pip 安定版とズレる。

| パッケージ | 依存 playwright | chromium revision |
|---|---|---|
| `@playwright/mcp@0.0.74` | 1.60.0-alpha | **1223** |
| `@playwright/mcp@0.0.75`（当時のlatest） | 1.61.0-alpha | 1224 |
| pip 安定版 playwright 1.60.0 | — | **1223** |

#### 確定した解決策（両方固定して chromium-1223 に一致させる）

- `start.bat`: ブラウザは npx ではなく `venv\Scripts\python.exe -m playwright install chromium` で入れる（固まらない）
- `start.bat`: `pip install playwright==1.60.0`（版数明示・chromium-1223）
- `config/mcp_servers.json`: `@playwright/mcp@latest` → `@playwright/mcp@0.0.74`（chromium-1223）
- **片方だけ更新するとズレて再発する**。更新時は両者の chromium revision を必ず一致させること。
- 詳細・確認コマンドは `CLAUDE.md` の「壊してはいけない設計」に記載。

#### start.bat のその他の修正

- Playwright インストールチェックを `chromium-*` フォルダ存在で判定（既存ならスキップ）
- Node.js 新規インストール直後に PATH を即時反映
- `pause` で結果確認できるよう維持（成功/失敗が見えないまま閉じる問題の対策）

#### 確認済み環境

- 新規Windows PC（Administrator・OSリフレッシュ後まっさら）: `git clone` → `start.bat` →
  Python/Node.js 自動インストール → chromium-1223 インストール → トレイ起動 →
  チャットUIで google.com スクショ撮影 **成功（実機確認）**

---

## 2026-05-28（セッション20 後半）

### MCP obsidian アイドルタイムアウト対策

#### 原因調査

- `obsidian-mcp` パッケージに `ConnectionMonitor` クラスが実装されており、起動30秒後から監視開始、**最後のツール呼び出しから60秒でサーバー自動終了**する仕様
- これにより「使ってしばらく経ってから呼ぶとタイムアウト」が発生していた（WSL・Windows 両方）
- `cmd.exe /c npx` ラッパーが obsidian-mcp 終了後も pipe を保持するため EOF が伝わらず、60秒ハングしてからタイムアウトになっていた

#### 修正内容（`tools/mcp_client.py` / `config/mcp_servers.json`）

- `mcp_servers.json` に `idle_timeout` フィールドを追加（obsidian: 60秒）
- ツール呼び出し前にアイドル時間をチェックし、`idle_timeout - 15秒` を超えていたらクエリ送信前にプロアクティブ再接続
- タイムアウト発生時は `session = None` にして次回呼び出しで自動回復
- npx 再起動に `--prefer-offline` フラグを追加（キャッシュ済みなら数秒で再接続）

#### テスト

- 未実施（明日予定）

---

## 2026-05-28（セッション20）

### Windows版 マルチPC対応・安定化バグ修正

#### バグ修正

- `tray.py` — 起動時にポート8001を占有する既存プロセスを自動終了する `_free_port()` を追加
  - 原因: tray は自分が起動したプロセスしか管理しておらず、別プロセスがポートを占有すると新サーバーが起動失敗していた
- `start.bat` — Node.js 自動インストール・Playwright Chromium セットアップを追加（setup.bat には既存、start.bat に抜けていた）
- `tools/mcp_client.py` — `OBSIDIAN_VAULT_PATH` 等の環境変数が未設定の場合、そのMCPサーバーを自動スキップして起動エラーを防ぐ

#### 確認済み環境

- yuichi.matsuo Windows PC（AI-Codeagent-win）: tray 起動・MCP 34ツール・Obsidian テスト全PASS
- ymatsuo PC: Node.js インストール後・tray 起動・MCP 34ツール PASS

#### 次回テスト予定

- 新規PC への Windows 版インストールテスト（start.bat からの一発セットアップ確認）

---

## 2026-05-28（セッション19続き）

### Windows版 Obsidian MCP 動作確認・バグ修正

#### バグ修正

- `tools/mcp_client.py` — Windows で `npx`（`.cmd` ラッパー）を asyncio subprocess で直接起動すると stdout pipe が届かない問題を修正
  - `os.name == "nt"` かつコマンドが `npx` の場合、自動で `cmd.exe /c npx ...` にラップする
  - 原因: Python asyncio の ProactorEventLoop が `.cmd` ファイルの pipe stdout を正しく読み取れない Windows 固有の挙動

#### テスト結果（Windows版・全PASS）

| テスト | ツール | 結果 |
|---|---|---|
| vault一覧取得 | `obsidian__list-available-vaults` | ✅ ai-agent |
| ノート読み取り | `obsidian__read-note` | ✅ ようこそ.md |
| ノート作成 | `obsidian__create-note` | ✅ MCPテスト.md |
| 検索 | `obsidian__search-vault` | ✅ キーワード「MCP」→ 1件ヒット |

---

## 2026-05-28（セッション19）

### for_windows リベース・Windows版テスト全PASS

#### 作業内容

- `for_windows` ブランチを `main`（セッション18）に rebase（force push）
  - コンフリクト：`docs/changelog.md` → HEAD（main側）を採用してスキップ
  - draw.io iframe 遅延読み込み修正コミット（e476e73）のみ残存
- Windows クローン（`C:\Users\yuichi.matsuo\AI-Codeagent-win`）を `origin/for_windows` に reset
- 依存パッケージ一括インストール（`pip install -r requirements.txt`、`bs4` 等が未インストールだった）
- uvicorn をポート8001で起動してChrome実機テスト

#### テスト結果（全PASS）

| 項目 | 結果 |
|---|---|
| サーバー起動（ポート8001） | ✅ |
| チャット応答（ストリーミング・OpenAI gpt-5.4-nano） | ✅ |
| シェルパネル（`echo hello windows` → exit 0） | ✅ |
| エディタパネル（ファイルツリー・ファイル開く） | ✅ |
| LLMパネル（プロバイダー設定表示） | ✅ |
| Draw.io（iframe読み込み完了・シェイプパレット表示） | ✅ |
| `GET /version` → `{"version":"1.4.0"}` | ✅ |
| バージョン表記（右上 v1.4.0） | ✅ |

#### 備考

- Obsidian Vault 未設定 warning → `.env` に `OBSIDIAN_VAULT_PATH` 未設定のため想定内
- MCP obsidian → 接続失敗（Vault未設定のため想定内）
- MCP playwright → 23ツール登録成功

---

## ロードマップ整理メモ（2026-05-26確認）

以下の項目がロードマップ上は「未完了」になっていたが、実装済みと確認。

- **#20 バックグラウンド実行**: `tools/background_tools.py` に `run_background` / `check_background` / `kill_background` 実装済み・TOOL_REGISTRY 登録済み。未実装は `send_input`（stdin送信）のみ。
- **#5 `write_pdf`**: `tools/pdf_tools.py` に実装済み・TOOL_REGISTRY 登録済み。
- **#6 pytest 単体テスト**: `tests/test_code_tools.py` / `test_command_tools.py` / `test_file_tools.py` / `test_web_tools.py` 実装済み。
- **#19 並列ツール実行**: `server.py` にて `asyncio.create_task` による並列実行実装済み。`run_command` 等ストリーミングツールのみ逐次、それ以外は全件並列。
- **#27 LLMプロバイダー切り替え Phase 2**: Azure / Foundry 複数インスタンス（`FOUNDRY_N_*`）/ Gemini / OpenAI / ローカルモデルのクロスプロバイダー切り替えが `server.py` に実装済み。`delegate_to_azure` という特定ツールではなく `preset_id` ベースの汎用切り替えとして超えた形で完了。

---

## 2026-05-28（セッション17）

### Obsidian inbox 監視ワーカー実装・README更新・各種UI整備

#### 変更ファイル

- `tools/inbox_worker.py` — 新規作成
  - `{vault}/AI-Codeagent/inbox/{hostname}_wsl|win/` を定期ポーリング（デフォルト15分）
  - ファイル検出時: inbox → processing → done の3ステップで移動（二重実行防止）
  - 成果物を `results/{hostname}_wsl|win/{date}/{job_id}/result.md` に書き出し
  - `_` 始まりファイルはスキャン除外（テンプレート・README 用）
  - 起動時に `_TEMPLATE.md` を inbox に、`_README.md` を drafts に自動生成
  - `get_stale_drafts()` — drafts フォルダで2時間以上放置されたファイルを検出
  - `drafts/` フォルダも自動作成（ホスト名なし・全PC共通）
- `config.py` — `OBSIDIAN_INBOX_ENABLED` / `OBSIDIAN_INBOX_POLL_SEC` 追加（範囲: 60〜86400秒でクランプ）
- `server.py`
  - lifespan に inbox ワーカー起動・停止を登録
  - `_inbox_process()` — MD を読み込み frontmatter パース → `_agent_stream_inner` でエージェント処理 → results 書き出し
  - 空本文・読み込みエラー時も processing/ に残らないよう修正
  - `GET /inbox/status` エンドポイント追加
  - `POST /inbox/scan` エンドポイント追加（即時スキャン）
  - `GET /inbox/draft-alerts` エンドポイント追加（放置下書き検出）
- `index.html`
  - スコープバーに 📥 inbox ボタン追加（`OBSIDIAN_INBOX_ENABLED=true` 時のみ表示）
  - 放置下書きがあると赤バッジ表示・クリックでファイル名と経過時間のポップアップ
  - 15分ごとに draft-alerts を自動チェック
- `setup.html` — Obsidian 連携セクションに inbox トグル・ポーリング間隔入力を追加
- `skills/inbox-scan/SKILL.md` — `/inbox-scan` スキル追加
- `skills/archive/SKILL.md` — アーカイブ設定（OBSIDIAN_VAULT_PATH の場所）を追記
- `README.md` — MCP・Obsidian 連携・通知・draw.io・`/archive` スキルのセクションを追加
- `.env.example` — Obsidian inbox 設定項目を追記

#### フォルダ構成（Vault 内）

```
{vault}/AI-Codeagent/
  drafts/                      ← 下書き置き場（全PC共通）
  inbox/{hostname}_wsl|win/    ← 処理待ち（ここに移動で実行指示）
  processing/{hostname}_wsl|win/  ← 作業中
  done/{hostname}_wsl|win/     ← 完了済みリクエスト
  results/{hostname}_wsl|win/{date}/{job_id}/  ← 成果物
```

#### 動作確認済み（セッション17）

- inbox ワーカー起動・ポーリング ✅
- シンプルなリクエスト処理（inbox → results 書き出し）✅
- frontmatter 付きリクエスト処理 ✅
- 空本文スキップ（processing/ に残らない）✅
- 二重スキャン防止 ✅
- `_TEMPLATE.md` スキャン除外 ✅
- `/inbox/draft-alerts` API（放置検出）✅
- 📥 inbox ボタン表示 ✅

#### 未テスト → セッション18 で全項目 PASS

---

## 2026-05-28（セッション18）

### inbox ワーカー全テスト PASS・バージョン表記追加・feature/mcp-client → main マージ

#### テスト結果（Chrome 実機確認）

- 放置バッジ UI 表示 ✅（赤バッジ `1` がスコープバーに表示）
- 放置バッジ ポップアップ ✅（ファイル名・経過時間が表示）
- `/inbox-scan` スキル チャット呼び出し ✅（バグ発見・修正後に PASS）
- `/setup` inbox トグル保存・読み込み ✅（.env 書き込み → 再起動 → 反映確認）
- ポーリング自動処理 ✅（60秒間隔で自動処理・results/ 書き出し確認）

#### バグ修正

- `skills/inbox-scan/SKILL.md` の curl 例に `-X POST` が欠けており AI が GET を送信して 405 になる問題を修正

#### 新機能：バージョン表記

- `config.py` に `APP_VERSION = "1.4.0"` を追加
- `GET /version` エンドポイント追加（AI からも参照可能）
- スコープバー右端に `v1.4.0` を表示（クリックでパブリックミラーを開く）
- `CLAUDE.md` にバージョン管理ポリシーを追記

**バージョン管理ポリシー（採用）:**
| バージョン | 条件 |
|---|---|
| メジャー | Stage 移行（マルチエージェント実用化 → `2.0.0`） |
| マイナー | 新機能のまとまり完成 |
| パッチ | バグ修正・微調整 |

#### ブランチ

- `feature/mcp-client` → `main` マージ・push 完了（セッション12〜18 分）
- `for_windows` への反映は次回 Windows 環境確認時に実施

---

## 2026-05-27（セッション16）

### draw.io タイトルバーファイル名表示・/archive スキル（Obsidian アーカイブ）

#### 変更ファイル

- `index.html`
  - draw.io「開く」ボタンからファイルを開いたとき、タイトルバーにファイル名が表示されない問題を修正
    - `toggleDrawioPanel()` が `value=''` でクリアする前にファイル名を設定していた順序バグを修正
    - `_drawioLoad()` に `filename` 引数を追加し `<mxfile><diagram name="...">` でラップして送信
  - `updateScopeBar()` にアーカイブ済みバッジ（📦）表示を追加
    - `.archived` マーカーファイルが存在する場合にスコープバー横に📦を表示（ホバーで日時確認可）
  - `archive_workspace` ツール完了時に📦バッジをリアルタイム更新
- `tools/workspace_tools.py` — `archive_workspace(scope)` 関数を追加
  - 現在の作業ディレクトリを `{vault}/archives/{hostname}_wsl|win/{scope}/` にコピー（`cp -u` 相当・削除は反映しない）
  - 完了後に `workspace/{scope}/.archived` マーカーファイルを書き込み
  - ホスト名＋プラットフォーム（wsl/win）でパス分岐し、同一PC上のWSL版/Windows版衝突を防止
- `server.py`
  - `archive_workspace` を TOOL_REGISTRY・TOOLS に登録
  - `OBSIDIAN_VAULT_PATH` を import に追加
  - `GET /workspace/archive-info` エンドポイント追加（スキル用・vault パス・コピー先などを返す）
  - `GET /workspace/archived-status` エンドポイント追加（.archived の有無・日時を返す）
- `skills/archive/SKILL.md` — `/archive` スキル追加
  - 「アーカイブして」で `archive_workspace` ツールを呼ぶだけのシンプルな定義

#### 動作確認済み

- draw.io「開く」ボタン → タイトルバーにファイル名表示 ✅
- 「アーカイブして」→ Obsidian vault の正しいパスにコピー ✅（nano/mini 両モデルで確認）
- アーカイブ完了後に📦バッジが即時表示 ✅
- ページ更新後も📦バッジが復元 ✅

---

## 2026-05-27（セッション15）

### MCP サーバー管理 UI・draw.io 「開く」ボタン

#### 変更ファイル

- `setup.html` — MCP サーバー管理セクション追加
  - 登録済みサーバーの一覧・有効/無効切り替え・削除
  - テンプレート選択（Playwright / Obsidian / Filesystem / GitHub / Brave Search / Memory / Sequential Thinking / Draw.io）→ 名前・コマンドを自動入力
  - 新規サーバー追加フォーム（コマンド＋引数をスペース区切りで1行入力）
  - デフォルトリセットボタン（Playwright + Obsidian に戻す）
- `server.py` — `GET /mcp/servers`・`POST /mcp/servers` エンドポイント追加（`mcp_servers.json` の読み書き・サービス再起動）
- `index.html` — `write_file` で `.drawio` ファイル保存時に「📐 Draw.io で開く」ボタンを表示
  - ストリーミング時：ツールグループの外（常に見える位置）に表示
  - 履歴復元時（ページ更新後）も同様にボタンを復元
  - draw.io ロード後に `fit` アクションを送信してビューを自動調整

#### 動作確認済み

- MCP サーバー管理セクション表示・テンプレート自動入力 ✅
- MCP クラッシュ回復テスト（kill -9 → 自動再接続・ツール呼び出し正常） ✅
- draw.io 保存後「📐 Draw.io で開く」ボタン表示 ✅
- ページ更新後もボタン復元 ✅（未確認・次セッションで要確認）
- draw.io fit アクション ✅（未確認・次セッションで要確認）

---

## 2026-05-27（セッション14）

### メール通知・Obsidian MCP・/setup UI 拡張

#### 新規ファイル

- `tools/notify_tools.py` — Gmail メール通知（smtplib）・10分クールダウン

#### 変更ファイル

- `server.py` — スクリーンショットを `workspace/playwright-screenshots/` に自動移動・`.jpg/.webp/.gif` 対応・メール通知フック（エラー時・「メールで通知して」指示時）・Obsidian MCP 有効化 API（`_get_mcp_enabled` / `_set_mcp_enabled`）・`SetupSaveRequest` に `email_notify` / `obsidian` フィールド追加
- `setup.html` — draw.io iframe 遅延読み込み（起動時エラーダイアログ解消）・メール通知セクション追加・Obsidian 連携セクション追加（Vault パス・MCP 有効/無効）
- `config.py` — `NOTIFY_EMAIL_*` / `OBSIDIAN_VAULT_PATH` 追加・Vault パスを `ALLOWED_WORK_DIRS` に自動追加
- `config/mcp_servers.json` — Obsidian MCP を有効化（標準暗号化 Vault で動作確認済み）
- `prompts.py` — メール通知ルール追加（設定済み/未設定で案内を分岐）・Obsidian MCP 空クエリ禁止・`list_files` 誘導

#### 動作確認済み

- Gmail メール通知（天気調査 → メール送信 Pass）
- Obsidian MCP `read-note` / `list_files` によるノート読み取り Pass
- `/setup` からの Vault パス・MCP 有効化の保存 Pass
- draw.io 起動時エラーダイアログ解消 Pass

### draw.io `addGCP3Palette is not a function` エラー調査・対応（セッション14後半）

#### 問題の概要

draw.io パネルを開くと `this.addGCP3Palette is not a function` という**別ウィンドウの alert ダイアログ**が表示されるバグ。

- **原因**: draw.io サービス側（`embed.diagrams.net`）のバグ。`kennedy` テーマ使用時に GCP3 パレット初期化コードが実行されるが、`addGCP3Palette` メソッドが `EditorUi` インスタンスに存在しない
- **進行性**: `embed.diagrams.net` の localStorage に GCP3 設定が蓄積されるにつれて悪化し、数回使用後に毎回発生するようになる
- **draw.io 側のバグと確認**: プルしていない旧バージョン（localhost:8001）でも同様に再現。我々のコード変更が原因ではない

#### 試みた修正（効果なし）

1. `&ui=kennedy` テーマ削除 → エラー解消せず
2. `&libs=0` 追加 → 解消せず
3. `&configure=1` 追加 → draw.io が `configure` イベント待ちのまま `init` を送信しなくなり描画不可に（**破壊的**・取り消し済み）
4. `/drawio-proxy` サーバーサイドプロキシ + polyfill 注入 → draw.io JS が非 diagrams.net ドメインを拒否して `init` 未発火
5. iframe を `data-src` に変更して遅延読み込み → 解消せず（取り消し済み・`src=` に戻した）

#### 現在適用中の対応

```html
<iframe id="drawio-iframe"
  src="https://embed.diagrams.net/?embed=1&spin=1&modified=unsavedChanges&proto=json&lang=ja&ui=kennedy"
  allow="clipboard-read; clipboard-write"
  sandbox="allow-scripts allow-forms allow-popups allow-downloads"
  style="color-scheme: light">
</iframe>
```

`sandbox` 属性で **`allow-same-origin` を除外** → iframe に opaque オリジンが付与され `embed.diagrams.net` の localStorage にアクセス不可になるため GCP3 設定が読み込まれない。加えて **`allow-modals` を除外** → alert ダイアログが表示されない。

#### 未確認事項

- sandbox 適用後の実動作テスト（ユーザー側で未実施・次セッションで確認）
- draw.io 公式 GitHub Issue の調査（次セッションで実施予定）

---

## 2026-05-26（セッション13）

### MCP クライアント Phase 1 テスト完了・スクリーンショット UI 表示・自動再接続対応

#### 変更ファイル

- `config/mcp_servers.json` — `--output-dir ./workspace/playwright-screenshots` を追加（スナップショット・コンソールログをワークスペース配下に保存）
- `tools/mcp_client.py` — ImageContent を base64 変換してワークスペースに保存・自動再接続ロジック追加（`_reconnect` メソッド・空エラーや接続切断系の例外で再接続＋1回リトライ）
- `server.py` — `playwright__browser_take_screenshot` 等の結果テキストに含まれる PNG パスを検出してファイルを読み込み `image_generated` SSE として送信

#### テスト結果（全項目 Pass）

1. **`__` 既存ツール衝突チェック** — `__skipped__` は tc_id に付与、ツール名には無関係。問題なし。
2. **スクリーンショット UI 表示** — `navigate` → `take_screenshot` でチャット UI に画像がインライン表示。
3. **連続ツール呼び出し** — 2 ツール連続呼び出し（navigate → screenshot）正常動作。
4. **MCP サーバークラッシュ回復** — `kill` でプロセス強制終了後、次のリクエストで自動再接続・正常動作復帰。

---

## 2026-05-26（セッション12）

### MCP クライアント実装 Phase 1（Playwright MCP）

#### 新規ファイル

- `tools/mcp_client.py` — MCPClientManager（AsyncExitStack で接続管理・ツール取得・OpenAI スキーマ変換・呼び出し）
- `config/mcp_servers.json` — MCP サーバー設定ファイル（Playwright enabled / Obsidian disabled）

#### 変更ファイル

- `server.py` — lifespan に MCP 起動/停止・TOOL_REGISTRY/TOOLS への動的登録を統合。`execute_tool_async` に `__` を含むツール名を async で直接 await する分岐を追加
- `requirements.txt` — `mcp>=1.0.0` 追加
- `setup.sh` — Node.js 22.x チェック・インストール・`npx @playwright/mcp install-browser chromium` 追加

#### 実装内容

**`MCPClientManager`**
- `config/mcp_servers.json` 読み込み・環境変数展開
- `AsyncExitStack` で `stdio_client` / `ClientSession` を管理（anyio cancel scope と干渉しないよう shutdown は `BaseException` 抑制）
- MCP ツール定義を `{server_id}__{tool_name}` 形式で OpenAI スキーマに変換
- `execute_tool_async` から async callable として呼び出せる統一インターフェース

**環境整備**
- Node.js 22.x (nodesource) をインストール
- mcp 1.27.1 を venv にインストール
- `npx @playwright/mcp install-browser chromium` で Chromium Headless Shell をインストール

**動作確認**
- 起動時に Playwright MCP へ接続・23 ツールを TOOL_REGISTRY に登録（約 1〜2 秒）
- `playwright__browser_navigate` / `playwright__browser_take_screenshot` 等が LLM から呼び出し可能であることを確認
- シャットダウン時エラーなし

**Phase 2 以降（未着手）**
- Obsidian MCP (`config/mcp_servers.json` の enabled を true にするだけ)
- /setup UI（MCP サーバー一覧・有効/無効トグル）

---

## 2026-05-26（セッション11）

### WinRM・インフラ情報収集・ホスト管理機能

#### 新規ファイル

- `tools/winrm_tools.py` — pywinrm を使った Windows リモート実行ツール
- `tools/host_info_tools.py` — Linux/Windows ホスト情報一括収集ツール
- `skills/infra/SKILL.md` — `/infra` スキル（インフラ管理ツール一覧表示）

#### 実装内容

**`winrm_command` ツール**
- TrustedHosts 設定不要で IP 直指定・ドメイン未参加環境でも動作
- `session.run_ps()` でコマンドを Base64 エンコード送信（パイプ・スクリプトブロック対応）
- NTLM/Kerberos/Basic/CredSSP 認証・HTTP(5985)/HTTPS(5986) 対応
- WSL版・Windows版どちらでも動作（pywinrm は純粋 Python 実装）

**`gather_host_info` ツール**
- `os_type="auto"` でポートスキャン（5985→Windows / 22→Linux）によるOS自動判定
- Linux: OS・カーネル・CPU・メモリ・ディスク・NIC・DNS・ルーティング・パッケージ・サービス・ユーザー・cron・オープンポートを一括収集（SSH鍵認証）
- Windows: OS・CPU・メモリ・ディスク・NIC・DNS・GW・インストール済みソフト・実行中サービス・スケジュールタスク・ローカルユーザー・Windows Update履歴を一括収集（WinRM）
- workspace 相対パスで鍵ファイルを自動検索

**`requirements.txt`**
- `pywinrm>=0.4.3` 追加（setup.sh / setup.bat 両方で自動インストール）

**`prompts.py`**
- `winrm_command` の使い方・禁止事項を明記
- `gather_host_info` を設計書・仕様書作成前の必須ツールとして明記
- リモート失敗時にローカル結果で誤魔化すことを明示禁止
- インストール済みソフトはレジストリ取得（winget 禁止）を明記

#### 設計上の注意

- `run_powershell` はローカル Windows 操作専用。リモート接続には `winrm_command` を使う
- Linux はSSH鍵認証のみ対応（パスワード認証は run_command 経由では不可）
- 設計書・仕様書作成前は必ず `gather_host_info` で一括収集すること（個別コマンドは抜け漏れリスクあり）

---

## 2026-05-25（セッション10）

### MD→DOCX変換機能・ファイルツリー更新ボタン（index.html / server.py）

#### 実装内容

**MD→DOCX変換（Word COM経由）**
- `/convert-to-docx` POST エンドポイント追加
- MD → HTML 変換（markdown-it-py）
- Obsidian パイプ記法 `![alt|80%](path)` → `<img width=N height=M>` 前処理
- Pillow で画像実寸取得・アスペクト比維持・A4ページ幅（540px）に収まるよう計算
- `wslpath -w` で WSL→Windows UNC パス変換
- PowerShell + Word COM で DOCX 保存（タイムアウト60秒）
- 一時HTMLは変換後自動削除

**ダウンロードエンドポイント**
- `/workspace/download` GET エンドポイント追加
- RFC 5987形式で日本語ファイル名対応（`filename*=UTF-8''...`）
- `ALLOWED_WORK_DIR` 基準のパス解決（`_resolve_safe_path` の日本語パス問題を回避）

**UI**
- エディタファイルツリーの `.md` ファイル右クリックメニューに「📄 Word変換（DOCX）」追加
- 変換中は右下トーストで「⏳ Word起動中・変換中...」表示
- 完了後は「✅ 変換完了: ファイル名」表示 + 自動ダウンロード
- 一括清書モーダル完了後にも「📄 Word変換（DOCX）に変換」ボタンを表示
- エディタファイルツリー上部に「WORKSPACE ↻」更新ボタン追加

#### 設計上の制約・課題

- MD→HTML→Word の2段変換なのでレイアウトが崩れることがある
- **次の方向性として「最初からDOCX形式でAIに書かせる」方針が浮上**（#57後継として検討中）
  - サーバーサイド Mermaid→PNG変換（mmdc / Mermaid CLI）が必要
  - `write_docx` ツール（python-docx）で AI が直接 DOCX 生成
  - プロンプト更新で AI がフロー全体を理解

#### Windows版の動作
- Pillow・python-docx・markdown-it-py はすべて requirements.txt 済み → 動作可
- `wslpath` は Windows ネイティブに存在しないが except で捕捉・フォールバック動作
- パイプ記法画像は正常動作、通常画像は CSS max-width のみ（クラッシュなし）

#### クローズしたIssue
- **#57** MD→DOCX変換ツール → 暫定実装完了（上記制約あり・DOCX直接生成に移行予定）

---

## 2026-05-23（セッション9）

### Obsidian WSL統合・環境整備（コード変更なし）

#### 環境整備

**Zone.Identifier ファイル削除**
- workspace 内に大量発生していた Windows セキュリティメタデータファイルを一括削除（約200個）
- ワークスペースを Windows側に移したことで発生、WSLに戻したため再発しない

**WSL版 Obsidian インストール（WSLg使用）**
- Windows版 Obsidian は WSL ファイルシステムを直接開けない（EISDIRエラー・既知の未対応問題）
- WSLg（Windows 11内蔵）経由で WSL内に Obsidian をインストール → Windowsのスタートメニューから起動可能
- 依存パッケージ: `obsidian_1.12.7_amd64.deb` / `fonts-noto-cjk` / `wslu`
- `.bashrc` エイリアス追加（GPU エラー抑制）・`xdg-open` 差し替え（ファイルエクスプローラー誤起動防止）
- 手順書: `docs/obsidian-setup.md`

**Obsidian Sync 設定**
- workspace 全体を Obsidian Sync で同期（ドットファイルは自動除外）
- Windows版・WSL版・iPhone で同一 Vault を共有
- 個人メモとワークスペースを一つの Vault に統合

#### クローズしたIssue
- **#56** Deep Research実行前に確認ダイアログ → クローズ（実装済み）

#### 検討中（未決定）
- workspace のディレクトリ構造整理（notes/ / jobs/ / images/ 分離・Obsidian同期範囲の最適化）→ GitLab #45 に設計メモ追記

---

## 2026-05-22（セッション8）

### Mermaid清書・エディタ機能強化・Obsidian連携（index.html / server.py / setup.html）

#### 新規実装

**エディタ用紙ガイド（index.html）**
- ツールバーに用紙サイズセレクター（ガイドなし / A4縦・横 / A3縦・横）を追加
- 「余白 Nmm」入力欄を追加 → PDFツールの余白に合わせてリアルタイム調整可能
- ページ区切り横線・コンテンツ幅縦線を CSS `background-image` オーバーレイで描画（コンテンツに干渉しない）
- ガイド選択時はコンテンツ幅を `width: Xpx` 固定 → ブラウザ幅変更・ガイド切替で画像サイズが変わらない
- プレビューコンテンツを `<div id="editor-preview-content">` でラップ
- `p { margin: 0.7em 0 }` / `br { display: block; margin-bottom: 0.4em }` で行間調整

**Obsidian互換画像リサイズ（index.html）**
- `applyImgResize()` の保存形式を `<img width="80%">` → `![alt|80%](path)` パイプ記法に変更
- `renderMarkdown()` に前処理を追加: `![alt|80%](path)` を `<img style="width:80%">` に変換してからmarked.jsに渡す
- `showImgResizePanel()` がパイプ記法・旧HTML形式の両方からサイズ/配置を読み取るよう更新
- これにより Obsidian でドラッグリサイズが効くようになった（左寄せ画像のみ；中央揃えはHTML形式のまま）

**セットアップ画面フォルダブラウザ（setup.html / server.py）**
- 「作業ディレクトリ」フィールドに「📁 参照」ボタンを追加
- クリックで階層型フォルダブラウザモーダルを表示（Windowsドライブ一覧 → サブフォルダをたどれる）
- Windowsパス（C:\...）入力時に WSL パス（/mnt/c/...）をリアルタイムプレビュー
- `server.py` に `/setup/browse-dir` エンドポイントを追加（`os.listdir` + `os.path.isdir` でDrvFs対応）
- Windowsパスを作業ディレクトリに設定 → Obsidianのvaultとして開けるようになる

#### デバッグコード削除
- `server.py`: `show_mermaid_batch_refine_dialog` SSEハンドラーの `print()` 3箇所を削除
- `index.html`: `mermaid_batch_refine` SSEハンドラーの `try/catch alert` を削除

#### GitLab Issue
- **#57** 新規作成: MD→DOCX変換: Obsidianパイプ記法とpandocの相性問題（将来のDOCX変換ツール実装時に前処理で対応する方針）

#### 運用メモ（今日判明）
- Obsidianはstandardマークダウン画像 `![](path)` のドラッグリサイズをサポートするが、HTML `<img>` タグは非対応
- `/mnt/c`（WindowsドライブのWSLマウント）は `Path.is_dir()` が False を返す場合がある → `os.path.isdir()` で回避
- MDは改行位置・ページレイアウトを精密にコントロールするための形式ではない（PDFツール依存）

---

## 2026-05-22（セッション7）

### Deep Research 残課題解消・品質改善（server.py / tools/web_tools.py / index.html）

#### 新規実装

**`tools/web_tools.py`**
- **URLフラグメント削除**: レポート内の `#:~:text=...` を `re.sub` で除去（表示・保存がきれいになる）
- **レポート自動保存**: Deep Research 完了時に `workspace/YYYYMMDD_クエリ名レポート.md` へ自動保存（ファイル名のスペースはアンダースコアに変換）
- **タイムアウト延長**: API `timeout` を 600秒 → 3600秒（OpenAI公式推奨値）

**`server.py`**
- **`/providers/current` に `web_research_provider` 追加**: フロントエンドで Deep Research 設定状態を判別できるよう
- **tool note 強化**: `saved_filename` を回答に含めること・要約は端折らず重要ポイントを網羅することをAIに指示
- **タイムアウト延長**: `asyncio.wait_for` のタイムアウトを 750秒 → 3600秒
- **タイムアウト時の再試行禁止**: Deep Research タイムアウト時のエラーメッセージに「再試行・別クエリでの再実行は絶対にしないこと」を追記

**`index.html`**
- **Deep Research 確認ダイアログ実装（#56）**: Deep Research プロバイダー設定中に送信すると「本当に実行しますか？（有料）」確認ダイアログを表示。キャンセルで中断可能
- **`_webResearchProvider` 変数追加**: 起動時・プロバイダー切り替え時に `web_research_provider` を取得して保持

#### 動作確認済み
- 確認ダイアログ表示 ✅
- 自動保存・ファイル名を回答に含める ✅
- レポート全文UI表示 ✅

#### 運用メモ（今日判明）
- Deep Research の実行時間は混雑時間帯で 900〜1200秒超かかることがある（1200秒でもタイムアウトした）
- タイムアウトしても OpenAI 側で処理が開始されていれば課金される
- 3600秒（1時間）あればほぼ完走できる見込み
- ブラウザ更新前に送信すると確認ダイアログが出ずに Deep Research が実行されてしまう（注意）

---

## 2026-05-21（セッション6）

### Deep Research 集中修正（server.py / tools/web_tools.py / index.html）

今日は Deep Research (OpenAI o4-mini-deep-research) が全く使えない状態だったため、集中的に修正。6回以上のテストが無駄になった反省から、表示・保存・接続維持・課金防止の全面対応を実施。

#### バグ修正

**`server.py`**
- **`_warning` 誤発動修正**: Deep Research の返り値は `sources:[]` が正常仕様のため、`report` フィールドがあれば「成功」と判定するよう変更（以前は空sourceを「失敗」と誤判定してAIがレポートを無視していた）
- **ツール説明の動的変更**: `WEB_RESEARCH_PROVIDER=deep-research-*` 時に `web_research` の説明を「必ずこのツールを使え」に、`web_search` を「Deep Research設定中は代わりにweb_researchを使え」に変更
- **二重呼び出し防止**: Deep Research 設定時に同一ターン内で `web_research` が複数呼ばれた場合、2件目以降をサーバー側でブロック（二重課金防止）
- **SSEキープアライブ追加**: 長時間ツール実行中に30秒ごと `": keepalive\n\n"` を送信してブラウザのSSE接続切れを防止（`asyncio.wait` でタスク監視しながら yield）
- **タイムアウト延長**: 600秒 → 750秒（リトライ60秒分の余裕を確保）
- **Deep Research レポートのUI直接表示**: `web_research` 結果に `report` フィールドがある場合に `deep_research_report` SSEイベントを送出（AIの要約を回避）
- **AIへの全文渡し修正**: tool_result_for_msg に `report` フィールドを含める（以前は `report_displayed:true` のみでAIが保存操作できなかった）
- **GitLab イシュー #56 作成**: Deep Research実行前確認ダイアログ（有料なのでワンクッション必要）

**`tools/web_tools.py`**
- **429自動リトライ**: `RateLimitError` 発生時に60秒待って1回自動リトライ

**`index.html`**
- **`deep_research_report` イベントハンドラ追加**: SSEで受け取ったレポートを青いボックス（マークダウンレンダリング・最大600px・スクロール対応）でチャットに直接表示
- **セッション復元時の再表示**: `_restoreToolBlocks` で `report_displayed:true` かつ `report` フィールドがあれば、ページリロード後も青いボックスで再レンダリング

#### 残課題（次回セッション）

- **「マークダウンに保存して」だと要約が保存される**: 「調査結果の全文を保存して」と言わないと全文が入らない → prompts.py または tool note を強化して「保存 = report全文」をデフォルト化
- **Deep Research 動作確認**: 昼間（15〜17時JST）に再テストして全修正が正常に機能するか確認
- **#56 確認ダイアログ実装**: 有料呼び出し前のワンクッション

#### 今日わかったこと（運用メモ）

- Deep Research 1回 ≒ 175,000 TPM消費（上限200,000/分）→ 連続2回は即429
- 日本時間21時台 = 米国東海岸8〜9時 → OpenAI混雑時間帯（タイムアウトリスク高）
- タイムアウトした場合の課金はOpenAI側の処理完了状況次第（不明）
- 「マークダウンに保存して」だけでは要約が保存される → 「全文を保存して」と明示が必要（次回修正予定）

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
