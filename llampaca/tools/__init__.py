# Tools package: built-in agent tools and the registry that exposes them
# to the model in OpenAI-compatible "tools" format.
#
# Public API:
#   - ToolRegistry / Tool: register Python functions as agent tools
#   - build_default_registry(): registry pre-loaded with the built-in tools
#     (filesystem, shell, web)

from llampaca.tools.registry import Tool, ToolRegistry
from llampaca.tools.filesystem import register_filesystem_tools
from llampaca.tools.shell import register_shell_tools
from llampaca.tools.web import register_web_tools


def build_default_registry() -> ToolRegistry:
    """
    Create a ToolRegistry pre-loaded with all built-in tools.

    This is the standard set used by the interactive CLI chat. Callers that
    want a custom tool set (e.g. tests, or a future GUI with different
    capabilities) can build their own registry and register tools manually.
    """
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    register_shell_tools(registry)
    register_web_tools(registry)
    return registry


__all__ = ["Tool", "ToolRegistry", "build_default_registry"]
