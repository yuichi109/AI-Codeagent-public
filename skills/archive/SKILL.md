---
name: archive
description: 現在の作業ディレクトリを Obsidian vault の archives フォルダにコピーして蓄積する
trigger: /archive
---

## スキル: /archive

ユーザーが `/archive`、「アーカイブして」と入力したとき、**必ず以下の手順を順番通りに実行すること。自己判断でパスを決めたり rsync を使ってはいけない。**

### ステップ1: API でアーカイブ情報を取得する（必須）

**必ず最初に** `run_command` で以下を実行してレスポンスを確認する：

```
curl -s "http://localhost:8000/workspace/archive-info?scope=現在のスコープ名"
```

現在のスコープ名はシステムプロンプトの「作業ディレクトリ」欄を参照する。

レスポンス例：
```json
{
  "vault_path": "/home/user/Obsidian-Vault/AI-Agent",
  "archives_base": "/home/user/Obsidian-Vault/AI-Agent/archives/MATSUO-WORK_wsl",
  "scope": "HOGE",
  "src_path": "/home/user/AI-Codeagent/workspace/HOGE",
  "dst_path": "/home/user/Obsidian-Vault/AI-Agent/archives/MATSUO-WORK_wsl/HOGE"
}
```

### ステップ2: 前提チェック

- `vault_path` が空 → 「OBSIDIAN_VAULT_PATH が設定されていません。/setup で設定してください。」と伝えて終了
- `scope` が空 → 「作業ディレクトリが選択されていません。スコープを選択してから実行してください。」と伝えて終了

### ステップ3: アーカイブ実行

取得した `dst_path` と `src_path` を使い、`run_command` で実行する：

```bash
mkdir -p "dst_path の値" && cp -r -u "src_path の値/." "dst_path の値/"
```

**注意:**
- コピー先は必ず API から取得した `dst_path` を使う。自分でパスを作ってはいけない
- `rsync` は使わない。`cp -r -u` を使う
- workspace 内のパスにコピーしてはいけない

### ステップ4: .archived マーカーファイルを書き込む

`write_file` ツールで以下を保存する：
- パス: `{scope}/.archived`（workspace 相対パス）
- 内容:
```
archived_at: {現在のISO日時}
destination: {dst_path の値}
```

### ステップ5: 完了報告

「✅ アーカイブ完了: `{scope}` → `{dst_path}`」と報告する。
