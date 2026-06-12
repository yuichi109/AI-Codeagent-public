# AI Code Agent — ロードマップ（未完了タスク）

> 完了済みタスクは docs/changelog.md を参照。
> GitLab イシューと連動管理（完了時は両方更新）。

---

## 優先タスク（優先度順）

1. **WinRT GraphicsCapture API**（★★）
   - ウィンドウ指定スクショツール（`tools/windows_tools.py` に追加）

---

## 中優先度

- **スマホ対応（レスポンシブUI）**（★★）: [イシュー #62](https://gitlab.com/yuichi.matsuo/AI-Codeagent/-/work_items/62)
  - Tailscale 経由でスマホから Web UI にアクセスできるようになったが、UIがPC向けで使いにくい。
  - 同じ `index.html` をレスポンシブ化（専用ページは作らない）。文字拡大・サイドペインのドロワー化・ヘッダー折りたたみ・タップ領域拡大。viewport メタタグ確認。任意でPWA化。
- **Tailscale 連携の自動化**（★）: スマホ等から Web UI にアクセスするための導線。
  - 手順は確立済み（→ **[docs/tailscale-setup.md](tailscale-setup.md)**）。HTTPS は `tailscale serve`（`*.ts.net` 正規証明書）で取得。tailnet内限定（Funnel不使用）。
  - 未実装: `setup.sh tailscale` サブコマンドでの導入自動化、`/setup` 画面に接続ステータス/アクセスURL/QR表示（クロスプラットフォーム対応）。

---

## 配布・インフラ

- **Ansibleプレイブック**（`setup.yml`）: 未着手（setup.sh で代替中）
- **Docker化オプション**（WSL2なし環境向け）: bubblewrapをオプション化し `SANDBOX=none` で無効化可能に
- **`docs/setup.md` 移行チェックリスト**: bubblewrap の追記
- **`docs/design.md` 更新**: 現在の実装に合わせて更新

---


## 次期大型機能（現行改良完了後に着手）

- **マルチエージェント Phase 2・3**（★★★★）
  - Phase 1（逐次実行・計画確認フロー・クロスプロバイダー）は実装済み
  - Phase 2: 並列実行（asyncio.gather・UI複数ストリーム表示）
  - Phase 3: ループ・品質制御（閾値・ループ上限・納期・中間報告）
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
