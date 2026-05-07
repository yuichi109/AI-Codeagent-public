---
name: rag-review
description: RAG知見DBの記録一覧を表示し、古い・無効な記録を整理する
trigger: /rag-review
---

## スキル: /rag-review

RAGデータベースに蓄積された知見を一覧表示し、陳腐化した記録を整理します。

### 実行手順

1. `rag_list(status="active")` で有効な記録を全件取得する
2. **全タイプをまとめて通し番号で表示する**（タイプをまたいで1, 2, 3... と振る）

```
## RAG知見DB レビュー（active: XX件）

| No | タイプ | 内容 | タグ | 記録日 | ID（短縮） |
|---|---|---|---|---|---|
| 1 | 🚫 prohibited | workspace ルートで git init してはいけない | git, workspace | 2026-05-07 | 8552afb9 |
| 2 | ⚠️ caution   | gitのpushで407エラー... | git, push | 2026-05-07 | 375a4894 |
| 3 | ✅ success    | docker pull してから compose up する | docker | 2026-05-07 | 85890ecb |
```

3. 表示後にユーザーへ確認:
   - 「No.XX を無効にして」「ID 8552afb9 を deprecated にして」のどちらでも受け付ける
   - 最終確認日が **90日以上前** の記録は「まだ有効ですか？」と個別に確認する
   - ユーザーが「古い」「無効」「削除」と言ったら `rag_update_status(id, "deprecated", reason)` を呼ぶ
   - **削除はしない**。deprecated に変更して履歴を保持する

4. 変更した件数を最後に報告する

### deprecated 記録も確認したい場合

`rag_list(status="deprecated")` で無効化済み記録も表示する（同じ通し番号形式で）。
