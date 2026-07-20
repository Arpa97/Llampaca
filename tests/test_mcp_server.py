import unittest
from pathlib import Path
import sys

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from llampaca.engine.mcp_server import create_mcp_server
from llampaca.tools import build_default_registry
from mcp.server.fastmcp import FastMCP

class TestMcpServer(unittest.TestCase):
    def test_mcp_server_creation_and_tools(self):
        """Verify that the MCP server is created and contains all Llampaca registry tools."""
        mcp_server = create_mcp_server()
        
        # Verify it is a FastMCP instance with the correct name
        self.assertIsInstance(mcp_server, FastMCP)
        self.assertEqual(mcp_server.name, "Llampaca")
        
        # Get all registered tools in the default registry
        default_registry = build_default_registry()
        registry_tools = default_registry.names()
        
        # Verify that all tools are successfully mapped into the MCP server
        mcp_tools = mcp_server._tool_manager._tools
        self.assertEqual(len(mcp_tools), len(registry_tools))
        
        for name in registry_tools:
            self.assertIn(name, mcp_tools)
            
            # Check that the mapped function is the same registry function.
            # Two independently built registries produce DISTINCT function
            # objects for closure-based tools (e.g. the wiki tools created
            # inside register_wiki_tools), so identity comparison is wrong
            # here: compare by qualified name instead — what matters is that
            # the same tool implementation is mapped, not that both
            # registries happen to share one function instance.
            tool = default_registry.get(name)
            self.assertEqual(mcp_tools[name].fn.__qualname__, tool.func.__qualname__)

if __name__ == "__main__":
    unittest.main()
