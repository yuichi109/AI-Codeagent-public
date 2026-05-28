---
name: inbox-scan
description: Obsidian inbox を即時スキャンして処理待ちリクエストを実行する
trigger: /inbox-scan
---

## スキル: /inbox-scan

ユーザーが `/inbox-scan`、「inbox をスキャン」「inbox を確認」と入力したとき：

**必ず POST メソッドで** `http://localhost:8000/inbox/scan` を呼び出し、結果をユーザーに伝える。

```bash
curl -s -X POST http://localhost:8000/inbox/scan
```

または Python:

```python
import requests
res = requests.post("http://localhost:8000/inbox/scan")
print(res.json())
```

※ GET では "Method Not Allowed" になるので必ず `-X POST` を指定すること。

- 処理待ちがなければ「inbox に新しいリクエストはありません」と伝える
- 処理を開始した場合は件数と「results/ に書き出されます」と伝える

## 設定

inbox 監視は `.env` の `OBSIDIAN_INBOX_ENABLED=true` で有効化。
未設定の場合は「inbox 監視が無効です。/setup → Obsidian 連携で有効にしてください」と伝える。

## 将来案（未実装）

`/send-to-inbox <指示テキスト>` スキル — チャット内の指示を inbox の MD として保存するショートカット。
自然言語でも「これをインボックスに置いて」と言えば `write_file` で対応可能（追加実装不要）。
