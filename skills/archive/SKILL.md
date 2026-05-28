---
name: archive
description: 現在の作業ディレクトリを Obsidian vault の archives フォルダにコピーして蓄積する
trigger: /archive
---

## スキル: /archive

ユーザーが `/archive`、「アーカイブして」と入力したとき：

`archive_workspace` ツールを呼ぶ。`scope` には現在の作業ディレクトリ名を渡す。

それだけ。他に何もしない。

## 設定

アーカイブ先は `.env` の `OBSIDIAN_VAULT_PATH` で決まる。
`/setup` → 「Obsidian 連携」セクションから変更可能。

コピー先: `{OBSIDIAN_VAULT_PATH}/archives/{hostname}_wsl|win/{scope}/`
