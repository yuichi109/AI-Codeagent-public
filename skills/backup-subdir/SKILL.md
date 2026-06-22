---
name: backup-subdir
description: workspace 以下のサブディレクトリを個別に tar.gz でバックアップ／リストア
trigger: /backup-subdir
---

## スキル: /backup-subdir

workspace 以下のサブディレクトリを **個別の tar.gz** でバックアップ／リストアします。

### バックアップ

1. **`list_files(".")`** で **workspace ルート（ALLOWED_WORK_DIR）の直下**のサブディレクトリ一覧を取得し、**番号付きリスト**で表示する
   - **重要**: 現在のセッションのスコープが `AI` 等になっていても、**その中ではなく必ず workspace ルート直下**を対象にする（`list_files(".")` は常に workspace ルートを指す）。サブディレクトリの中（`list_files("AI")` 等）を見てはいけない。
2. ユーザーが番号（複数可: 1,3）を入力
3. `mkdir -p ~/Backups/` で保存先を確保し、日付を `date +%Y%m%d` で取得しておく
4. 選択された各ディレクトリについて、**必ず圧縮（tar）の前に**保存先ファイルの有無を確認する
   - ⚠️ **`tar czf` は既存ファイルを無条件で黙って上書きする**。確認は必ず**圧縮の前**に行うこと（圧縮してから確認しても手遅れ＝既に上書き済み）。
   - `test -f ~/Backups/<dir>_<YYYYMMDD>.tar.gz && echo EXISTS` で存在確認（`EXISTS` と出れば既存ファイルあり）
   - **既存だった場合** → 「上書きしますか？（y）／時刻付きで別名保存しますか？（t）」と確認し、**ユーザーの回答を待ってから**圧縮する
     - **y** → `tar czf ~/Backups/<dir>_<YYYYMMDD>.tar.gz <dir>`（上書き）
     - **t** → 時刻を `date +%H%M%S` で取得し `tar czf ~/Backups/<dir>_<YYYYMMDD>_<HHMMSS>.tar.gz <dir>`
   - **存在しなかった場合** → そのまま `tar czf ~/Backups/<dir>_<YYYYMMDD>.tar.gz <dir>`
   - いずれも work_dir は workspace ルート・`timeout_minutes`（例10）を指定
5. 完了時 `ls -lh ~/Backups` でファイルサイズ一覧を表示

### リストア

1. `ls -lh ~/Backups` でバックアップ一覧を取得し、**番号付きリスト**で表示
2. ユーザーが番号を入力
3. 展開先ディレクトリが既存の場合、「上書きしますか？ 上書き前に一時バックアップを取りますか？」と確認
   - **はい** → `cp -r <dir> <dir>_<YYYYMMDD>_before` で退避後に `tar xzf` 展開
   - **いいえ** → 安全のため中断する
4. `tar xzf ~/Backups/<file>.tar.gz` で展開（work_dir は workspace ルート・`timeout_minutes` を指定）
5. 完了を報告

### 注意

- 保存先: `~/Backups/`（WSL ユーザープロセス内で作成。run_command でアクセス可能）
- バックアップは **workspace ルート相対**で行う（tar の解凍先が合うように）
- `write_file` ツールは使わず、すべて `run_command` で `tar` を実行する
- バックアップファイル名に使う日時は `date +%Y%m%d`（時刻は `date +%H%M%S`）で取得する
- `tar` は時間がかかることがあるため、`run_command` に `timeout_minutes`（例: 10）を指定して既定30秒で打ち切られないようにする

### `run_command` の注意（重要）

- `run_command` は内部的に **`shell=False`** で動作する。シェルによる展開は一切行われない。
- **シェル変数展開は行われない**: `$(date +%Y%m%d)` や `$HOME` などを直接渡してはいけない。
  - 日付をファイル名に使いたい場合は、事前に `run_command("date +%Y%m%d")` で文字列を取得し、それを変数に埋め込んでから使うこと。
  - **絶対に** ファイル名に `$(date)` を直接書いて `tar` などに渡さない（`AI_$(date` のようなゴミファイルができる）。
- **グロブ（ワイルドカード）も展開されない**: `ls ~/Backups/*.tar.gz` は `*` がそのまま `ls` に渡り「No such file」で失敗する。
  - ディレクトリ全体を見るなら `ls -lh ~/Backups`（このディレクトリは本スキル専用なので tar.gz のみが入る）。
  - パターンで絞りたいときは `find ~/Backups -maxdepth 1 -name '*.tar.gz'`（`find` は内部でマッチするので動く）を使う。
- `~`（チルダ）はホームに展開される（`~/Backups` → `/home/<user>/Backups`）。これは利用してよい。
- `&&` による連結は `run_command` が自動分割して順次実行するため使える。
