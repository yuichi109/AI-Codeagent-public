---
name: infra
description: インフラ管理ツール一覧を表示する。利用可能なホスト情報収集・リモート操作ツールの使い方を案内する。
trigger: /infra
---

## スキル: /infra

ユーザーが `/infra` と入力したとき、以下の内容を **そのまま** Markdown テーブルで表示する。ツールを実行してはいけない。

---

## インフラ管理で使えるツール

### ホスト情報収集

| ツール | 対象 | 説明 |
|---|---|---|
| `gather_host_info` | Linux / Windows | OS・CPU・メモリ・ディスク・NIC・DNS・GW・サービス・パッケージ・ユーザー・ポートを一括収集。`os_type="auto"` でOS自動判定。 |
| `winrm_command` | Windows | 任意のPowerShellコマンドをリモート実行（TrustedHosts不要） |
| `run_command` + SSH | Linux | 鍵認証でリモートコマンドを実行 |

### 収集できる情報

| カテゴリ | Linux | Windows |
|---|---|---|
| OS・ホスト名・ドメイン | ✅ | ✅ |
| CPU・メモリ・ディスク | ✅ | ✅ |
| NIC・DNS・デフォルトGW | ✅ | ✅ |
| オープンポート | ✅ | ✅ |
| インストール済みパッケージ/ソフト | ✅ | ✅ |
| 実行中サービス | ✅ | ✅ |
| ユーザー一覧 | ✅ | ✅ |
| ルーティング・cron | ✅ | — |
| スケジュールタスク・Windows Update | — | ✅ |

### 使い方の例

```
# OS不明の場合（自動判定）
「10.49.89.160 に Administrator/#Password01 で接続して仕様書を作って。鍵は workspace の xxx.pem を使って」

# Linux（鍵認証）
「10.49.89.137 に user で接続して仕様書を作って。鍵は spec_id_rsa-m.pem」

# Windows（パスワード認証）
「10.49.89.160 に Administrator/#Password01 で接続してインフラ設計書を作って」

# 任意コマンドを投げたい場合
「10.49.89.160 で Get-EventLog を実行して」
```

### 認証方式

| OS | 方式 | 必要な情報 |
|---|---|---|
| Windows | WinRM (NTLM) | host・username・password |
| Linux | SSH 鍵認証 | host・username・key_file（workspaceに置いたPEMファイル） |

> **自動判定の仕組み**: `os_type="auto"` を指定するとポート 5985(WinRM) → Windows / ポート 22(SSH) → Linux の順で判定する
