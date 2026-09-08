"""
MCP Server implementation for Llampaca.

This module exposes Llampaca's built-in tools (filesystem, shell, web)
to Model Context Protocol (MCP) clients like VSCode Cline or Claude Desktop.

Confirmation model
------------------
Llampaca's own safety model gates destructive tools (`run_shell_command`,
`write_file`, `edit_file`, `delete_path`, the wiki/skill writers, the package
installer) behind an interactive confirmation. That gate is enforced by the
agent loop, which calls `execute_with_confirmation` and prompts through the
CLI or GUI.

Over MCP stdio there is no such UI: Llampaca is a subprocess talking JSON-RPC,
with no way to ask the user anything. Registering the gated tools here anyway
would silently drop the confirmation — `run_shell_command` would run whatever
the calling model asked for, with the user's privileges.

So by default those tools are **not exposed** over MCP. The MCP specification
does expect clients to seek user approval before invoking a tool, and the
major ones do prompt — but that is the client's promise, not something
Llampaca can verify, and "the other program probably asks" is not the safety
model this project documents. Users who do trust their client's approval flow
can opt back in with the environment variable below; the tools are then
annotated as destructive so a compliant client prompts accordingly.
"""

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from llampaca.tools import build_default_registry

logger = logging.getLogger(__name__)

# Set to 1/true/yes to expose the confirmation-gated tools over MCP, delegating
# user approval to the MCP client.
ALLOW_GATED_TOOLS_ENV = "LLAMPACA_MCP_ALLOW_CONFIRMED_TOOLS"


def _gated_tools_allowed() -> bool:
    """True when the user has explicitly opted into exposing gated tools."""
    return os.environ.get(ALLOW_GATED_TOOLS_ENV, "").strip().lower() in ("1", "true", "yes")


def create_mcp_server() -> FastMCP:
    """
    Create a FastMCP server instance and register the default tools.

    Tools flagged `requires_confirmation` are skipped unless the user opted in
    via ALLOW_GATED_TOOLS_ENV — see the module docstring.
    """
    mcp = FastMCP("Llampaca")
    registry = build_default_registry()
    allow_gated = _gated_tools_allowed()

    withheld = []
    for name in registry.names():
        tool = registry.get(name)

        if tool.requires_confirmation and not allow_gated:
            withheld.append(name)
            continue

        annotations = None
        if tool.requires_confirmation:
            # Exposed only because the user opted in: label it so the client
            # knows to ask before calling. destructiveHint/readOnlyHint are
            # left unset for the other tools, where the MCP defaults
            # (readOnly=false, destructive=true) are already the cautious
            # reading and asserting otherwise could be wrong — generate_image,
            # for instance, is ungated but does write a file.
            annotations = ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
            )

        # fastmcp uses the function signature and docstrings to generate schemas.
        mcp.tool(name=tool.name, annotations=annotations)(tool.func)

    if withheld:
        # stderr, not stdout: stdout is the JSON-RPC channel.
        print(
            f"[Llampaca MCP] {len(withheld)} confirmation-gated tool(s) not exposed: "
            f"{', '.join(sorted(withheld))}.\n"
            f"[Llampaca MCP] These require interactive approval, which is unavailable over "
            f"stdio. Set {ALLOW_GATED_TOOLS_ENV}=1 to expose them and rely on your MCP "
            f"client to ask for confirmation instead.",
            file=sys.stderr,
        )
    elif allow_gated:
        print(
            f"[Llampaca MCP] WARNING: {ALLOW_GATED_TOOLS_ENV} is set — destructive tools "
            f"(shell, file writes, deletions) are exposed. Approval is now entirely up to "
            f"your MCP client.",
            file=sys.stderr,
        )

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
