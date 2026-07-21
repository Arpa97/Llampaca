import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import sys

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from llampaca.engine.mcp_client import (
    format_mcp_result,
    check_requires_confirmation,
    McpClientManager,
)
from llampaca.tools.registry import ToolRegistry


class TestMcpClient(unittest.IsolatedAsyncioTestCase):
    def test_check_requires_confirmation(self):
        # 1. Config override False
        server_cfg = {"requires_confirmation": False}
        self.assertFalse(check_requires_confirmation(server_cfg, "delete_file"))

        # 2. Config override True
        server_cfg = {"requires_confirmation": True}
        self.assertTrue(check_requires_confirmation(server_cfg, "read_file"))

        # 3. List check
        server_cfg = {"requires_confirmation": ["send_email", "create_event"]}
        self.assertTrue(check_requires_confirmation(server_cfg, "send_email"))
        self.assertFalse(check_requires_confirmation(server_cfg, "read_email"))

        # 4. Default heuristic check
        server_cfg = {}
        self.assertTrue(check_requires_confirmation(server_cfg, "delete_email"))
        self.assertTrue(check_requires_confirmation(server_cfg, "write_note"))
        self.assertTrue(check_requires_confirmation(server_cfg, "run_cmd"))
        self.assertFalse(check_requires_confirmation(server_cfg, "get_info"))

    def test_format_mcp_result(self):
        # Mock TextContent
        text_content = MagicMock()
        text_content.text = "Hello world"
        del text_content.json
        del text_content.data

        # Mock result
        result = MagicMock()
        result.content = [text_content]

        formatted = format_mcp_result(result)
        self.assertEqual(formatted, "Hello world")

        # Mock dict
        result_dict = MagicMock()
        result_dict.content = [{"text": "Hello dict"}]
        self.assertEqual(format_mcp_result(result_dict), "Hello dict")

        # Mock generic object fallback
        fallback_item = MagicMock()
        del fallback_item.text
        del fallback_item.json
        del fallback_item.data
        fallback_item.__str__.return_value = "fallback value"

        result_fallback = MagicMock()
        result_fallback.content = [fallback_item]
        self.assertEqual(format_mcp_result(result_fallback), "fallback value")

    @patch("llampaca.engine.mcp_client.stdio_client")
    @patch("llampaca.engine.mcp_client.ClientSession")
    async def test_mcp_client_manager(self, mock_client_session_class, mock_stdio_client):
        # Setup mocks
        mock_read = AsyncMock()
        mock_write = AsyncMock()

        # stdio_client is an async context manager returning (read, write)
        mock_stdio_context = AsyncMock()
        mock_stdio_context.__aenter__.return_value = (mock_read, mock_write)
        mock_stdio_client.return_value = mock_stdio_context

        # ClientSession mock
        mock_session = AsyncMock()
        mock_session_context = AsyncMock()
        mock_session_context.__aenter__.return_value = mock_session
        mock_client_session_class.return_value = mock_session_context

        # Mock tool list returned by server
        mock_mcp_tool = MagicMock()
        mock_mcp_tool.name = "get_emails"
        mock_mcp_tool.description = "List recent emails"
        mock_mcp_tool.inputSchema = {"type": "object", "properties": {}}

        mock_tools_result = MagicMock()
        mock_tools_result.tools = [mock_mcp_tool]
        mock_session.list_tools.return_value = mock_tools_result

        # Config
        mcp_config = {
            "gmail": {
                "command": "npx",
                "args": ["-y", "mcp-gmail"],
                "env": {"GMAIL_TOKEN": "secret_token"},
                "requires_confirmation": False
            }
        }

        # Manager
        manager = McpClientManager(mcp_config)
        registry = ToolRegistry()

        await manager.start(registry)

        # Check tool registration
        self.assertIn("gmail__get_emails", registry.names())
        tool = registry.get("gmail__get_emails")
        self.assertEqual(tool.name, "gmail__get_emails")
        self.assertEqual(tool.description, "List recent emails")
        self.assertFalse(tool.requires_confirmation)

        # Test tool execution wrapper
        mock_tool_result = MagicMock()
        mock_tool_result.isError = False
        text_item = MagicMock()
        text_item.text = "email list content"
        del text_item.json
        del text_item.data
        mock_tool_result.content = [text_item]
        mock_session.call_tool.return_value = mock_tool_result

        exec_result = await registry.execute("gmail__get_emails", "{}")
        self.assertEqual(exec_result, "email list content")
        mock_session.call_tool.assert_called_once_with("get_emails", {})

        # Test tool execution error handling
        mock_error_result = MagicMock()
        mock_error_result.isError = True
        err_item = MagicMock()
        err_item.text = "Access denied"
        del err_item.json
        del err_item.data
        mock_error_result.content = [err_item]
        mock_session.call_tool.reset_mock()
        mock_session.call_tool.return_value = mock_error_result

        exec_result_err = await registry.execute("gmail__get_emails", "{}")
        self.assertIn("Access denied", exec_result_err)

        # Test stopping manager
        await manager.stop()
        self.assertEqual(len(manager.sessions), 0)

    @patch("llampaca.engine.mcp_client.stdio_client")
    @patch("llampaca.engine.mcp_client.ClientSession")
    @patch("asyncio.wait_for")
    async def test_mcp_client_manager_timeout(self, mock_wait_for, mock_client_session_class, mock_stdio_client):
        import asyncio
        async def mock_wait_for_impl(coro, timeout):
            try:
                coro.close()
            except RuntimeError:
                pass
            raise asyncio.TimeoutError()
        mock_wait_for.side_effect = mock_wait_for_impl

        mcp_config = {
            "gmail": {
                "command": "npx",
                "args": ["-y", "mcp-gmail"],
                "env": {"GMAIL_TOKEN": "secret_token"},
                "requires_confirmation": False
            }
        }

        manager = McpClientManager(mcp_config)
        registry = ToolRegistry()

        # Should complete without throwing exceptions as timeout is handled gracefully
        await manager.start(registry)

        # The tool should not be registered since it timed out
        self.assertNotIn("gmail__get_emails", registry.names())

    def test_load_save_mcp_config(self):
        import tempfile
        import os
        from llampaca.config import load_mcp_config, save_mcp_config
        
        fd, temp_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        temp_path_obj = Path(temp_path)
        
        with patch("llampaca.config.MCP_CONFIG_PATH", temp_path_obj):
            if temp_path_obj.exists():
                temp_path_obj.unlink()
                
            cfg = load_mcp_config()
            self.assertIn("mcp_registries", cfg)
            self.assertIn("mcp_servers", cfg)
            self.assertEqual(len(cfg["mcp_servers"]), 0)
            
            cfg["mcp_servers"]["mock_server"] = {"command": "echo"}
            save_mcp_config(cfg)
            
            reloaded = load_mcp_config()
            self.assertIn("mock_server", reloaded["mcp_servers"])
            self.assertEqual(reloaded["mcp_servers"]["mock_server"]["command"], "echo")
            
        if temp_path_obj.exists():
            temp_path_obj.unlink()

    def test_integrations_cli_commands(self):
        from click.testing import CliRunner
        from llampaca.cli import main
        import tempfile
        import os
        
        fd, temp_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        temp_path_obj = Path(temp_path)
        if temp_path_obj.exists():
            temp_path_obj.unlink()
            
        runner = CliRunner()
        
        with patch("llampaca.config.MCP_CONFIG_PATH", temp_path_obj), \
             patch("llampaca.config.load_mcp_config") as mock_load, \
             patch("llampaca.config.save_mcp_config") as mock_save:
             
            mcp_data = {
                "mcp_registries": ["https://glama.ai/api/mcp/v1/servers"],
                "mcp_servers": {}
            }
            mock_load.return_value = mcp_data
            
            def save_side_effect(cfg):
                mcp_data.update(cfg)
            mock_save.side_effect = save_side_effect
            
            # Test repo list
            result = runner.invoke(main, ["integrations", "repo", "list"])
            self.assertEqual(result.exit_code, 0)
            self.assertIn("https://glama.ai/api/mcp/v1/servers", result.output)
            
            # Test repo add
            result = runner.invoke(main, ["integrations", "repo", "add", "https://new-repo.com"])
            self.assertEqual(result.exit_code, 0)
            self.assertIn("https://new-repo.com", mcp_data["mcp_registries"])
            
            # Test repo remove
            result = runner.invoke(main, ["integrations", "repo", "remove", "https://new-repo.com"])
            self.assertEqual(result.exit_code, 0)
            self.assertNotIn("https://new-repo.com", mcp_data["mcp_registries"])
            
            # Test integrations list (empty)
            result = runner.invoke(main, ["integrations", "list"])
            self.assertEqual(result.exit_code, 0)
            self.assertIn("Nessuna integrazione MCP configurata", result.output)
            
            # Test integrations add
            result = runner.invoke(
                main, 
                ["integrations", "add", "mock-server"], 
                input="node\n--version\nn\n"
            )
            self.assertEqual(result.exit_code, 0)
            self.assertIn("mock-server", mcp_data["mcp_servers"])
            self.assertEqual(mcp_data["mcp_servers"]["mock-server"]["command"], "node")
            
            # Test integrations list (non-empty)
            result = runner.invoke(main, ["integrations", "list"])
            self.assertEqual(result.exit_code, 0)
            self.assertIn("mock-server", result.output)
            
            # Test integrations remove
            result = runner.invoke(main, ["integrations", "remove", "mock-server"])
            self.assertEqual(result.exit_code, 0)
            self.assertNotIn("mock-server", mcp_data["mcp_servers"])

            # Test integrations setup with git failure
            with patch("subprocess.run") as mock_sub:
                mock_sub.side_effect = Exception("Git error")
                result = runner.invoke(
                    main, 
                    ["integrations", "setup", "mock-git-server", "--repo", "https://github.com/mock/mock.git"]
                )
                self.assertEqual(result.exit_code, 0)
                self.assertIn("Errore durante la clonazione git", result.output)


if __name__ == "__main__":
    unittest.main()
