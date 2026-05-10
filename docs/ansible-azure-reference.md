# Ansible Azure リファレンス

> 実装時に参照すべき既存コードの場所と使い方のメモ。

---

## Azure VM スナップショット → リバート

### 既存コードの場所

リポジトリ: `spec2325705/bk-yuichi.matsuo`（GitLab）

| ファイル | 内容 |
|---|---|
| `3Ti-NC-Role-FIX/rsrenew-snap_to_vm.yml` | スナップショット作成＋VM再作成（Linux 3台構成） |
| `3TierWEB/VMDEV-WEB/rsrenew-snap_to_vm.yml` | 同上・別構成 |
| `W2025JP_TMP/mk_template.yml` | Windows Server 2025 日本語化済みイメージ作成 |

### 現状コードの構造

```
myVMのOSディスクID取得
  → 既存スナップショット削除
  → 現在のディスクからスナップショット作成（毎回作り直し）
  → VM2/VM3/VM4 それぞれ：
       VM削除 → ディスク作成 → NSG作成 → NIC作成 → VMデプロイ
```

※ Linux 3台構成・毎回スナップショットを作り直す設計

### Windows単体VM用に改変する場合のフロー

```
【初回のみ】
  SYSPREP済みVMのOSディスクからスナップショット取得
  → 取得済みチェック → あればスキップ

【テストのたびに】
  VM停止（deallocate）
  → VM削除（NIC・NSGは別リソースなので残る・IPも維持される）
  → 古いOSディスク削除
  → スナップショットから新しいOSディスク作成
  → 同じ名前・既存のNIC・NSGを使ってVMを再デプロイ
```

### 既存コードとの対応（改変ポイント）

| 既存コードの処理 | 改変後 |
|---|---|
| スナップショット作成（毎回） | 初回のみ・取得済みチェック追加 |
| VM削除 | ✅ そのまま使える |
| スナップショットからディスク作成 | ✅ そのまま使える |
| NSG・NIC作成 | ❌ 不要（既存を使う） |
| VMデプロイ | 既存NICのIDを参照する形に変更 |

`vm_from_disk_template.json`（ARMテンプレート）はOS問わず流用可能。

### 想定ユースケース

マルチエージェント Phase 2 のインフラ担当AIが、テスト用Windows VMを
クリーンな状態に戻す運用。テスト完了後にスナップショットからリバートする。

---

## 検索時のメモ

GitLab APIのグローバル検索は無効（403）のため、プロジェクトIDを指定して検索する。

```
プロジェクトID: 68808781
検索キーワード: azure_rm_snapshot / deallocate / os_disk
API: GET /api/v4/projects/68808781/search?scope=blobs&search=azure_rm_snapshot
```
