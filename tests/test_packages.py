import os
import unittest
from unittest.mock import patch, MagicMock

from llampaca import packages
from llampaca.tools.packages import register_package_tools
from llampaca.tools.registry import ToolRegistry
from llampaca.tools.shell import run_shell_command


class TestPackages(unittest.TestCase):

    def test_package_name_validation(self):
        with self.assertRaises(ValueError):
            packages.install_python_package("invalid_pkg; rm -rf /")

        with self.assertRaises(ValueError):
            packages.install_node_package("invalid_node_pkg && echo 123")

    @patch("llampaca.packages.subprocess.run")
    def test_install_python_package(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Successfully installed", stderr="")
        msg = packages.install_python_package("requests")
        self.assertIn("installato con successo", msg)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("pip", cmd)
        self.assertIn("requests", cmd)

    @patch("llampaca.packages.subprocess.run")
    def test_install_node_package(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="added 1 package", stderr="")
        msg = packages.install_node_package("docx")
        self.assertIn("installato con successo", msg)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("npm", cmd)
        self.assertIn("--prefix", cmd)
        self.assertIn("docx", cmd)

    def test_install_package_tool_registration(self):
        registry = ToolRegistry()
        register_package_tools(registry)
        tool = registry.get("install_package")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.requires_confirmation)

    @patch("llampaca.tools.shell.subprocess.run")
    def test_shell_node_path_injection(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        run_shell_command("echo hello")
        mock_run.assert_called_once()
        env = mock_run.call_args[1].get("env", {})
        self.assertIn("NODE_PATH", env)
        self.assertIn(".llampaca/node_modules", env["NODE_PATH"])


if __name__ == "__main__":
    unittest.main()
