# AI Code Agent — テスト項目チェックリスト

> 新機能追加時や別PCへの移行後に実施するチェックリスト。

---

## 基本動作
- [ ] `uvicorn server:app --reload` で正常起動
- [ ] `http://localhost:8000` でチャット UI 表示
- [ ] メッセージ送信 → AI 応答が返ってくる

## ツール動作確認
- [ ] `list_files` → workspace のファイル一覧が返る
- [ ] `write_file` → workspace にファイルが作成される
- [ ] `read_file` → ファイル内容が返る
- [ ] `run_command("python3 --version")` → バージョンが返る
- [ ] `bash script.sh` → bubblewrap で実行される
- [ ] `web_search "FastAPI"` → 検索結果が返る
- [ ] `web_fetch "https://httpbin.org/get"` → コンテンツが返る
- [ ] `code_lint` → ruff が動作する

## セキュリティ確認
- [ ] `read_file("../../etc/passwd")` → エラーで拒否される
- [ ] `run_command("dd if=/dev/zero of=/dev/sda")` → ブラックリスト拒否
- [ ] `bash -c "rm -rf /"` → 形式エラーで拒否
- [ ] bash スクリプト内の `curl` → bubblewrap でネットワーク遮断

## GitLab 連携
- [ ] `curl` で GitLab プロジェクト作成 → 成功
- [ ] `git init` + `git push` → GitLab に反映
- [ ] `.env` の PAT が切れた場合のエラーメッセージ確認

## UI
- [ ] ページリロードで履歴が復元される
- [ ] 5ターン超えで古いターンが折りたたまれる
- [ ] 「履歴クリア」で localStorage が消える
- [ ] ツール実行ブロックに説明が表示される
