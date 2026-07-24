# Tools package: built-in agent tools and the registry that exposes them
# to the model in OpenAI-compatible "tools" format.
#
# Public API:
#   - ToolRegistry / Tool: register Python functions as agent tools
#   - build_default_registry(): registry pre-loaded with the built-in tools
#     (filesystem, shell, web, wiki)

from llampaca.tools.registry import Tool, ToolRegistry
from llampaca.tools.filesystem import register_filesystem_tools
from llampaca.tools.shell import register_shell_tools
from llampaca.tools.web import register_web_tools
from llampaca.tools.wiki import register_wiki_tools
from llampaca.tools.skills import register_skills_tools
from llampaca.tools.packages import register_package_tools
from llampaca.tools.image import register_image_tools
# search_documents is exported but deliberately NOT part of
# build_default_registry: it is registered dynamically, only for sessions
# whose conversation has indexed attachments (see tools/documents.py).
from llampaca.tools.documents import register_document_tools, make_query_embedder


def register_custom_tools(registry) -> None:
    """
    Load custom Python functions defined in ~/.llampaca/custom_tools/
    and register them on the given ToolRegistry.
    """
    from llampaca.config import LLAMPACA_DIR
    import importlib.util
    import sys
    import inspect

    custom_dir = LLAMPACA_DIR / "custom_tools"
    custom_dir.mkdir(parents=True, exist_ok=True)

    for path in custom_dir.glob("*.py"):
        if path.name.startswith("_"):
            continue
        try:
            module_name = f"llampaca_custom_{path.stem}"
            # Evict cached module to force reload on subsequent calls
            if module_name in sys.modules:
                del sys.modules[module_name]
            
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

                for name, func in inspect.getmembers(module, inspect.isfunction):
                    if func.__module__ == module_name and not name.startswith("_"):
                        req_conf = getattr(func, "requires_confirmation", False)
                        registry.register(func, requires_confirmation=req_conf)
        except Exception as e:
            print(f"Error loading custom tool {path.name}: {e}", flush=True)


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
    # The wiki (persistent cross-session memory) is always available:
    # unlike search_documents it is global, not tied to one conversation.
    register_wiki_tools(registry)
    register_skills_tools(registry)
    register_package_tools(registry)
    register_image_tools(registry)
    # Load custom user-defined tools
    try:
        register_custom_tools(registry)
    except Exception as e:
        print(f"Failed to load custom tools: {e}", flush=True)
    return registry


__all__ = [
    "Tool",
    "ToolRegistry",
    "build_default_registry",
    "register_document_tools",
    "make_query_embedder",
    "register_wiki_tools",
    "register_custom_tools",
]
