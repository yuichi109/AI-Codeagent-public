# AI Code Agent — ロードマップ（未完了タスク）

> 完了済みタスクは docs/changelog.md を参照。
> GitLab イシューと連動管理（完了時は両方更新）。

---

## 優先タスク（優先度順）

1. **WinRT GraphicsCapture API**（★★）
   - ウィンドウ指定スクショツール（`tools/windows_tools.py` に追加）

2. **#19 並列ツール実行**（★★★）
   - 現在は逐次実行のみ。`asyncio.gather` 等で複数ツールを同時実行して高速化。

3. **#20 インタラクティブプロセス管理**（★★★）
   - バックグラウンド実行・stdin 送信・長時間プロセスの制御。
   - `run_background` / `send_input` ツールの追加が必要。

4. **#27 LLMプロバイダー切り替え Phase 2（ハイブリッドモード）**（★★★）
   - Qwen3.5-4B をオーケストレーター（日本語会話・判断担当）として動作
   - `delegate_to_azure` ツールを追加 — コード・ツール作業はgpt-4.1に委譲
   - 推奨サーバー: Ollama（LM StudioはQwen3.5のテンプレート不一致問題あり）

5. **PDF変換ツール `write_pdf`**（★）
   - reportlab or weasyprint で実装。ユースケース確認後着手。

6. **各ツールの単体テスト（pytest）**（★★）→ GitLab #6

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

## 低優先度・アイデア枠

- **MAGIシステム**（★低）: `/magi` スキル。3つのLLM（GPT-4.1/Gemini/現モデル）が可決・否決で議決する形式。
  `multi_consult` ツール + スキルで実装予定。ユースケースが固まったタイミングで着手。

---

## 品質・テスト

- [ ] 各ツールの単体テスト（pytest）を書く
- [ ] bubblewrap サンドボックスの脱出テスト
- [ ] 長いプロンプトでのトークン上限テスト
- [ ] 別 PC（社内プロキシあり）での動作確認
