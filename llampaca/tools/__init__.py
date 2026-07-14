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


def build_default_registry(max_result_chars: int = None) -> ToolRegistry:
    """
    Create a ToolRegistry pre-loaded with all built-in tools.

    This is the standard set used by the interactive CLI chat. Callers that
    want a custom tool set (e.g. tests, or a future GUI with different
    capabilities) can build their own registry and register tools manually.

    Args:
        max_result_chars: Cap on the size (in characters) of a single tool
            result returned to the model. None keeps the registry's default.
            The CLI derives this from the model's context size so that one
            tool result can never swamp the whole context window.
    """
    if max_result_chars is not None:
        registry = ToolRegistry(max_result_chars=max_result_chars)
    else:
        registry = ToolRegistry()
    register_filesystem_tools(registry)
    register_shell_tools(registry)
    register_web_tools(registry)
    return registry


__all__ = ["Tool", "ToolRegistry", "build_default_registry"]
