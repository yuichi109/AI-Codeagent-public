# AI Code Agent — ロードマップ（未完了タスク）

> 完了済みタスクは docs/changelog.md を参照。
> GitLab イシューと連動管理（完了時は両方更新）。

---

## 優先タスク（優先度順）

1. **WinRT GraphicsCapture API**（★★）
   - ウィンドウ指定スクショツール（`tools/windows_tools.py` に追加）

---

## 中優先度

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

- **BG（非同期エージェント）でもマルチAIに対応**（★★・将来課題）
  - 現状: BG ジョブ（`/async-agent/jobs`）はマルチAI状態を送らず、常に単一エージェント（`agent_core.py`）で実行。マルチAIが効くのは通常チャット送信時のみ。
  - 動機: 重く時間のかかるタスクほど裏で回したい＝マルチAI × BG は相性が良い。加えて **BG は UI 上で「今なにをしているか」が見えにくい**ため、複数AIの役割分担・進捗をBGペインで可視化できると使い勝手が上がる。
  - 必要作業: フロント（`bgSubmitJob`）で `multi_agent` を送る配線 ＋ `agent_core` 側でマルチAIエンジンを呼ぶ配線。無人実行のため**計画承認フローの扱い（auto相当で止まらない設計）**とコスト可視化が論点。
  - 着手タイミング: **マルチAIが通常チャットで安定し main にマージできた後**（現在 `feature/multi-agent-team` で実機調整中）。

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
