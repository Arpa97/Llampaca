import json
from typing import Any, Dict, List, Tuple, Optional

from llampaca.config import CHARS_PER_TOKEN

MESSAGE_OVERHEAD_TOKENS = 4
MIN_ACTIVE_WINDOW = 6
CONTEXT_TRIM_THRESHOLD = 0.70
GENERATION_RESERVE = 0.15

def estimate_tokens(
    messages: List[Dict[str, Any]],
    tools_enabled: bool = False,
    native_tools: bool = False,
    registry: Any = None
) -> int:
    total = 0
    for message in messages:
        content = message.get("content") or ""
        total += len(content) // CHARS_PER_TOKEN + MESSAGE_OVERHEAD_TOKENS
        if message.get("tool_calls"):
            total += len(json.dumps(message["tool_calls"])) // CHARS_PER_TOKEN
    if tools_enabled and native_tools and registry:
        total += len(json.dumps(registry.definitions())) // CHARS_PER_TOKEN
    return total

def trim_history_to_budget(
    messages: List[Dict[str, Any]],
    context_size: int,
    tools_enabled: bool = False,
    native_tools: bool = False,
    registry: Any = None
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    Drop the oldest conversation turns when the history approaches the
    context limit.
    Returns:
        (dropped_count, removed_messages)
    """
    high_budget = int(context_size * CONTEXT_TRIM_THRESHOLD)
    if estimate_tokens(messages, tools_enabled, native_tools, registry) <= high_budget:
        return 0, []

    low_budget = int(context_size * (CONTEXT_TRIM_THRESHOLD - GENERATION_RESERVE))
    dropped = 0
    removed_messages = []

    while len(messages) > (1 + MIN_ACTIVE_WINDOW) and estimate_tokens(messages, tools_enabled, native_tools, registry) > low_budget:
        removed = messages.pop(1)
        removed_messages.append(removed)
        dropped += 1
        if removed.get("tool_calls"):
            while (
                len(messages) > (1 + MIN_ACTIVE_WINDOW)
                and messages[1].get("role") == "tool"
            ):
                orphaned = messages.pop(1)
                removed_messages.append(orphaned)
                dropped += 1

    return dropped, removed_messages
