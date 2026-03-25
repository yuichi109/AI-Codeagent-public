---
name: get-proj
description: まとめリポジトリから特定ディレクトリを取り出して独立したプロジェクト/ディレクトリにする
trigger: /get-proj
---

## スキル: /get-proj

ユーザーが `/get-proj` と入力したとき、以下の手順で実行する。

### ステップ1: GitLabプロジェクト一覧を取得して選ばせる

`run_command("curl -s http://localhost:8000/gitlab/projects")` を実行し、
以下の形式で**番号付きリスト**で表示する。**表形式・箇条書きは使わない。**

```
GitLabプロジェクト一覧:
1. yuichi.matsuo/HOGE
2. yuichi.matsuo/bk-yuichi.matsuo
3. ...
番号を入力してください:
```

ユーザーが番号を入力したら、対応する `id`・`path_with_namespace`・`http_url_to_repo` を使って次のステップへ進む。

### ステップ2: 選択リポジトリのディレクトリ一覧を取得して選ばせる

```bash
curl -s "https://gitlab.com/api/v4/projects/<id>/repository/tree?per_page=100&page=1" \
  -H "PRIVATE-TOKEN: <GITLAB_PAT>"
```

レスポンスの各要素のうち **`"type": "tree"` のものだけ**（ファイルは除外）、以下の形式で**番号付きリスト**で表示する。**表形式・箇条書きは使わない。**

```
ディレクトリ一覧:
1. project-alpha
2. project-beta
3. ansible-roles
番号を入力してください:
```

100件を超える場合は `page=2`, `page=3` ... と繰り返して全件取得してから表示する。
ユーザーが番号を入力したら、**必ず選択されたディレクトリ名を復唱して確認する**：
「`<ディレクトリ名>` を選択しました。このディレクトリを取り出します。」
その後、次のステップへ進む。

### ステップ3: 取り出し先を確認する

「取り出し先ディレクトリ名を教えてください（省略時: <選択ディレクトリ名>）」

**⚠️ 取り出し先の安全確認（必須）**
- 取り出し先は必ず **workspace 直下の新規ディレクトリ** にする
- `list_files()` で workspace の現在のディレクトリ一覧を確認し、同名が既存の場合は警告して確認する
- ユーザーが明示的に「はい」と答えない限り実行しない
- 既存プロジェクトの中（例: TEST1/ の中）には絶対に入れない

### ステップ4: クローンしてコピーする（**この手順のみ使う**）

**`work_dir` は絶対に使わない。以下の3コマンドを順番に実行する。失敗したら別の方法を試みず、エラー内容をそのまま報告する。**

まずホームディレクトリを取得する：
```bash
echo $HOME
```
得られたパスを `<HOME>` として以下に使う（例: `/home/user`）。

```bash
# 1. クローン（work_dir 指定なし）
git clone --depth 1 "https://oauth2:<GITLAB_PAT>@<リポジトリURL(https://以降)>" <HOME>/AI-Codeagent/workspace/_gp_tmp

# 2. コピー（work_dir 指定なし）
cp -r <HOME>/AI-Codeagent/workspace/_gp_tmp/<ディレクトリ名> <HOME>/AI-Codeagent/workspace/<取り出し先>

# 3. 一時ディレクトリ削除（work_dir 指定なし）
rm -rf <HOME>/AI-Codeagent/workspace/_gp_tmp
```

- `<リポジトリURL(https://以降)>` は `http_url_to_repo` の `https://` を除いた部分（例: `gitlab.com/yuichi.matsuo/HOGE.git`）
- **いずれかのコマンドが失敗したらその場で止めてエラーを報告する。リトライや別方法への切り替えは禁止**

### ステップ5: git管理の有無を確認する

「Gitプロジェクトとして独立させますか？（はい/いいえ）」

**いいえの場合:**
```bash
rm -rf workspace/<取り出し先>/.git
```

**はいの場合:**
```bash
git init        (work_dir: <取り出し先>)
git add .       (work_dir: <取り出し先>)
git commit -m "initial: extract from <元リポジトリ名>/<ディレクトリ名>"  (work_dir: <取り出し先>)
```

さらに「新しいGitLabリポジトリを作成しますか？」と聞き、はいなら GitLab API でリポジトリ作成 → push まで実行する。

### 完了報告
取り出し先のパスと git管理の有無を1〜2行で報告する。
