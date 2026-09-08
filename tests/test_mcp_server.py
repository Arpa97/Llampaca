import os
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from llampaca.engine.mcp_server import create_mcp_server, ALLOW_GATED_TOOLS_ENV
from llampaca.tools import build_default_registry
from mcp.server.fastmcp import FastMCP


class TestMcpServer(unittest.TestCase):
    """
    The MCP server exposes Llampaca's tools over stdio, where no interactive
    confirmation is possible. Tools flagged `requires_confirmation` are
    therefore withheld unless the user opts in explicitly.

    This test previously asserted that *every* registry tool was mapped, which
    encoded the bug: `mcp.tool(name=tool.name)(tool.func)` dropped the
    confirmation flag, so `run_shell_command` and `delete_path` were reachable
    from any MCP client with no Llampaca-side gate at all.
    """

    def setUp(self):
        self.registry = build_default_registry()
        self.gated = {
            name for name in self.registry.names()
            if self.registry.get(name).requires_confirmation
        }
        self.ungated = set(self.registry.names()) - self.gated

    def test_server_identity(self):
        with patch.dict(os.environ, {ALLOW_GATED_TOOLS_ENV: ""}):
            mcp_server = create_mcp_server()
        self.assertIsInstance(mcp_server, FastMCP)
        self.assertEqual(mcp_server.name, "Llampaca")

    def test_registry_actually_has_gated_tools(self):
        """Guards the test below: if nothing were gated, it would pass vacuously."""
        self.assertIn("run_shell_command", self.gated)
        self.assertIn("delete_path", self.gated)
        self.assertIn("write_file", self.gated)

    def test_gated_tools_withheld_by_default(self):
        with patch.dict(os.environ, {ALLOW_GATED_TOOLS_ENV: ""}):
            mcp_tools = create_mcp_server()._tool_manager._tools

        for name in self.gated:
            self.assertNotIn(name, mcp_tools, f"{name} must not be exposed by default")

    def test_ungated_tools_still_exposed(self):
        with patch.dict(os.environ, {ALLOW_GATED_TOOLS_ENV: ""}):
            mcp_tools = create_mcp_server()._tool_manager._tools

        self.assertEqual(set(mcp_tools), self.ungated)
        for name in self.ungated:
            # Two independently built registries produce DISTINCT function
            # objects for closure-based tools (e.g. the wiki tools created
            # inside register_wiki_tools), so identity comparison is wrong
            # here: compare by qualified name instead.
            self.assertEqual(
                mcp_tools[name].fn.__qualname__,
                self.registry.get(name).func.__qualname__,
            )

    def test_opt_in_exposes_gated_tools_as_destructive(self):
        with patch.dict(os.environ, {ALLOW_GATED_TOOLS_ENV: "1"}):
            mcp_tools = create_mcp_server()._tool_manager._tools

        self.assertEqual(set(mcp_tools), set(self.registry.names()))
        for name in self.gated:
            annotations = mcp_tools[name].annotations
            self.assertIsNotNone(annotations, f"{name} must carry annotations")
            self.assertTrue(annotations.destructiveHint)
            self.assertFalse(annotations.readOnlyHint)

    def test_opt_in_accepts_documented_values(self):
        for value in ("1", "true", "yes", "TRUE"):
            with self.subTest(value=value), patch.dict(os.environ, {ALLOW_GATED_TOOLS_ENV: value}):
                self.assertIn("run_shell_command", create_mcp_server()._tool_manager._tools)

        for value in ("0", "false", "no", ""):
            with self.subTest(value=value), patch.dict(os.environ, {ALLOW_GATED_TOOLS_ENV: value}):
                self.assertNotIn("run_shell_command", create_mcp_server()._tool_manager._tools)


if __name__ == "__main__":
    unittest.main()
