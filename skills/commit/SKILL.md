---
name: commit
description: ステージ済みの変更にコミットメッセージを付けてコミットする
trigger: /commit
---

## スキル: /commit

ユーザーが `/commit` と入力したとき、または「コミットして」と依頼したとき：

1. `run_command("git status")` で変更内容を確認（work_dir はプロジェクトのサブディレクトリを指定）
2. `run_command("git diff --staged")` でステージ済み内容を確認
   - ステージ済みがなければ `git diff` で未ステージ内容も確認
3. 変更内容から適切なコミットメッセージを日本語で生成（prefix: feat/fix/docs/refactor/chore）
4. `run_command("git add -A")` → `run_command("git commit -m '...'")` を実行
5. 完了後にコミットメッセージと変更ファイル数を報告する

**注意:** push はユーザーから明示的に求められた場合のみ実行する。
