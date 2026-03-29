---
name: ansible
description: Ansible プレイブックを選択して実行する。workspace/.azure_creds からクレデンシャルを自動ロードする。
trigger: /ansible
---

## スキル: /ansible

ユーザーが `/ansible` と入力したとき：

1. `list_ansible_playbooks` を呼ぶ（引数なし）
   - workspace 以下の .yml を再帰検索してUIにチェックボックスを表示する
   - このツールを呼んだら**必ずターンを終了**し、ユーザーの選択を待つ
   - 「プレイブックを選択して実行ボタンを押してください」と一言添えて終わる

2. ユーザーが「以下のプレイブックを実行してください: - xxx.yml」と送ってきたら：
   - `run_ansible_playbook(playbook="xxx.yml")` を呼ぶ
   - クレデンシャルは workspace/.azure_creds から自動ロードされる
   - 複数選択された場合は1つずつ順番に実行する
   - 実行後に stdout/stderr と returncode を報告する

**注意:**
- `run_command("ansible-playbook ...")` は使わない（クレデンシャルが自動ロードされない）
- `run_ansible_playbook` は必ず playbook に workspace 相対パスを指定する（例: `myproject/site.yml`）
- workspace/.azure_creds が存在しない場合はその旨を伝えてテンプレートの場所を案内する
