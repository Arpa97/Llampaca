import inspect
import json
from typing import Callable, Any, Optional

DECLINED_MARKER = "[DECLINED]"

def format_confirmation(name: str, arguments_json: str) -> str:
    try:
        args = json.loads(arguments_json, strict=False)
    except (json.JSONDecodeError, TypeError):
        return f"The agent wants to run tool '{name}' with arguments: {arguments_json}"

    if not isinstance(args, dict) or not args:
        return f"The agent wants to run tool '{name}' with arguments: {arguments_json}"

    lines = [f"The agent wants to run tool '{name}':"]
    for key, value in args.items():
        text = value if isinstance(value, str) else json.dumps(value)
        if "\n" in text:
            indented = "\n".join(f"    | {ln}" for ln in text.split("\n"))
            lines.append(f"  {key}:\n{indented}")
        else:
            lines.append(f"  {key}: {text}")
    return "\n".join(lines)

async def execute_with_confirmation(
    registry: Any,
    confirm: Optional[Callable[[str], Any]],
    name: str,
    arguments_json: str
) -> str:
    tool = registry.get(name)

    if tool is not None and tool.requires_confirmation:
        if confirm is None:
            return (
                f"Tool '{name}' requires user confirmation, but no "
                "confirmation mechanism is available. Action not executed."
            )
        prompt = format_confirmation(name, arguments_json)

        try:
            sig = inspect.signature(confirm)
            num_params = len(sig.parameters)
        except Exception:
            num_params = 1

        if inspect.iscoroutinefunction(confirm):
            if num_params >= 3:
                confirmed = await confirm(prompt, name, arguments_json)
            else:
                confirmed = await confirm(prompt)
        else:
            if num_params >= 3:
                confirmed = confirm(prompt, name, arguments_json)
            else:
                confirmed = confirm(prompt)

        if not confirmed:
            return f"{DECLINED_MARKER} L'utente ha rifiutato l'autorizzazione per eseguire l'operazione '{name}'."

    return await registry.execute(name, arguments_json)
import re
from typing import Tuple

def extract_tool_call(text: str, registry: Any) -> Optional[Tuple[str, str]]:
    stripped = text.strip()
    candidates = []

    xml_tag = re.search(r"<tool_call>\s*(\{.*?\})(?:\s*</tool_call>|\s*$)", stripped, re.S)
    if xml_tag:
        candidates.append(xml_tag.group(1))

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.S)
    if fence:
        candidates.append(fence.group(1))

    first, last = stripped.find("{"), stripped.rfind("}")
    if first != -1 and last > first:
        candidates.append(stripped[first:last + 1])

    for candidate in candidates:
        try:
            obj = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(obj, dict)
            and isinstance(obj.get("name"), str)
            and registry.get(obj["name"]) is not None
            and isinstance(obj.get("arguments", {}), dict)
        ):
            return obj["name"], json.dumps(obj.get("arguments", {}))
    return None

def clean_tool_call_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    cleaned = re.sub(r"<tool_call>\s*\{.*?\}\s*</tool_call>", "", text, flags=re.S)
    cleaned = re.sub(r"<tool_call>.*", "", cleaned, flags=re.S)
    cleaned = re.sub(r"```(?:json)?\s*\{\s*\"name\"\s*:.*?\s*\}\s*```", "", cleaned, flags=re.S)
    cleaned = cleaned.strip()
    return cleaned if cleaned else None
