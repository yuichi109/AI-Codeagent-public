# AI Code Agent — ロードマップ（未完了タスク）

> 完了済みタスクは docs/changelog.md を参照。
> GitLab イシューと連動管理（完了時は両方更新）。

---

## 優先タスク（優先度順）

1. **WinRT GraphicsCapture API**（★★）
   - ウィンドウ指定スクショツール（`tools/windows_tools.py` に追加）

2. **#20 インタラクティブプロセス管理（残り: `send_input`）**（★★）
   - `run_background` / `check_background` / `kill_background` は実装済み。
   - 未実装: stdin 送信（`send_input` ツール）— 対話型プロセスへのキー入力送信。

---

## 中優先度

- **#22 スラッシュコマンド拡充**: `/clear` `/compact` `/help` 以外のショートカット追加
- **#23 CLAUDE.md 自動読み込み**: workspace 内の `CLAUDE.md` を自動検出してシステムプロンプトに注入
- **LINE Bot連携**（★）: スマホの LINE からエージェントを遠隔操作
  - LINE Messaging API（webhook）→ `server.py` に転送 → 返答をLINEに送信
  - `/setup` 画面にLINE Bot設定セクション追加

---

## 配布・インフラ

- **Ansibleプレイブック**（`setup.yml`）: 未着手（setup.sh で代替中）
- **Docker化オプション**（WSL2なし環境向け）: bubblewrapをオプション化し `SANDBOX=none` で無効化可能に
- **`docs/setup.md` 移行チェックリスト**: bubblewrap の追記
- **`docs/design.md` 更新**: 現在の実装に合わせて更新

---

## 進行中の大型機能

- **MCP クライアント実装**（★★★★）　**作業ブランチ: `feature/mcp-client`**
  - 公式 `mcp` Python ライブラリを使用
  - Phase 1: Playwright MCP（ブラウザ操作）
  - Phase 2: Obsidian MCP
  - 動的ツール登録・プロセス管理・OpenAI スキーマ変換が主な実装対象
  - 完了後は main にマージしてこの項目を changelog に移動する

---

## 次期大型機能（現行改良完了後に着手）

- **ドキュメント駆動型マルチエージェント**（★★★★）
  - ディスパッチャーがタスクを分解し、設計・コーディング・ドキュメント・デバッグ等の役割エージェントに割り当て
  - エージェント同士はファイルで通信（チャットではなく文章・コードを残す）
  - 役割ごとに異なるモデルを割り当て可能（GPT-4.1 / gpt-4.1-mini / Foundry複数インスタンス等）
  - 品質閾値・ループ上限・納期を設定し、未完了でも中間報告を生成
  - 詳細: [docs/multi-agent-dispatch-design.md](multi-agent-dispatch-design.md)

---

## 低優先度・アイデア枠

- **MAGIシステム**（★低）: `/magi` スキル。3つのLLM（GPT-4.1/Gemini/現モデル）が可決・否決で議決する形式。
  `multi_consult` ツール + スキルで実装予定。ユースケースが固まったタイミングで着手。

---

## 品質・テスト

- [x] 各ツールの単体テスト（pytest）を書く（test_code/command/file/web_tools.py 実装済み）
- [ ] bubblewrap サンドボックスの脱出テスト
- [ ] 長いプロンプトでのトークン上限テスト
- [ ] 別 PC（社内プロキシあり）での動作確認
