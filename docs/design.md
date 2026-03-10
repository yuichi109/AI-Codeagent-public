# AI Code Agent 拡張設計

## 概要

Azure OpenAI (gpt-4.1-mini) を使ったコードエージェントの拡張設計。
既存の `server.py` + `index.html` を基に、Web 検索・コードリント・セキュリティ強化を追加。

---

## 実装フェーズ

### フェーズ0: GitLab プロジェクト作成 ✅ 完了
- GitLab.com に `AI-Codeagent` プロジェクト作成 (private)
- URL: https://gitlab.com/yuichi.matsuo/AI-Codeagent

### フェーズ1: セキュリティ基盤 (実装中)
- [ ] `.env` + `.env.example` 作成
- [ ] `config.py` 実装 (python-dotenv)
- [ ] `tools/command_tools.py`: shell=False + ホワイトリスト化

### フェーズ2: ファイルツール分離
- [ ] `tools/file_tools.py`: read_file, write_file, list_files
- [ ] パストラバーサル対策 (`_resolve_safe_path()`)

### フェーズ3: Web ツール
- [ ] `tools/web_tools.py`: web_search (DuckDuckGo), web_fetch
- [ ] SSRF 対策 (プライベート IP 拒否)

### フェーズ4: コードリントツール
- [ ] `tools/code_tools.py`: code_lint (ruff)
- [ ] 一時ファイルの安全な処理

### フェーズ5: システムプロンプト
- [ ] `prompts.py`: コード生成・レビュー特化

### フェーズ6: server.py リファクタリング
- [ ] TOOL_REGISTRY でディスパッチ
- [ ] ツール定義の整理・拡張

### フェーズ7: フロントエンド最小変更
- [ ] ツールアイコン追加 (🔍🌐📁🔬)
- [ ] 長い結果の `<details>` 折りたたみ

---

## ツール仕様

### read_file(path, encoding="utf-8")
作業ディレクトリ内のファイルを読む。パストラバーサル防止済み。

### write_file(path, content, mode="overwrite")
ファイルを書き込む。`mode="append"` も可。

### list_files(path=".", pattern="*")
`pathlib.Path.glob()` で一覧取得。最大 200 件。

### run_command(command, work_dir=None)
- `shell=False` + `shlex.split()` でインジェクション防止
- 許可コマンド: python, python3, pip, git, ls, cat, head, tail, grep, find, mkdir, touch, ruff, black, mypy, node, npm, go, cargo
- タイムアウト: 30 秒、出力上限: 4096 文字

### web_search(query, max_results=5)
DuckDuckGo Instant Answer API → RelatedTopics から結果抽出。

### web_fetch(url, extract_text=True, max_chars=8000)
requests + BeautifulSoup4。SSRF 対策あり。

### code_lint(file_path=None, code=None, language=None)
Python: ruff check --output-format=json。一時ファイルは finally で削除。

---

## セキュリティ設計

| 脅威 | 対策 |
|---|---|
| API キー漏洩 | .env に分離、.gitignore に追加 |
| シェルインジェクション | shell=False + コマンドホワイトリスト |
| パストラバーサル | Path.resolve() + ALLOWED_WORK_DIR チェック |
| SSRF | socket.gethostbyname() でプライベート IP 拒否 |
| 出力爆発 | コマンド出力 4096 文字、web_fetch 8000 文字上限 |

---

## 依存パッケージ

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
openai>=1.30.0
python-dotenv>=1.0.0
requests>=2.31.0
beautifulsoup4>=4.12.0
ruff>=0.4.0
```
