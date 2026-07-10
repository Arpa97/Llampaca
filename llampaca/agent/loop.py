"""
The agentic execution loop (asynchronous).

This is the core of Llampaca as an *agent* rather than a plain chatbot:

    1. Send the conversation to the model together with the available tool
       definitions (OpenAI "tools" format, supported natively by
       llama-server via its jinja chat templates).
    2. The model answers with text, with tool calls, or both.
    3. Execute each requested tool locally (asking the user for
       confirmation when the tool is flagged as dangerous), append the
       results to the conversation as "tool" messages.
    4. Go back to 1, until the model produces a final text answer or the
       iteration cap is reached.

Design rule: this module is UI-independent. It never prints, never prompts.
It communicates with the outside world by *yielding events* (see send()),
and asks for confirmation through an injected callback. The CLI renders
these events in the terminal today; a future GUI or local HTTP API will
render the exact same events differently.
"""

import inspect
from datetime import datetime
from typing import Any, Callable, Dict, AsyncGenerator, List, Optional, Tuple

from llampaca.engine.client import LlamaClient
from llampaca.tools.registry import ToolRegistry

# Safety cap: maximum model→tools→model round-trips for a single user
# message. Prevents a confused model from looping on tool calls forever.
MAX_ITERATIONS = 10

DEFAULT_SYSTEM_PROMPT = (
    "You are Llampaca, a helpful local AI personal assistant running entirely "
    "on the user's machine. You can use the available tools to read and write "
    "files in the user's workspace, run shell commands, search the web, and "
    "fetch web pages. When the user asks about current events or facts that may "
    "have changed since your training, use web_search to find up-to-date "
    "information rather than answering from memory, and cite what you found. "
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
        confirm: Optional[Callable[[str], Any]] = None,
        model: str = "local-model",
        max_iterations: int = MAX_ITERATIONS,
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
        """
        self.client = client
        self.registry = registry
        self.confirm = confirm
        self.model = model
        self.max_iterations = max_iterations
        # True while we send tool definitions with each request; flipped off
        # permanently if the model's chat template rejects them.
        self.tools_enabled = registry is not None and len(registry.names()) > 0
        # Becomes True the first time a request that *included* tools comes
        # back successfully. Once tools are proven to work, a later streaming
        # error can no longer be "the model rejected the tools parameter"
        # (that failure only happens on the very first request, before any
        # content is produced) — so we must NOT disable tools in that case.
        self._tools_confirmed_working = False

        # Local models have old training cutoffs and no clock: without this
        # they guess the date (and e.g. build web searches around the wrong
        # year). Appending today's date to the system prompt fixes that for
        # free. Applied to custom prompts too, since the problem is the same.
        base_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        today = datetime.now().strftime("%A, %d %B %Y")
        dated_prompt = f"{base_prompt} Today's date is {today}."

        # Full conversation history, in OpenAI messages format.
        # Kept on the instance so multiple send() calls form one conversation.
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": dated_prompt}
        ]

    async def send(self, user_input: str) -> AsyncGenerator[Tuple[str, Any], None]:
        """
        Process one user message through the agent loop, yielding events as
        they happen so the UI can render progress live.

        Events yielded (as (kind, data) tuples):
            ("text", str)         — chunk of assistant text, render as it arrives
            ("tool_call", dict)   — the model requested a tool:
                                     {"name": ..., "arguments": <json str>}
            ("tool_result", dict) — a tool finished:
                                     {"name": ..., "result": <str>}
            ("warning", str)      — non-fatal problem (e.g. tools unsupported
                                     by this model, iteration cap reached)
            ("error", str)        — the model/server failed this turn; the
                                     session stays alive so the user can retry
        """
        self.messages.append({"role": "user", "content": user_input})

        for _ in range(self.max_iterations):
            tools = self.registry.definitions() if self.tools_enabled else None

            # --- 1. Query the model (streaming) -------------------------
            assistant_message: Optional[Dict[str, Any]] = None
            produced_text = False  # did we stream any assistant text this call?
            try:
                async for kind, data in self.client.chat_stream_events(
                    self.messages, model=self.model, tools=tools
                ):
                    if kind == "text":
                        produced_text = True
                        yield ("text", data)
                    elif kind == "message":
                        assistant_message = data
                # A request that included tools and completed without error
                # proves the model's chat template accepts the tools parameter.
                if tools is not None:
                    self._tools_confirmed_working = True
            except Exception as e:
                # Distinguish two very different failures:
                #
                # (a) The model's chat template can't handle the `tools`
                #     parameter at all. llama-server rejects the request
                #     immediately, on the FIRST call, before any text is
                #     streamed and before tools have ever worked. Only then
                #     does it make sense to disable tools and retry.
                #
                # (b) Anything else — a server-side compute/GPU-memory error,
                #     a dropped connection, etc. Tools may already be working
                #     fine, so disabling them fixes nothing (and the server is
                #     often wedged anyway). Report the error and end the turn
                #     WITHOUT crashing, so the user keeps their session.
                looks_like_tool_rejection = (
                    tools is not None
                    and not self._tools_confirmed_working
                    and not produced_text
                )
                if looks_like_tool_rejection:
                    self.tools_enabled = False
                    yield (
                        "warning",
                        f"The model appears not to support tool calling ({e}). "
                        "Continuing without tools for this session.",
                    )
                    continue  # retry the same turn, without tools
                yield ("error", self._describe_error(e))
                return

            if assistant_message is None:
                # Defensive: the stream ended without a final message event
                yield ("warning", "The model returned an empty response.")
                return

            # The assistant message (text and/or tool calls) always goes into
            # the history, so the model sees its own tool requests next turn.
            self.messages.append(assistant_message)

            tool_calls = assistant_message.get("tool_calls")
            if not tool_calls:
                # No tools requested: this was the final answer for this turn
                return

            # --- 2. Execute each requested tool -------------------------
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

    async def _execute_with_confirmation(self, name: str, arguments_json: str) -> str:
        """
        Execute a tool, honoring the confirmation flag.

        Tools flagged requires_confirmation are only run if the injected
        confirm callback approves. A denial is reported to the model as the
        tool result, so it can adapt (e.g. propose an alternative) instead
        of retrying blindly.
        """
        tool = self.registry.get(name)

        if tool is not None and tool.requires_confirmation:
            if self.confirm is None:
                return (
                    f"Tool '{name}' requires user confirmation, but no "
                    "confirmation mechanism is available. Action not executed."
                )
            prompt = f"The agent wants to run tool '{name}' with arguments: {arguments_json}"
            
            if inspect.iscoroutinefunction(self.confirm):
                confirmed = await self.confirm(prompt)
            else:
                confirmed = self.confirm(prompt)
                
            if not confirmed:
                return "The user declined to execute this action."

        return self.registry.execute(name, arguments_json)

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
