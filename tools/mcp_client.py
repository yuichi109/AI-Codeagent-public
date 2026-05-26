"""MCP クライアント管理モジュール。

mcp_servers.json に定義された MCP サーバーに接続し、ツールを動的に TOOL_REGISTRY へ登録する。
AsyncExitStack で各サーバーの stdio_client / ClientSession を管理する。
"""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "mcp_servers.json"
TOOL_CALL_TIMEOUT = 60  # seconds


def _load_server_configs() -> list[dict]:
    if not CONFIG_PATH.exists():
        return []
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    servers = data.get("servers", [])
    for srv in servers:
        srv["args"] = [os.path.expandvars(a) for a in srv.get("args", [])]
    return [s for s in servers if s.get("enabled", False)]


def _mcp_tool_to_openai_schema(server_id: str, tool) -> dict:
    """MCP ツール定義を OpenAI function-calling スキーマに変換する。"""
    name = f"{server_id}__{tool.name}"
    schema = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": tool.description or "",
            "parameters": schema,
        },
    }


class _ServerConnection:
    def __init__(self, config: dict):
        self.config = config
        self.session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self.tools: list = []
        self._lock = asyncio.Lock()

    async def connect(self):
        cfg = self.config
        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env=None,
        )
        self._exit_stack = AsyncExitStack()
        stdio_transport = await self._exit_stack.enter_async_context(stdio_client(params))
        read, write = stdio_transport
        self.session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        result = await self.session.list_tools()
        self.tools = result.tools
        logger.info("MCP [%s] 接続完了: %d ツール取得", cfg["id"], len(self.tools))

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        if self.session is None:
            return "エラー: MCP サーバーに接続されていません"
        async with self._lock:
            try:
                result = await asyncio.wait_for(
                    self.session.call_tool(tool_name, arguments),
                    timeout=TOOL_CALL_TIMEOUT,
                )
                parts = []
                for c in result.content:
                    if hasattr(c, "text"):
                        parts.append(c.text)
                    elif hasattr(c, "data"):
                        parts.append(f"[画像データ: {c.mimeType}]")
                return "\n".join(parts) if parts else "(空のレスポンス)"
            except asyncio.TimeoutError:
                return f"エラー: MCPツール呼び出しがタイムアウト ({TOOL_CALL_TIMEOUT}秒)"
            except Exception as e:
                return f"エラー: {e}"

    async def close(self):
        self.session = None
        stack = self._exit_stack
        self._exit_stack = None
        if stack:
            try:
                await stack.aclose()
            except BaseException:
                # anyio cancel scope が shutdown 時にキャンセルされるため抑制
                pass


class MCPClientManager:
    """起動・停止・ツール登録を一元管理するクラス。"""

    def __init__(self):
        self._connections: dict[str, _ServerConnection] = {}

    async def start(self):
        configs = _load_server_configs()
        if not configs:
            print("[MCP] 有効なサーバー設定がないためスキップ", flush=True)
            return
        for cfg in configs:
            conn = _ServerConnection(cfg)
            try:
                print(f"[MCP] {cfg['id']} 接続開始...", flush=True)
                await conn.connect()
                print(f"[MCP] {cfg['id']} 接続完了: {len(conn.tools)} ツール", flush=True)
                self._connections[cfg["id"]] = conn
            except Exception as e:
                print(f"[MCP] {cfg['id']} 起動失敗: {e}", flush=True)

    async def stop(self):
        for conn in list(self._connections.values()):
            await conn.close()
        self._connections.clear()

    def get_tool_schemas(self) -> list[dict]:
        schemas = []
        for server_id, conn in self._connections.items():
            for tool in conn.tools:
                schemas.append(_mcp_tool_to_openai_schema(server_id, tool))
        return schemas

    def get_tool_registry(self) -> dict[str, Any]:
        registry = {}
        for server_id, conn in self._connections.items():
            for tool in conn.tools:
                full_name = f"{server_id}__{tool.name}"
                original_name = tool.name

                def make_caller(c: _ServerConnection, tname: str):
                    async def caller(**kwargs) -> str:
                        return await c.call_tool(tname, kwargs)
                    return caller

                registry[full_name] = make_caller(conn, original_name)
        return registry

    def status(self) -> dict:
        return {
            sid: {
                "connected": conn.session is not None,
                "tools": [t.name for t in conn.tools],
            }
            for sid, conn in self._connections.items()
        }
