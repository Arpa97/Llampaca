import re

with open("/tmp/loop_full.py", "r") as f:
    text = f.read()

# 1. Imports
imports = """
from llampaca.agent.context import estimate_tokens, trim_history_to_budget
from llampaca.agent.prompts import build_base_system_prompt, build_system_prompt_content, DEFAULT_SYSTEM_PROMPT
from llampaca.agent.executor import execute_with_confirmation, extract_tool_call, clean_tool_call_text, DECLINED_MARKER
"""
text = text.replace("from llampaca.tools.registry import ToolRegistry\n", "from llampaca.tools.registry import ToolRegistry\n" + imports)

# 2. Remove constants
text = re.sub(r"DECLINED_MARKER = \"\[DECLINED\]\"\n", "", text)
text = re.sub(r"MIN_ACTIVE_WINDOW = 6\n", "", text)
text = re.sub(r"CONTEXT_HIGH_WATERMARK = 0.80.*?\n", "", text)
text = re.sub(r"CONTEXT_LOW_WATERMARK = 0.60.*?\n", "", text)
text = re.sub(r"MESSAGE_OVERHEAD_TOKENS = 4\n", "", text)
text = re.sub(r"DEFAULT_SYSTEM_PROMPT = \([\s\S]*?\n\)\n\n", "", text)

# 3. In __init__, replace base prompt logic
old_init_prompt = """        base_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        today = datetime.now().strftime("%A, %d %B %Y")
        year = datetime.now().year
        # The tool-free part of the system prompt, kept on the instance so
        # the full prompt can be REBUILT whenever the tool set or the tool
        # mode changes mid-session (refresh_tools, _switch_to_prompt_mode).
        # Rebuilding from this base — instead of appending to messages[0] —
        # keeps those operations idempotent: no duplicated tool sections.
        self._base_system_prompt = (
            f"{base_prompt} "
            f"Today's date is {today}. The current year is {year}. "
            f"La data di oggi è: {today}. L'anno corrente è {year}. "
            f"When searching the web, ALWAYS include the current year ({year}) in the query "
            f"if the user's question refers to recent or current events."
        )"""
new_init_prompt = "        self._base_system_prompt = build_base_system_prompt(system_prompt)"
text = text.replace(old_init_prompt, new_init_prompt)

# 4. _trim_history replacement
old_trim_call = "            self._trim_history()"
new_trim_call = """            dropped, removed = trim_history_to_budget(self.messages, self.context_size, self.tools_enabled, self.native_tools, self.registry)
            if removed:
                for msg in removed:
                    if msg.get("id") is not None:
                        self._pending_summary_last_id = msg["id"]
                self._pending_summary_turns.extend(m for m in removed if m["role"] in ("user", "assistant"))"""
text = text.replace(old_trim_call, new_trim_call)

# 5. _extract_tool_call
text = text.replace("self._extract_tool_call(", "extract_tool_call(")
text = text.replace("extract_tool_call(text)", "extract_tool_call(text, self.registry)")
text = text.replace("extract_tool_call(accumulated_reasoning)", "extract_tool_call(accumulated_reasoning, self.registry)")
text = text.replace("extract_tool_call(text_content)", "extract_tool_call(text_content, self.registry)")

# 6. _execute_with_confirmation
text = text.replace("self._execute_with_confirmation(", "execute_with_confirmation(self.registry, self.confirm, ")

# 7. _clean_tool_call_text
text = text.replace("self._clean_tool_call_text(", "clean_tool_call_text(")

# 8. context_usage
old_context_usage = """    def context_usage(self) -> Tuple[int, int]:
        \"\"\"
        Report the estimated context occupancy for UI display.

        Returns:
            (estimated_used_tokens, context_size) — the caller can derive a
            percentage from these. The estimate is heuristic (chars/4), so
            it should be presented as approximate.
        \"\"\"
        return self._estimate_tokens(), self.context_size"""
new_context_usage = """    def context_usage(self) -> Tuple[int, int]:
        return estimate_tokens(self.messages, self.tools_enabled, self.native_tools, self.registry), self.context_size"""
text = text.replace(old_context_usage, new_context_usage)

# 9. _system_prompt_content
old_system_prompt_content = """    def _system_prompt_content(self) -> str:
        \"\"\"
        The full system prompt for the CURRENT tool mode and tool set:
        the dated base prompt plus, in prompt-based mode only, the tool
        instructions (in native mode the tools travel as a request
        parameter instead). Single source of truth for messages[0] —
        __init__, refresh_tools() and _switch_to_prompt_mode() all build
        it from here, so mode/tool changes can never stack duplicates.
        \"\"\"
        content = self._base_system_prompt
        if self.tools_enabled and not self.native_tools:
            content += "\\n\\n" + self._tool_instructions()
        return content"""
new_system_prompt_content = """    def _system_prompt_content(self) -> str:
        return build_system_prompt_content(self._base_system_prompt, self.tools_enabled, self.native_tools, self.registry)"""
text = text.replace(old_system_prompt_content, new_system_prompt_content)

lines = text.split("\n")
new_lines = []
skip = False
for i, line in enumerate(lines):
    # detect method start
    if line.startswith("    def _estimate_tokens(self)"): skip = True
    elif line.startswith("    def _trim_history(self)"): skip = True
    elif line.startswith("    def _tool_instructions(self)"): skip = True
    elif line.startswith("    def _extract_tool_call(self"): skip = True
    elif line.startswith("    async def _execute_with_confirmation("): skip = True
    elif line.startswith("    @staticmethod"):
        # look ahead to see what it decorates
        if i+1 < len(lines):
            if lines[i+1].startswith("    def _clean_tool_call_text("): skip = True
            elif lines[i+1].startswith("    def _format_confirmation("): skip = True
    
    # if we are skipping, when do we stop?
    # we stop when we see a line starting with exactly 4 spaces and a new def/class/@ or #
    if skip and line.startswith("    ") and not line.startswith("        ") and line.strip() != "":
        # this could be a new method
        if line.startswith("    def ") or line.startswith("    async def ") or line.startswith("    @") or line.startswith("    #") or line.startswith("    @property"):
            # Check if this new line is ALSO one of the methods we want to remove
            if any(line.startswith(m) for m in [
                "    def _estimate_tokens", "    def _trim_history", 
                "    def _tool_instructions", "    def _extract_tool_call",
                "    async def _execute_with_confirmation", "    def _clean_tool_call_text",
                "    def _format_confirmation", "    @staticmethod"
            ]):
                pass # keep skipping
            else:
                skip = False
    
    if not skip:
        new_lines.append(line)

with open("llampaca/agent/loop.py", "w") as f:
    f.write("\n".join(new_lines))

print("loop.py refactored successfully.")
