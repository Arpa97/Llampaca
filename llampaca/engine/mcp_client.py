"""
MCP Client implementation for Llampaca.

This module allows Llampaca's agent to act as an MCP client, connecting to
external MCP servers configured in config.json and exposing their tools
to the local LLM.
"""

import os
import sys
import json
from typing import Dict, List, Optional
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from llampaca.tools.registry import Tool, ToolRegistry


def format_mcp_result(result) -> str:
    """Format the result of a tool call returned by an MCP server into a single string."""
    texts = []
    if hasattr(result, "content") and result.content:
        for item in result.content:
            text = getattr(item, "text", None)
            if text is not None:
                texts.append(text)
                continue

            if isinstance(item, dict):
                if "text" in item:
                    texts.append(item["text"])
                    continue
                texts.append(json.dumps(item))
                continue

            data = getattr(item, "data", None)
            if data is not None:
                mime_type = getattr(item, "mimeType", "image/png")
                texts.append(f"[Embedded media: {mime_type}, {len(data)} bytes]")
                continue

            texts.append(str(item))
    return "\n".join(texts)


def check_requires_confirmation(server_cfg: dict, tool_name: str) -> bool:
    """
    Check if an MCP tool requires user confirmation.

    Order of preference:
    1. Check if 'requires_confirmation' is explicitly False in the config -> return False.
    2. Check if 'requires_confirmation' is a list of tool names -> return True if tool_name is in the list.
    3. Check if 'requires_confirmation' is explicitly True -> return True.
    4. Safe heuristic fallback based on dangerous keywords.
    """
    config_confirm = server_cfg.get("requires_confirmation")
    if config_confirm is False:
        return False
    if isinstance(config_confirm, list):
        return tool_name in config_confirm
    if config_confirm is True:
        return True

    # Safe heuristic fallback
    dangerous_keywords = [
        "write", "delete", "send", "remove", "create", "update",
        "edit", "run", "execute", "modify", "destroy", "post",
        "patch", "put"
    ]
    return any(kw in tool_name.lower() for kw in dangerous_keywords)


class McpClientManager:
    """Manages the lifecycle of external MCP servers and registers their tools."""

    def __init__(self, mcp_servers_config: dict):
        self.config = mcp_servers_config
        self.exit_stack = AsyncExitStack()
        self.sessions: Dict[str, ClientSession] = {}

    async def start(self, registry: ToolRegistry):
        """Start all configured MCP servers and register their tools."""
        for name, server_cfg in self.config.items():
            try:
                # Sanitize environment: start from a minimal whitelist to prevent exposing
                # sensitive host environment credentials (AWS_SECRET_ACCESS_KEY, GITHUB_TOKEN, etc.)
                base_env_keys = {
                    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
                    "TMPDIR", "TEMP", "TMP", "SystemRoot", "SystemDrive", "APPDATA", "LOCALAPPDATA",
                    "PROGRAMDATA", "PATHEXT", "COMSPEC", "NODE_PATH", "TERM"
                }
                merged_env = {k: v for k, v in os.environ.items() if k in base_env_keys}
                if "env" in server_cfg and isinstance(server_cfg["env"], dict):
                    for k, v in server_cfg["env"].items():
                        merged_env[k] = str(v)

                command = server_cfg.get("command")
                if not command:
                    print(f"Skipping MCP server '{name}': no command specified.", file=sys.stderr)
                    continue

                args = server_cfg.get("args", [])

                params = StdioServerParameters(
                    command=command,
                    args=args,
                    env=merged_env,
                )

                print(f"Connecting to MCP server '{name}'...", file=sys.stderr)
                
                async def _connect_and_init():
                    read_stream, write_stream = await self.exit_stack.enter_async_context(
                        stdio_client(params)
                    )

                    session = await self.exit_stack.enter_async_context(
                        ClientSession(read_stream, write_stream)
                    )

                    await session.initialize()
                    self.sessions[name] = session

                    # Fetch tools and register them
                    tools_result = await session.list_tools()
                    return tools_result

                import asyncio
                try:
                    tools_result = await asyncio.wait_for(_connect_and_init(), timeout=300.0)
                except asyncio.TimeoutError:
                    raise RuntimeError("Connection or initialization timed out after 300 seconds")

                registered_count = 0
                for mcp_tool in tools_result.tools:
                    prefixed_name = f"{name}__{mcp_tool.name}"

                    # Closure to bind session and original tool name
                    def make_wrapper(s_name, t_name):
                        async def mcp_tool_wrapper(**kwargs):
                            sess = self.sessions.get(s_name)
                            try:
                                result = await asyncio.wait_for(sess.call_tool(t_name, kwargs), timeout=60.0)
                            except asyncio.TimeoutError:
                                raise RuntimeError(f"Il server MCP '{s_name}' non ha risposto per il tool '{t_name}' entro 60 secondi.")
                            formatted = format_mcp_result(result)
                            if getattr(result, "isError", False):
                                raise RuntimeError(formatted)
                            return formatted
                        return mcp_tool_wrapper

                    wrapper_func = make_wrapper(name, mcp_tool.name)
                    req_confirm = check_requires_confirmation(server_cfg, mcp_tool.name)

                    tool = Tool(
                        name=prefixed_name,
                        description=mcp_tool.description or "",
                        func=wrapper_func,
                        parameters=mcp_tool.inputSchema or {"type": "object", "properties": {}, "required": []},
                        requires_confirmation=req_confirm
                    )
                    registry._tools[prefixed_name] = tool
                    registered_count += 1

                print(f"Connected to MCP server '{name}' with {registered_count} tools.", file=sys.stderr)
            except Exception as e:
                print(f"Failed to start MCP server '{name}': {e}", file=sys.stderr)

    async def stop(self):
        """Close all MCP server sessions and terminate processes."""
        await self.exit_stack.aclose()
        self.sessions.clear()
