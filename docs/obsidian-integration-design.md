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

### フォルダ構成

```
{vault}/AI-Codeagent/
  inbox/
    {hostname}_wsl/    ← 各PCのAIが自分のサブフォルダだけ監視
    {hostname}_win/
  processing/
    {hostname}_wsl/    ← 受理後に移動（作業中の証拠・二重実行防止）
  done/
    {hostname}_wsl/    ← 完了後に移動（リクエスト保存）
  results/
    {hostname}_wsl/    ← 成果物の書き出し先（どのPCが実行したか一目でわかる）
      {date}/{job-id}/
```

- ホスト名 + `_wsl` / `_win` サフィックスで同一物理マシンの WSL/Windows 版も区別
- PC-A の Obsidian から `inbox/pc-b_win/` にファイルを置いて PC-B に指示することも可能
- `results/` もホストごとに分かれるため、どのPCで実行されたか Obsidian から確認できる

### フロー
```
inbox/{hostname}_wsl/request.md     ← ユーザーが置く（他PCからも可）
  ↓ AI が検出・受理（ポーリング or 手動スキャン）
processing/{hostname}_wsl/request.md  ← 移動（二重実行防止）
  ↓ 処理完了
done/{hostname}_wsl/{timestamp}-request.md  ← 移動
results/{hostname}_wsl/{date}/{job-id}/     ← 成果物を書き出し
```

`processing/{hostname}_wsl/` にファイルがある = そのPCのAIが現在作業中。

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
- ポーリング間隔は `OBSIDIAN_INBOX_POLL_SEC`（デフォルト 900秒 = 15分）
- 手動スキャン手段を2つ用意：`/inbox-scan` スキル + `/setup` の「今すぐスキャン」ボタン
- 受理時に `processing/` へ移動（二重実行防止）
- 完了後に `done/{timestamp}-request.md` へ移動・成果物を `results/` に書き出し
- 難易度: ★★★★

---

## 設定項目（.env）

```env
OBSIDIAN_VAULT_PATH=C:\Users\yuichi.matsuo\Documents\Obsidian\MyVault
OBSIDIAN_INBOX_ENABLED=false   # 機能B: inbox 監視
OBSIDIAN_INBOX_POLL_SEC=900    # ポーリング間隔（秒）デフォルト15分
```
