# MCP クライアント実装 設計書

**作業ブランチ**: `feature/mcp-client`
**目的**: 公式 `mcp` Python ライブラリを使い、外部 MCP サーバーのツールをエージェントに動的に追加できるようにする。

---

## フェーズ構成

| フェーズ | 内容 | 優先度 |
|---|---|---|
| Phase 1 | MCP クライアント基盤 + Playwright MCP | 最優先 |
| Phase 2 | Obsidian MCP | Phase 1 完了後 |
| Phase 3 | 設定画面 UI（/setup からサーバー追加） | Phase 2 完了後 |

---

## Phase 1: MCP クライアント基盤

### 追加する依存パッケージ

```
mcp>=1.0.0
playwright>=1.40.0
```

`requirements.txt` に追記。初回セットアップ時に `playwright install chromium` も必要。

### 新規ファイル構成

```
tools/
  mcp_client.py       ← MCP クライアント本体（接続・ツール取得・呼び出し）
config/
  mcp_servers.json    ← 接続する MCP サーバーの設定ファイル（gitignore 対象外）
```

### `mcp_servers.json` の形式

```json
{
  "servers": [
    {
      "id": "playwright",
      "name": "Playwright MCP",
      "transport": "stdio",
      "command": "npx",
      "args": ["@playwright/mcp@latest"],
      "enabled": true
    },
    {
      "id": "obsidian",
      "name": "Obsidian MCP",
      "transport": "stdio",
      "command": "npx",
      "args": ["obsidian-mcp", "/path/to/vault"],
      "enabled": false
    }
  ]
}
```

### `mcp_client.py` の責務

1. **接続管理**: `mcp_servers.json` を読んで有効なサーバーに stdio 接続
2. **ツール取得**: 各サーバーから `tools/list` を取得
3. **スキーマ変換**: MCP ツール定義 → OpenAI 関数呼び出し形式に変換
4. **ツール呼び出し**: `execute_tool` から呼び出せる統一インターフェース提供
5. **プロセス管理**: サーバー起動・停止・クラッシュ時の再接続

### server.py への統合方針

#### 起動時（`startup` イベント）
```python
from tools.mcp_client import MCPClientManager
mcp_manager = MCPClientManager()
await mcp_manager.start()          # 有効なサーバーに接続
TOOL_REGISTRY.update(mcp_manager.get_tool_registry())   # 動的に登録
TOOLS.extend(mcp_manager.get_tool_schemas())            # LLM 定義に追加
```

#### 終了時（`shutdown` イベント）
```python
await mcp_manager.stop()           # 子プロセスを正常終了
```

#### `execute_tool` への影響
既存の `execute_tool` は `TOOL_REGISTRY[name](**arguments)` を呼ぶだけなので、
`mcp_manager` が返す callable を登録すれば**既存コードの変更不要**。

---

## Phase 2: Obsidian MCP

`mcp_servers.json` に Obsidian サーバーのエントリを追加するだけ。
ボルトパスは `.env` の `OBSIDIAN_VAULT_PATH` から読む。

---

## Phase 3: 設定画面 UI

`/setup` 画面に「MCP サーバー」セクションを追加。
- サーバー一覧表示（有効/無効・接続状態）
- 追加・削除・有効化トグル
- 変更後はサーバーを再起動して即反映

---

## 実装上の注意点

- **stdio サーバーは子プロセス**: `asyncio.create_subprocess_exec` で起動。サービス再起動時に一緒に落とす。
- **ツール名の衝突**: MCP サーバー ID をプレフィックスにする（例: `playwright__screenshot`）
- **タイムアウト**: MCP サーバーへの呼び出しは 60 秒タイムアウトを設ける
- **`mcp_servers.json` が存在しない場合**: MCP 機能をスキップして通常起動（後方互換）
- **Node.js 依存**: Playwright MCP・Obsidian MCP は `npx` 経由のため Node.js が必要。`setup.sh` にチェック追加。

---

## 実装手順（セッション再開時の参照用）

- [x] `requirements.txt` に `mcp>=1.0.0` 追加
- [x] `setup.sh` に Node.js チェック・`npx @playwright/mcp install-browser chromium` 追加
- [x] `config/mcp_servers.json` のサンプルファイル作成
- [x] `tools/mcp_client.py` 実装（接続・ツール取得・スキーマ変換・呼び出し）
- [x] `server.py` の startup/shutdown に統合
- [x] Playwright MCP で動作確認（browser_navigate / browser_take_screenshot 等 23 ツール登録確認済み）
- [ ] Phase 2: Obsidian MCP 追加
- [ ] Phase 3: /setup UI 追加（MCP サーバー一覧・有効/無効トグル）
- [ ] `feature/mcp-client` を main にマージ
