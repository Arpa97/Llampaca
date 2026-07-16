"""
MCP Server implementation for Llampaca.

This module exposes Llampaca's built-in tools (filesystem, shell, web) 
to Model Context Protocol (MCP) clients like VSCode Cline or Claude Desktop.
"""

import sys
from mcp.server.fastmcp import FastMCP
from llampaca.tools import build_default_registry


def create_mcp_server() -> FastMCP:
    """
    Create a FastMCP server instance and register all default tools.
    """
    mcp = FastMCP("Llampaca")
    registry = build_default_registry()

    for name in registry.names():
        tool = registry.get(name)
        # Register the tool function dynamically with FastMCP.
        # fastmcp uses the function signature and docstrings to generate schemas.
        mcp.tool(name=tool.name)(tool.func)

    return mcp


def main():
    """
    Start the MCP server using stdio transport.
    """
    mcp = create_mcp_server()
    # Always redirect standard output of the application setup to sys.stderr 
    # to avoid corrupting the stdio JSON-RPC channel.
    print("Starting Llampaca MCP Server...", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
