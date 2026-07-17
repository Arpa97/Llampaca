"""
The agentic execution loop.

This is the core of Llampaca as an *agent* rather than a plain chatbot:

    1. Send the conversation to the model together with the available tool
       definitions.
    2. The model answers with text, with tool calls, or both.
    3. Execute each requested tool locally (asking the user for
       confirmation when the tool is flagged as dangerous), append the
       results to the conversation, and go back to 1 — until the model
       produces a final text answer or the iteration cap is reached.

Design rule: this module is UI-independent. It never prints, never prompts.
It communicates with the outside world by *yielding events* (see send()),
and asks for confirmation through an injected callback. The CLI renders
these events in the terminal today; a future GUI or local HTTP API will
render the exact same events differently.

Tool-calling modes
------------------
Not every GGUF ships a chat template that understands tools, so the agent
supports two modes and picks one automatically:

- NATIVE (e.g. the Qwen family): the model's chat template renders the
  OpenAI "tools" request parameter and llama-server parses the model's
  reply into structured `tool_calls`. Most reliable — the server both
  formats and parses the exchange.

- PROMPT-BASED (e.g. Gemma): the template has no tool support, so passing
  "tools" to the API silently drops them (the model then *hallucinates*
  results). Instead, tool definitions are injected into the system prompt,
  the model is asked to reply with a single JSON object when it wants a
  tool, and we parse that JSON out of the reply text ourselves. Tool
  results go back as user messages, because these templates reject the
  "tool" role.

The mode is auto-detected at startup by inspecting the server's chat
template (llama-server's /props endpoint); if native mode turns out to be
unsupported at runtime anyway (first request rejected), the agent switches
to prompt-based mode on the fly instead of giving up on tools.
"""

import inspect
import json
import re
from datetime import datetime
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

from llampaca.engine.client import LlamaClient
from llampaca.tools.registry import ToolRegistry

# Safety cap: maximum model→tools→model round-trips for a single user
# message. Prevents a confused model from looping on tool calls forever.
MAX_ITERATIONS = 10

# Minimum number of recent messages (turns) that are guaranteed to remain
# in the active context window and never get trimmed/summarized.
MIN_ACTIVE_WINDOW = 6

# --- Context budget management -----------------------------------------
# The conversation history grows forever, but the model's context window is
# fixed (llama-server's -c). If the prompt outgrows it, llama-server starts
# dropping tokens from the *front* of the prompt — which is the system
# prompt, i.e. the agent's identity and (in prompt-based tool mode) the tool
# instructions themselves. To prevent that, the agent trims its own history
# before each request, always keeping the system prompt pinned.
#
# Trimming uses two watermarks (hysteresis) instead of one threshold on
# purpose: llama-server caches the KV state of the common prompt prefix
# between requests, and every trim changes that prefix, forcing a full
# re-computation of the prompt. Trimming down to a *lower* watermark in one
# go means the prefix then stays stable for many turns (cache hits) before
# the next trim, instead of invalidating the cache on every single turn.
CONTEXT_HIGH_WATERMARK = 0.80  # trim when the history exceeds this fraction
CONTEXT_LOW_WATERMARK = 0.60   # ...and cut it down to this fraction

# Rough tokens-per-character ratio used for budget estimates. Exact token
# counts would require a round-trip to the server's /tokenize endpoint per
# message; a chars/4 estimate is standard, cheap, and accurate enough for
# watermark decisions (the 20% headroom above the high watermark absorbs
# the estimation error).
CHARS_PER_TOKEN = 4

# Fixed per-message overhead, in tokens: every message costs a few extra
# tokens for its role marker and the chat template's framing around it.
MESSAGE_OVERHEAD_TOKENS = 4

DEFAULT_SYSTEM_PROMPT = (
    "You are Llampaca, a helpful local AI personal assistant running entirely "
    "on the user's machine. You can use the available tools to read and write "
    "files in the user's workspace, run shell commands, search the web, and "
    "fetch web pages. When the user asks about current events or facts that may "
    "have changed since your training, use web_search to find up-to-date "
    "information rather than answering from memory, and cite what you found. "
    # Language models tokenize text, so they cannot reliably see individual
    # characters and are notoriously bad at counting them or at exact
    # arithmetic. The shell can do both perfectly, so we steer the model to
    # compute these answers instead of guessing them.
    "You cannot see individual characters in text and you cannot do reliable "
    "arithmetic in your head. For anything that must be exact — counting "
    "characters, letters, words or lines, reversing or sorting text, and "
    "arithmetic — do not guess: use run_shell_command to compute the answer, "
    "then report the computed result. "
    "Use tools when they help you answer accurately; answer directly when you "
    "don't need them. Be concise."
)


class Agent:
    """
    A stateful conversation agent: holds the message history and runs the
    tool-execution loop for each user input.
    """

    def __init__(
        self,
        client: LlamaClient,
        registry: Optional[ToolRegistry] = None,
        system_prompt: Optional[str] = None,
        # May be sync (returns bool) or async (returns an awaitable bool).
        confirm: Optional[Callable[[str], Any]] = None,
        model: str = "local-model",
        max_iterations: int = MAX_ITERATIONS,
        context_size: int = 32768,
        summary: Optional[str] = None,
    ):
        """
        Args:
            client: Connected LlamaClient for the running llama-server.
            registry: Tools available to the model. None/empty means the
                agent degrades gracefully to a plain chatbot.
            system_prompt: Override for the default system prompt.
            confirm: Callback invoked before executing a tool flagged with
                requires_confirmation. Receives a human-readable description
                of the action, returns True to proceed. If None, dangerous
                tools are *never* executed (safe default for non-interactive
                usage).
            model: Model name passed through to the server (informational
                for llama-server, which serves a single model).
            max_iterations: Cap on tool round-trips per user message.
            context_size: The model's context window in tokens (llama-server's
                -c value). Used to trim old history before it overflows and
                to report context usage to the UI.
            summary: Optional conversation summary loaded from the database.
        """
        self.client = client
        self.registry = registry
        self.confirm = confirm
        self.model = model
        self.max_iterations = max_iterations
        self.context_size = context_size
        self.summary = summary
        self.tools_enabled = registry is not None and len(registry.names()) > 0

        # Becomes True the first time a native-tools request succeeds. Once
        # tools are proven to work, a later streaming error can no longer be
        # "the template rejected the tools parameter" (that failure happens
        # on the very first request, before any content is produced).
        self._tools_confirmed_working = False

        # --- Detect the tool-calling mode (see module docstring) ---------
        # A template that never mentions tools cannot render the native
        # "tools" parameter: llama-server would silently drop the tool
        # definitions and the model would hallucinate results.
        self.native_tools = True
        if self.tools_enabled:
            try:
                template = self.client.get_chat_template()
                if template:
                    self.native_tools = "tool" in template.lower()
            except Exception:
                # Can't inspect the server (older build, test double, ...):
                # assume native and rely on the runtime fallback in send().
                self.native_tools = True

        # Local models have old training cutoffs and no clock: without this
        # they guess the date (and e.g. build web searches around the wrong
        # year). Appending today's date to the system prompt fixes that for
        # free. Applied to custom prompts too, since the problem is the same.
        base_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        today = datetime.now().strftime("%A, %d %B %Y")
        dated_prompt = f"{base_prompt} Today's date is {today}."

        # In prompt-based mode the tool definitions live in the system prompt
        if self.tools_enabled and not self.native_tools:
            dated_prompt += "\n\n" + self._tool_instructions()

        # Full conversation history, in OpenAI messages format.
        # Kept on the instance so multiple send() calls form one conversation.
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": dated_prompt}
        ]

    async def send(
        self,
        user_input: str,
        message_id: Optional[int] = None,
    ) -> AsyncGenerator[Tuple[str, Any], None]:
        """
        Process one user message through the agent loop, yielding events as
        they happen so the UI can render progress live.

        Events yielded (as (kind, data) tuples):
            ("text", str)             — chunk of assistant text, render as it arrives
            ("tool_call", dict)       — the model requested a tool:
                                         {"name": ..., "arguments": <json str>}
            ("tool_result", dict)     — a tool finished:
                                         {"name": ..., "result": <str>}
            ("warning", str)          — non-fatal problem (e.g. tool mode switched,
                                         iteration cap reached)
            ("error", str)            — the model/server failed this turn; the
                                         session stays alive so the user can retry
            ("summary_updated", dict) — the conversation summary was updated:
                                         {"summary": <str>, "last_summarized_message_id": <int>}
        """
        self.messages.append({"role": "user", "content": user_input, "id": message_id})

        for _ in range(self.max_iterations):
            # Keep the history inside the context budget before *every*
            # request, not just once per user message: tool results appended
            # mid-turn can overflow the window too.
            dropped, new_summary, last_id = await self._trim_history()
            if dropped > 0 and new_summary is not None:
                yield ("summary_updated", {"summary": new_summary, "last_summarized_message_id": last_id})

            prompt_mode = self.tools_enabled and not self.native_tools
            tools = (
                self.registry.definitions()
                if self.tools_enabled and self.native_tools
                else None
            )

            # Build messages_to_send: sanitizing keys to only standard OpenAI fields,
            # and injecting the summary into the system prompt.
            messages_to_send = []
            for i, msg in enumerate(self.messages):
                clean_msg = {"role": msg["role"], "content": msg["content"]}
                if "tool_calls" in msg:
                    clean_msg["tool_calls"] = msg["tool_calls"]
                if "name" in msg:
                    clean_msg["name"] = msg["name"]
                if "tool_call_id" in msg:
                    clean_msg["tool_call_id"] = msg["tool_call_id"]
                
                # Inject summary in system prompt
                if i == 0 and self.summary:
                    clean_msg["content"] = msg["content"] + (
                        "\n\n[Nota: Di seguito un riassunto della parte precedente della conversazione, "
                        f"archiviata per ragioni di spazio. Usala come contesto se necessario:\n{self.summary}]"
                    )
                messages_to_send.append(clean_msg)

            # --- 1. Query the model (streaming) -------------------------
            assistant_message: Optional[Dict[str, Any]] = None
            produced_text = False  # did we stream any assistant text this call?
            # In prompt mode a tool call arrives as JSON *text*. We hold the
            # stream back while the output still looks like JSON (so the raw
            # call is never shown to the user), but flush and stream live as
            # soon as it is clearly prose.
            buffer = ""
            buffering = prompt_mode
            try:
                async for kind, data in self.client.chat_stream_events(
                    messages_to_send, model=self.model, tools=tools
                ):
                    if kind == "text":
                        produced_text = True
                        if buffering:
                            buffer += data
                            head = buffer.lstrip()
                            if head and not head.startswith(("{", "`")):
                                buffering = False  # plain prose: stream it
                                yield ("text", buffer)
                                buffer = ""
                        else:
                            yield ("text", data)
                    elif kind == "message":
                        assistant_message = data
                # A request that included native tools and completed without
                # error proves the template accepts the tools parameter.
                if tools is not None:
                    self._tools_confirmed_working = True
            except Exception as e:
                # A native-tools request rejected on FIRST contact (no text
                # produced, tools never seen working) means the template
                # can't handle the tools parameter: switch to prompt-based
                # mode and retry the same turn. Any other failure (compute/
                # GPU-memory error, dropped connection, ...) is reported and
                # the turn ends gracefully — the session stays alive.
                looks_like_tool_rejection = (
                    tools is not None
                    and not self._tools_confirmed_working
                    and not produced_text
                )
                if looks_like_tool_rejection:
                    self._switch_to_prompt_mode()
                    yield (
                        "warning",
                        f"The model rejected native tool calling ({e}). "
                        "Switching to prompt-based tools for this session.",
                    )
                    continue  # retry the same turn in prompt mode
                yield ("error", self._describe_error(e))
                return

            if assistant_message is None:
                # Defensive: the stream ended without a final message event
                yield ("warning", "The model returned an empty response.")
                return

            # --- 2a. Prompt-based mode: parse a tool call out of the text
            if prompt_mode:
                text = assistant_message.get("content") or ""
                # Only text that stayed JSON-looking to the end (still
                # buffered) can be a tool call; flushed prose is an answer.
                call = self._extract_tool_call(text) if buffering else None

                # The model's literal reply goes into the history either way,
                # so it can see its own (attempted) call next round.
                self.messages.append({"role": "assistant", "content": text})

                if call is None:
                    if buffer:
                        # JSON-looking but not a valid tool call: it's just
                        # the model's answer — show it after all.
                        yield ("text", buffer)
                    return  # final answer for this turn

                name, arguments_json = call
                yield ("tool_call", {"name": name, "arguments": arguments_json})
                result = await self._execute_with_confirmation(name, arguments_json)
                yield ("tool_result", {"name": name, "result": result})
                # Templates without tool support reject the "tool" role, so
                # the result is fed back as a clearly-labeled user message.
                self.messages.append({
                    "role": "user",
                    "content": f"[Result of tool '{name}']\n{result}",
                })
                continue  # let the model see the result

            # --- 2b. Native mode: structured tool_calls from the server -
            self.messages.append(assistant_message)

            tool_calls = assistant_message.get("tool_calls")
            if not tool_calls:
                return  # no tools requested: final answer for this turn

            for tool_call in tool_calls:
                name = tool_call["function"]["name"]
                arguments_json = tool_call["function"]["arguments"]
                yield ("tool_call", {"name": name, "arguments": arguments_json})

                result = await self._execute_with_confirmation(name, arguments_json)
                yield ("tool_result", {"name": name, "result": result})

                # Feed the result back to the model, linked to its call id
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result,
                })

            # --- 3. Loop: let the model see the results and continue ----

        # Iteration cap reached: tell the user rather than silently stopping
        yield (
            "warning",
            f"Stopped after {self.max_iterations} tool iterations without a "
            "final answer. You can ask the model to continue.",
        )

    # ------------------------------------------------------------------
    # Context budget helpers
    # ------------------------------------------------------------------

    def _estimate_tokens(self) -> int:
        """
        Estimate how many tokens the next request will occupy in the model's
        context window: the full message history plus, in native tool mode,
        the tool definitions the chat template renders into the prompt.

        Uses the chars/4 heuristic (see CHARS_PER_TOKEN) — cheap and close
        enough for watermark decisions; no server round-trip needed.
        """
        total = 0
        for message in self.messages:
            content = message.get("content") or ""
            total += len(content) // CHARS_PER_TOKEN + MESSAGE_OVERHEAD_TOKENS
            # Assistant messages can carry structured tool calls (native
            # mode); their JSON is rendered into the prompt too.
            if message.get("tool_calls"):
                total += len(json.dumps(message["tool_calls"])) // CHARS_PER_TOKEN
        # In native mode the tool definitions travel with every request and
        # the template renders them into the prompt. (In prompt-based mode
        # they are already inside the system prompt, counted above.)
        if self.tools_enabled and self.native_tools:
            total += (
                len(json.dumps(self.registry.definitions())) // CHARS_PER_TOKEN
            )
        return total

    def context_usage(self) -> Tuple[int, int]:
        """
        Report the estimated context occupancy for UI display.

        Returns:
            (estimated_used_tokens, context_size) — the caller can derive a
            percentage from these. The estimate is heuristic (chars/4), so
            it should be presented as approximate.
        """
        return self._estimate_tokens(), self.context_size

    async def _trim_history(self) -> Tuple[int, Optional[str], Optional[int]]:
        """
        Drop the oldest conversation turns when the history approaches the
        context limit, so llama-server never truncates the prompt itself
        (which would eat the system prompt first — fatal in prompt-based
        tool mode, where it carries the tool instructions).

        Hysteresis: trimming only starts above CONTEXT_HIGH_WATERMARK, but
        then cuts all the way down to CONTEXT_LOW_WATERMARK. This trades one
        big prompt-cache invalidation every N turns for stable cache hits in
        between (see the watermark constants for the full rationale).

        Never dropped: the system prompt (messages[0]), the most recent messages
        guaranteed by MIN_ACTIVE_WINDOW, and the current input.

        Returns:
            Tuple of (dropped_count, new_summary, last_summarized_id)
        """
        high_budget = int(self.context_size * CONTEXT_HIGH_WATERMARK)
        if self._estimate_tokens() <= high_budget:
            return 0, None, None

        low_budget = int(self.context_size * CONTEXT_LOW_WATERMARK)
        dropped = 0
        removed_messages = []
        
        # messages[0] is system prompt; the last MIN_ACTIVE_WINDOW messages are untouchable
        # to ensure recent conversation context remains intact.
        while len(self.messages) > (1 + MIN_ACTIVE_WINDOW) and self._estimate_tokens() > low_budget:
            removed = self.messages.pop(1)
            removed_messages.append(removed)
            dropped += 1
            # Native mode: a "tool" result message is only valid while the
            # assistant message carrying the matching tool_call is present.
            # Dropping the call but keeping the result would make the server
            # reject the request, so orphaned results go with it.
            if removed.get("tool_calls"):
                while (
                    len(self.messages) > (1 + MIN_ACTIVE_WINDOW)
                    and self.messages[1].get("role") == "tool"
                ):
                    orphaned = self.messages.pop(1)
                    removed_messages.append(orphaned)
                    dropped += 1
                    
        if not removed_messages:
            return 0, None, None

        # Find the last message with a database ID in the removed chunk
        last_id = None
        for msg in removed_messages:
            if "id" in msg and msg["id"] is not None:
                last_id = msg["id"]

        # Generate the new summary by calling the LLM
        new_summary = None
        chat_turns = [m for m in removed_messages if m["role"] in ("user", "assistant")]
        if chat_turns:
            summary_instructions = (
                "Sei un assistente specializzato nel riassumere conversazioni. "
                "Aggiorna la sinossi precedente includendo le informazioni rilevanti contenute "
                "nei nuovi messaggi di seguito. Mantieni la sinossi concisa ed evidenzia accordi, "
                "fatti chiave o modifiche. Rispondi SOLO con la sinossi aggiornata."
            )
            
            prompt_msgs = [
                {"role": "system", "content": summary_instructions}
            ]
            if self.summary:
                prompt_msgs.append({
                    "role": "user",
                    "content": f"Sinossi precedente:\n{self.summary}"
                })
            
            transcript_lines = []
            for msg in chat_turns:
                transcript_lines.append(f"{msg['role'].upper()}: {msg['content']}")
            transcript = "\n".join(transcript_lines)
            
            prompt_msgs.append({
                "role": "user",
                "content": f"Nuovi messaggi da integrare:\n{transcript}"
            })
            
            try:
                # Use LlamaClient's OpenAI client under the hood for a non-streaming call
                response = await self.client.client.chat.completions.create(
                    model=self.model,
                    messages=prompt_msgs,
                    stream=False,
                )
                new_summary = response.choices[0].message.content.strip()
                self.summary = new_summary
            except Exception:
                # Fallback to current summary in case of model error
                new_summary = self.summary

        return dropped, new_summary, last_id

    # ------------------------------------------------------------------
    # Prompt-based tool mode helpers
    # ------------------------------------------------------------------

    def _tool_instructions(self) -> str:
        """
        Build the system-prompt section that teaches a template-less model
        how to call tools: the definitions as JSON schemas, plus the exact
        reply format we can parse back out of its text.
        """
        definitions = [d["function"] for d in self.registry.definitions()]
        return (
            "You have access to the following tools, described as JSON schemas:\n"
            + json.dumps(definitions)
            + "\n\nTo use a tool, reply with ONLY a single JSON object in this "
            'exact format and nothing else:\n{"name": "<tool_name>", '
            '"arguments": {<parameters>}}\n'
            "You will receive the tool result in the next message; then answer "
            "the user (or call another tool). Never invent or assume tool "
            "results: if you need one, emit the JSON and wait."
        )

    def _switch_to_prompt_mode(self) -> None:
        """
        Runtime fallback: native tools turned out to be unsupported, so
        inject the tool instructions into the system prompt and flip the mode.
        """
        self.native_tools = False
        self.messages[0]["content"] += "\n\n" + self._tool_instructions()

    def _extract_tool_call(self, text: str) -> Optional[Tuple[str, str]]:
        """
        Try to parse a prompt-mode tool call out of the model's reply text.

        Accepts the JSON object bare, inside a ```json fence, or surrounded
        by stray whitespace/text. Returns (tool_name, arguments_json) only if
        the JSON is valid AND names a registered tool with a dict of
        arguments — anything else is treated as a normal text answer.
        """
        stripped = text.strip()
        candidates = []

        # Fenced block: ```json { ... } ```
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.S)
        if fence:
            candidates.append(fence.group(1))
        # Bare object, possibly with stray text around it: widest {...} slice
        first, last = stripped.find("{"), stripped.rfind("}")
        if first != -1 and last > first:
            candidates.append(stripped[first:last + 1])

        for candidate in candidates:
            try:
                # strict=False accepts literal control characters (newlines,
                # tabs) inside JSON strings. Spec-wise they should be escaped
                # as \n, but prompt-mode models (e.g. Gemma) routinely emit
                # them raw in multi-line arguments — which is exactly the
                # common case for edit_file/write_file content.
                obj = json.loads(candidate, strict=False)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(obj, dict)
                and isinstance(obj.get("name"), str)
                and self.registry.get(obj["name"]) is not None
                and isinstance(obj.get("arguments", {}), dict)
            ):
                return obj["name"], json.dumps(obj.get("arguments", {}))
        return None

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _execute_with_confirmation(self, name: str, arguments_json: str) -> str:
        """
        Execute a tool, honoring the confirmation flag.

        Tools flagged requires_confirmation are only run if the injected
        confirm callback approves. A denial is reported to the model as the
        tool result, so it can adapt (e.g. propose an alternative) instead
        of retrying blindly.

        The confirm callback may be either sync (e.g. click.confirm from the
        CLI) or async (e.g. a future GUI/HTTP front-end), so it is awaited
        only when it is actually a coroutine function.
        """
        tool = self.registry.get(name)

        if tool is not None and tool.requires_confirmation:
            if self.confirm is None:
                return (
                    f"Tool '{name}' requires user confirmation, but no "
                    "confirmation mechanism is available. Action not executed."
                )
            prompt = self._format_confirmation(name, arguments_json)

            if inspect.iscoroutinefunction(self.confirm):
                confirmed = await self.confirm(prompt)
            else:
                confirmed = self.confirm(prompt)

            if not confirmed:
                return "The user declined to execute this action."

        return await self.registry.execute(name, arguments_json)

    @staticmethod
    def _format_confirmation(name: str, arguments_json: str) -> str:
        """
        Render a tool call as human-readable text for the confirmation prompt.

        The raw arguments arrive as a JSON string, so a shell command reaches
        us escaped and on a single line (\\n, \\", ...). The user is being
        asked to vet code before it runs on their machine, so it must be shown
        exactly as it will execute: this decodes the JSON and prints each
        argument verbatim, preserving newlines and quoting.

        Returns UI-agnostic plain text — the CLI styles it, a future GUI can
        render it differently. Falls back to the raw JSON if it can't be
        parsed, so the user never sees *less* than what will run.
        """
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
                # Multi-line values (scripts, file contents) get their own
                # indented block so the user can read every line.
                indented = "\n".join(f"    | {ln}" for ln in text.split("\n"))
                lines.append(f"  {key}:\n{indented}")
            else:
                lines.append(f"  {key}: {text}")
        return "\n".join(lines)

    @staticmethod
    def _describe_error(exc: Exception) -> str:
        """
        Turn a raw client/server exception into a helpful, user-facing message.

        Special-cases the llama-server "Compute error", which on Apple Silicon
        almost always means the Metal (GPU) backend ran out of memory. When
        that happens the backend is left in a broken state and every following
        request fails too, so the server must be restarted — we say so, and
        suggest the concrete knobs that reduce memory usage.
        """
        text = str(exc)
        lowered = text.lower()
        if (
            "compute error" in lowered
            or "out of memory" in lowered
            or "insufficient memory" in lowered
        ):
            return (
                f"The inference server hit a compute error ({text}). "
                "This usually means it ran out of GPU memory. The server is "
                "likely wedged now and must be restarted. To avoid it, relaunch "
                "with fewer GPU layers (e.g. `llampaca run --gpu 20`, or "
                "`--gpu 0` for CPU-only), a smaller context (e.g. `--ctx 2048`), "
                "or a smaller / more lightly-quantized model (e.g. a Q4_K_M "
                "build instead of Q8_0)."
            )
        return f"The inference server returned an error: {text}"

    def reset(self) -> None:
        """Clear the conversation history, keeping the system prompt."""
        self.messages = self.messages[:1]
