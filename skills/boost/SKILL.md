---
name: boost
description: PC Manager のブースト相当。一時ファイル削除・ごみ箱クリア・DNS キャッシュクリア・メモリ解放を実行する
trigger: /boost
---

## スキル: /boost

ユーザーが `/boost` と入力したとき、または「ブーストして」「メモリを解放して」と依頼したとき：

以下を **順番に** `run_command` で実行する。各ステップの結果（削除件数・解放量）を記録して最後にまとめて報告する。

### ステップ1: ユーザー一時ファイル削除

```
run_command("powershell -Command \"$before = (Get-ChildItem $env:TEMP -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum; Remove-Item \"$env:TEMP\\*\" -Recurse -Force -ErrorAction SilentlyContinue; $after = (Get-ChildItem $env:TEMP -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum; $freed = [math]::Round(($before - $after) / 1MB, 1); Write-Output \"解放: ${freed}MB\"\"", timeout_seconds=30)
```

### ステップ2: Windows 一時ファイル削除

```
run_command("powershell -Command \"Remove-Item 'C:\\Windows\\Temp\\*' -Recurse -Force -ErrorAction SilentlyContinue; Write-Output '完了'\"", timeout_seconds=30)
```

### ステップ3: ごみ箱を空にする

```
run_command("powershell -Command \"Clear-RecycleBin -Force -ErrorAction SilentlyContinue; Write-Output '完了'\"", timeout_seconds=15)
```

### ステップ4: DNS キャッシュクリア

```
run_command("ipconfig /flushdns", timeout_seconds=10)
```

### ステップ5: メモリのワーキングセット解放

```
run_command("powershell -Command \"$before = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1); [System.GC]::Collect(); [System.GC]::WaitForPendingFinalizers(); $after = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1); Write-Output \"解放前: ${before}GB 空き / 解放後: ${after}GB 空き\"\"", timeout_seconds=15)
```

### 完了報告

全ステップ完了後、以下の形式で報告する：

```
ブースト完了！
- 一時ファイル: XX MB 削除
- ごみ箱: クリア済み
- DNS キャッシュ: クリア済み
- 空きメモリ: XX GB → XX GB
```

**注意:**
- `C:\Windows\Temp` の削除は権限エラーが出ることがある（無視して続行）
- メモリ解放は .NET GC レベルのため PC Manager ほど大きくない場合がある
- エラーが出ても途中で止まらず全ステップを実行すること
