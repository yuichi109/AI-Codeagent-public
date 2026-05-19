# Obsidian 統合設計メモ

> 作成: 2026-05-19  
> ステータス: 未着手  
> 関連: マルチエージェント機能（feature/multi-agent）

---

## 機能A: マルチエージェント成果物 → Obsidian 自動コピー

ジョブ完了後に Obsidian ボルトへ成果物を書き出す。

### フォルダ構成
```
{obsidian_vault}/AI-Codeagent/{YYYY-MM-DD}/{job-id}/
  design.md
  final-report.md
  code/
    fibonacci.py
    how-to-use.md
  test-result.md
  ...
```

### 実装方針
- セットアップ画面に `OBSIDIAN_VAULT_PATH` 設定欄を追加（Windows パス可・WSL で /mnt/c/... に変換）
- `multi_agent_stream()` の最後に `cp -r {job_dir}/* {vault}/AI-Codeagent/{date}/{job-id}/` を実行
- 難易度: ★★

---

## 機能B: Obsidian inbox → AI 自律実行 → 書き戻し

ユーザーがボルト内の inbox フォルダにノートを書くと AI が検出・実行・結果を書き戻す。

### フロー
```
{vault}/AI-Codeagent/inbox/request.md  ← ユーザーが書く
  ↓ AI が検出（ポーリング）
マルチエージェント or 通常エージェントで処理
  ↓
{vault}/AI-Codeagent/{date}/{job-id}/  ← 結果を書き戻す
inbox/request.md を処理済みフォルダへ移動
```

### 技術的な課題
- WSL から Windows NTFS を `inotify` で監視すると NTFS の仕様でイベントが発火しないケースがある
- **ポーリング**（数秒間隔でフォルダを確認）で代替。若干のラグあり
- `watchdog` ライブラリ or 自前のポーリングループ

### inbox ノートのフォーマット案
```markdown
---
mode: multi-agent   # または single
agent_mode: balance
---

Pythonでフィボナッチ数列を返す関数を作って
```

### 実装方針
- `server.py` の lifespan に inbox 監視タスクを追加（`asyncio` ループ）
- 処理中は `request.md` を `request.processing.md` にリネームして二重実行防止
- 完了後は `inbox/done/{timestamp}-request.md` へ移動
- 難易度: ★★★★

---

## 設定項目（.env）

```env
OBSIDIAN_VAULT_PATH=C:\Users\yuichi.matsuo\Documents\Obsidian\MyVault
OBSIDIAN_INBOX_ENABLED=false   # 機能B: inbox 監視
OBSIDIAN_INBOX_POLL_SEC=5      # ポーリング間隔（秒）
```
