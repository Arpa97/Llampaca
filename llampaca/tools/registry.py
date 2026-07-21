"""
Tool registry: turns plain Python functions into agent tools.

A "tool" is a Python function the model is allowed to call during the agent
loop. The registry does two jobs:

1. Introspects each registered function (type hints + docstring) and builds
   the JSON Schema definition that llama-server expects in the OpenAI
   "tools" request parameter — so tool authors just write a normal,
   well-documented Python function and get the schema for free.

2. Executes a tool by name with JSON-encoded arguments coming back from the
   model, converting any failure (unknown tool, malformed JSON, exception
   inside the tool) into an error *string*. Errors are returned to the model
   as the tool result instead of crashing the loop, so the model can read
   the error and try a different approach.
"""

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# Mapping from Python type annotations to JSON Schema type names.
# Tools should only use these simple types as parameters: the model fills
# arguments from a JSON schema, so complex Python types would not survive
# the round-trip anyway.
_PYTHON_TYPE_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}

# Default cap on the size of a tool result sent back to the model.
# Local models have small context windows (a few thousand tokens), so a
# single huge tool result (e.g. reading a big file) could evict the whole
# conversation. Anything longer is truncated with an explicit marker so the
# model knows the output is partial.
#
# This is only the fallback for registries built without an explicit cap:
# the CLI passes a cap proportional to the actual context size instead
# (see build_default_registry / cli.py), so bigger contexts allow bigger
# tool results and smaller contexts stay protected.
MAX_TOOL_RESULT_CHARS = 8000


def _parse_docstring(doc: Optional[str]):
    """
    Extract (description, {param_name: param_description}) from a docstring.

    Supports the Google style "Args:" section, e.g.:

        Read a file from the workspace.

        Args:
            path: Relative path of the file to read.

    Everything before "Args:" becomes the tool description; each indented
    "name: text" line inside the section becomes a parameter description.
    """
    if not doc:
        return "", {}

    lines = doc.strip().splitlines()
    description_lines: List[str] = []
    param_docs: Dict[str, str] = {}
    in_args_section = False
    current_param = None

    for line in lines:
        stripped = line.strip()
        if stripped.lower() in ("args:", "arguments:", "parameters:"):
            in_args_section = True
            continue
        # A new unindented section header (e.g. "Returns:") ends the Args section
        if in_args_section and stripped.endswith(":") and ":" not in stripped[:-1] and not line.startswith((" ", "\t")):
            in_args_section = False
            continue

        if in_args_section:
            if ":" in stripped:
                # New "param: description" entry
                name, _, text = stripped.partition(":")
                current_param = name.strip()
                param_docs[current_param] = text.strip()
            elif current_param and stripped:
                # Continuation line of the previous parameter description
                param_docs[current_param] += " " + stripped
        else:
            description_lines.append(stripped)

    description = " ".join(l for l in description_lines if l).strip()
    return description, param_docs


@dataclass
class Tool:
    """A single registered tool: callable + its OpenAI-format definition."""
    name: str
    description: str
    func: Callable
    parameters: dict = field(default_factory=dict)
    # When True, the agent loop asks the user for confirmation before
    # executing (used for destructive/dangerous tools like shell commands
    # and file writes).
    requires_confirmation: bool = False

    def to_openai_format(self) -> dict:
        """Return the tool definition in the OpenAI 'tools' request format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Holds the set of tools available to an Agent and executes them."""

    def __init__(self, max_result_chars: int = MAX_TOOL_RESULT_CHARS):
        """
        Args:
            max_result_chars: Hard cap (in characters) on the size of a tool
                result returned to the model; longer results are truncated
                with an explicit marker. Callers that know the model's
                context size should scale this accordingly (a character is
                roughly a quarter of a token).
        """
        self._tools: Dict[str, Tool] = {}
        self.max_result_chars = max_result_chars

    def register(
        self,
        func: Callable,
        *,
        name: str = None,
        description: str = None,
        requires_confirmation: bool = False,
    ) -> Tool:
        """
        Register a Python function as a tool.

        The JSON Schema for the tool parameters is generated automatically:
        - parameter types come from the function's type hints
        - the tool description comes from the docstring body
        - per-parameter descriptions come from the docstring "Args:" section
        - parameters without a default value are marked as required

        Args:
            func: The function to expose to the model.
            name: Tool name shown to the model (defaults to the function name).
            description: Override for the docstring description.
            requires_confirmation: Ask the user before executing this tool.
        """
        tool_name = name or func.__name__
        doc_description, param_docs = _parse_docstring(func.__doc__)

        # Build the JSON Schema "properties" object from the signature
        properties: Dict[str, dict] = {}
        required: List[str] = []
        signature = inspect.signature(func)

        for param_name, param in signature.parameters.items():
            annotation = param.annotation
            json_type = _PYTHON_TYPE_TO_JSON.get(annotation, "string")
            prop: dict = {"type": json_type}
            if param_name in param_docs:
                prop["description"] = param_docs[param_name]
            properties[param_name] = prop
            # No default value means the model must always provide it
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        tool = Tool(
            name=tool_name,
            description=description or doc_description,
            func=func,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
            },
            requires_confirmation=requires_confirmation,
        )
        self._tools[tool_name] = tool
        return tool

    def get(self, name: str) -> Optional[Tool]:
        """Look up a tool by name, or None if not registered."""
        return self._tools.get(name)

    def names(self) -> List[str]:
        """Names of all registered tools (used to build the system prompt)."""
        return list(self._tools.keys())

    def definitions(self) -> List[dict]:
        """All tool definitions in the OpenAI 'tools' request format."""
        return [tool.to_openai_format() for tool in self._tools.values()]

    async def execute(self, name: str, arguments_json: str) -> str:
        """
        Execute a tool by name with the JSON arguments produced by the model.

        Always returns a string (the tool result, or an error message the
        model can react to). Never raises: a broken tool call must not kill
        the agent loop.
        """
        tool = self.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'. Available tools: {', '.join(self.names())}"

        # The model produces arguments as a JSON string; it can occasionally
        # emit malformed JSON, which we report back instead of crashing.
        try:
            arguments = json.loads(arguments_json) if arguments_json.strip() else {}
        except json.JSONDecodeError as e:
            return f"Error: could not parse tool arguments as JSON ({e}). Arguments were: {arguments_json!r}"

        if not isinstance(arguments, dict):
            return f"Error: tool arguments must be a JSON object, got: {arguments_json!r}"

        try:
            if inspect.iscoroutinefunction(tool.func):
                result = await tool.func(**arguments)
            else:
                result = await asyncio.to_thread(tool.func, **arguments)
                if inspect.isawaitable(result):
                    result = await result
        except TypeError as e:
            # Wrong/missing parameters — tell the model what the schema expects
            return f"Error: invalid arguments for tool '{name}': {e}"
        except Exception as e:
            # Any runtime failure inside the tool is reported as a result
            return f"Error while executing tool '{name}': {type(e).__name__}: {e}"

        result_str = str(result)
        # Truncate oversized results to protect the small local context window
        if len(result_str) > self.max_result_chars:
            result_str = (
                result_str[:self.max_result_chars]
                + f"\n... [truncated: output was {len(result_str)} characters]"
            )
        return result_str
