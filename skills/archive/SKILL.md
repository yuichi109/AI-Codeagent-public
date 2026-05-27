---
name: archive
description: 現在の作業ディレクトリを Obsidian vault の archives フォルダにコピーして蓄積する
trigger: /archive
---

## スキル: /archive

ユーザーが `/archive`、「アーカイブして」と入力したとき：

### 前提確認

1. `GET /workspace/archive-info` を fetch して以下を取得する
   - `vault_path`: Obsidian vault のパス
   - `archives_base`: アーカイブ先ベースパス（`{vault}/archives/{hostname}_wsl/` など）
   - `scope`: 現在の作業ディレクトリ名（例: `HOGE`）
   - `src_path`: コピー元フルパス
   - `dst_path`: コピー先フルパス

2. `vault_path` が空の場合は「OBSIDIAN_VAULT_PATH が設定されていません。/setup で設定してください。」と伝えて終了。

3. `scope` が空（作業ディレクトリ未選択）の場合は「作業ディレクトリが選択されていません。スコープを選択してから実行してください。」と伝えて終了。

### 実行

4. `run_command` で以下を実行：
   ```bash
   mkdir -p "{dst_path}" && cp -r -u "{src_path}/." "{dst_path}/"
   ```
   - `-u` オプションで「コピー先より新しいファイルのみ上書き」= 削除は反映しない

5. `.archived` マーカーファイルを作業ディレクトリ内に書き込む：
   - パス: `{scope}/.archived`（workspace 相対パス）
   - 内容: 日時・コピー先パスを記録（例）:
     ```
     archived_at: 2026-05-27T15:30:00
     destination: /home/user/Obsidian-Vault/AI-Agent/archives/MATSUO-WORK_wsl/HOGE
     ```
   - `write_file` ツールを使用

### 完了報告

6. 「✅ アーカイブ完了: `{scope}` → `{dst_path}`」と報告する。

**注意:** ユーザーへの確認は不要。即座に実行する。
