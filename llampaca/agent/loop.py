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

from llampaca.agent.context import estimate_tokens, trim_history_to_budget
from llampaca.agent.prompts import build_base_system_prompt, build_system_prompt
from llampaca.agent.executor import execute_with_confirmation, extract_tool_call, clean_tool_call_text, DECLINED_MARKER

# Safety cap: maximum model→tools→model round-trips for a single user
# message. Prevents a confused model from looping on tool calls forever.
MAX_ITERATIONS = 10


# Minimum number of recent messages (turns) that are guaranteed to remain
# in the active context window and never get trimmed/summarized.

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

# Rough tokens-per-character ratio used for budget estimates (the 20%
# headroom above the high watermark absorbs the estimation error). The
# constant lives in config.py so that low-level modules (rag, attachments)
# can share the same heuristic without importing this module — this
# re-export keeps existing `from llampaca.agent.loop import CHARS_PER_TOKEN`
# users working.
from llampaca.config import CHARS_PER_TOKEN, DEFAULT_CONTEXT_SIZE
import logging
logger = logging.getLogger(__name__)

# Fixed per-message overhead, in tokens: every message costs a few extra
# tokens for its role marker and the chat template's framing around it.


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
        context_size: int = DEFAULT_CONTEXT_SIZE,
        summary: Optional[str] = None,
        no_think: bool = False,
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

        # Turns removed by _trim_history() but not yet folded into the
        # rolling summary. Summarization is DEFERRED on purpose: it is a
        # full LLM generation (seconds on local hardware), so doing it
        # inline before answering would both delay the answer and clobber
        # llama-server's prompt cache right before the main request. The UI
        # drains this buffer with summarize_pending() after the turn's
        # answer has been rendered — typically while the user is typing.
        self._pending_summary_turns: List[Dict[str, Any]] = []
        self._pending_summary_last_id: Optional[int] = None

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
        self._base_system_prompt = build_base_system_prompt(system_prompt)

        # Skip the reasoning phase on every request of this session. The CLI
        # sets the equivalent on the server at launch (--no-think); this is the
        # per-request form, so the GUI can flip it between turns without a
        # restart. Only effective when the GGUF's chat template implements the
        # toggle — official Qwen GGUFs do.
        self.no_think = no_think

        # Full conversation history, in OpenAI messages format.
        # Kept on the instance so multiple send() calls form one conversation.
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt_content()}
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
            ("reasoning", str)        — chunk of the model's thinking text
                                         (reasoning models only). Display-only:
                                         never stored in the history or the DB
            ("tool_start", dict)      — a tool call is being generated:
                                         {"name": ...}. Emitted as soon as the
                                         tool's name is known, while its
                                         arguments are still streaming — lets
                                         the UI announce the call immediately
            ("tool_call", dict)       — the model requested a tool (arguments
                                         complete, about to execute):
                                         {"name": ..., "arguments": <json str>}
            ("tool_result", dict)     — a tool finished:
                                         {"name": ..., "result": <str>}
            ("stats", dict)           — llama-server's timings for one model
                                         request (see client docstring). A turn
                                         with tool round-trips emits one per
                                         request; the UI aggregates them to
                                         show turn-level tokens/second
            ("warning", str)          — non-fatal problem (e.g. tool mode switched,
                                         iteration cap reached)
            ("error", str)            — the model/server failed this turn; the
                                         session stays alive so the user can retry

        Note: trimming old history queues the removed turns for deferred
        summarization — the caller flushes them AFTER the turn with
        summarize_pending() (see that method for the rationale).
        """
        self.messages[0]["content"] = self._system_prompt_content()
        self.messages.append({"role": "user", "content": user_input, "id": message_id})
        last_image_result = None

        for _ in range(self.max_iterations):
            # Keep the history inside the context budget before *every*
            # request, not just once per user message: tool results appended
            # mid-turn can overflow the window too. Trimming is cheap and
            # synchronous; the summary of what was dropped is generated
            # later, off this turn's critical path (summarize_pending).
            dropped, removed = trim_history_to_budget(self.messages, self.context_size, self.tools_enabled, self.native_tools, self.registry)
            if removed:
                for msg in removed:
                    if msg.get("id") is not None:
                        self._pending_summary_last_id = msg["id"]
                self._pending_summary_turns.extend(m for m in removed if m["role"] in ("user", "assistant"))

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
            accumulated_reasoning = ""
            # In prompt mode a tool call arrives as JSON *text*. We hold the
            # stream back while the output still looks like JSON (so the raw
            # call is never shown to the user), but flush and stream live as
            # soon as it is clearly prose.
            buffer = ""
            buffering = prompt_mode
            try:
                async for kind, data in self.client.chat_stream_events(
                    messages_to_send, model=self.model, tools=tools,
                    no_think=self.no_think
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
                    elif kind == "reasoning":
                        produced_text = True
                        accumulated_reasoning += data
                        yield ("reasoning", data)
                    elif kind == "tool_name":
                        # Early announcement: the call's arguments are still
                        # streaming (often for seconds), but the UI can
                        # already show which tool is being prepared.
                        yield ("tool_start", {"name": data})
                    elif kind == "stats":
                        # Per-request server timings, forwarded as-is: the
                        # UI owns the aggregation across a turn's multiple
                        # requests (tool round-trips).
                        yield ("stats", data)
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
                error_desc = self._describe_error(e)
                yield ("text", "\n\n❌ **ATTENZIONE:**\n\n")
                
                explanation_messages = [
                    {
                        "role": "system",
                        "content": (
                            "Sei l'assistente AI integrato in Llampaca (un'applicazione desktop locale per modelli di linguaggio e integrazioni MCP).\n"
                            "L'utente ha riscontrato un errore tecnico nell'agente. "
                            "Analizza l'errore e spiegalo in italiano in modo semplice, cordiale e diretto.\n"
                            "Fornisci istruzioni precise relative all'interfaccia grafica di Llampaca per risolverlo:\n"
                            "- Di' all'utente che può aumentare la dimensione del contesto (Context Size / Max tokens per la sessione) cliccando sulla scheda 'Impostazioni' (Settings) dell'applicazione e inserendo un valore maggiore (es. 65536 o 98304).\n"
                            "- Se l'errore riguarda i token di contesto esauriti a causa di troppi strumenti MCP (come accade se si attiva un server con decine/centinaia di tool), consiglia di andare nella scheda 'MCP' per disinstallare i server MCP superflui o disattivare quelli con troppi tool.\n"
                            "Parla al plurale come Llampaca o come assistente di Llampaca, usa formattazione markdown chiara e sii molto specifico."
                        )
                    },
                    {"role": "user", "content": f"spiegami questo errore: {error_desc}"}
                ]
                
                full_explanation = "❌ **ATTENZIONE:**\n\n"
                try:
                    async for chunk in self.client.chat_stream(explanation_messages, model=self.model):
                        yield ("text", chunk)
                        full_explanation += chunk
                except Exception as stream_err:
                    fallback_err = f"Non è stato possibile caricare i dettagli dell'errore tramite il modello: {stream_err}.\n\nL'errore originale riscontrato è:\n`{error_desc}`"
                    yield ("text", fallback_err)
                    full_explanation += fallback_err
                
                self.messages.append({"role": "assistant", "content": full_explanation})
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
                call = (extract_tool_call(text, self.registry) if buffering else None) or extract_tool_call(accumulated_reasoning, self.registry)

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
                result = await execute_with_confirmation(self.registry, self.confirm, name, arguments_json)
                yield ("tool_result", {"name": name, "result": result})
                # Templates without tool support reject the "tool" role, so
                # the result is fed back as a clearly-labeled user message.
                self.messages.append({
                    "role": "user",
                    "content": f"[Result of tool '{name}']\n{result}",
                })
                if result.startswith(DECLINED_MARKER):
                    cancel_msg = "\n\n⚠️ **Operazione annullata:** L'utente ha rifiutato l'autorizzazione per eseguire l'operazione."
                    yield ("text", cancel_msg)
                    self.messages.append({"role": "assistant", "content": cancel_msg.strip()})
                    return
                continue  # let the model see the result

            # --- 2b. Native mode: structured tool_calls from the server -
            tool_calls = assistant_message.get("tool_calls")
            logger.debug(f"\n  [DEBUG native] assistant_message keys: {list(assistant_message.keys())}")
            logger.debug(f"  [DEBUG native] tool_calls from server: {tool_calls is not None} ({type(tool_calls).__name__})")
            logger.debug(f"  [DEBUG native] content (first 100): {(assistant_message.get('content') or '')[:100]!r}")
            logger.debug(f"  [DEBUG native] accumulated_reasoning (first 100): {accumulated_reasoning[:100]!r}")
            if not tool_calls:
                text_content = assistant_message.get("content") or ""
                fallback = extract_tool_call(text_content, self.registry) or extract_tool_call(accumulated_reasoning, self.registry)
                logger.debug(f"  [DEBUG native] fallback result: {fallback}")
                if fallback:
                    name, arguments_json = fallback
                    call_id = f"call_fallback_{len(self.messages)}"
                    tool_calls = [{
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": arguments_json,
                        }
                    }]
                    # Sincronizza il messaggio assistente nella cronologia salvando il tool_calls sintetico
                    # e pulendo la chiamata grezza dal contenuto di testo
                    assistant_message["tool_calls"] = tool_calls
                    assistant_message["content"] = clean_tool_call_text(text_content)
                else:
                    # If content is empty but reasoning contains the actual answer
                    # (Qwen with reasoning enabled sometimes puts everything in
                    # the thinking stream), promote the cleaned reasoning to content.
                    if not (assistant_message.get("content") or "").strip() and accumulated_reasoning.strip():
                        cleaned = clean_tool_call_text(accumulated_reasoning)
                        if cleaned:
                            assistant_message["content"] = cleaned
                            yield ("text", cleaned)
                    if last_image_result and last_image_result not in (assistant_message.get("content") or ""):
                        extra_preview = f"\n\n{last_image_result}"
                        current_c = assistant_message.get("content") or ""
                        assistant_message["content"] = current_c + extra_preview
                        yield ("text", extra_preview)
                    self.messages.append(assistant_message)
                    return  # no tools requested: final answer for this turn

            self.messages.append(assistant_message)

            for tool_call in tool_calls:
                name = tool_call["function"]["name"]
                arguments_json = tool_call["function"]["arguments"]
                yield ("tool_call", {"name": name, "arguments": arguments_json})

                result = await execute_with_confirmation(self.registry, self.confirm, name, arguments_json)
                yield ("tool_result", {"name": name, "result": result})

                if name == "generate_image" and ("![" in result or "/api/media" in result or "file://" in result):
                    last_image_result = result

                # Feed the result back to the model, linked to its call id
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result,
                })

                if result.startswith(DECLINED_MARKER):
                    cancel_msg = "\n\n⚠️ **Operazione annullata:** L'utente ha rifiutato l'autorizzazione per eseguire l'operazione."
                    yield ("text", cancel_msg)
                    self.messages.append({"role": "assistant", "content": cancel_msg.strip()})
                    return

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

    def context_usage(self) -> Tuple[int, int]:
        return estimate_tokens(self.messages, self.tools_enabled, self.native_tools, self.registry), self.context_size

    @property
    def has_pending_summary(self) -> bool:
        """Whether trimmed turns are waiting to be folded into the summary."""
        return bool(self._pending_summary_turns)

    async def summarize_pending(self) -> Optional[Dict[str, Any]]:
        """
        Fold the turns queued by _trim_history() into the rolling summary
        with one non-streaming LLM call.

        Deliberately NOT called from send(): a summary is a full generation
        (seconds on local hardware) against the same single-slot
        llama-server, so doing it before the turn's main request would both
        delay the user's answer and replace the server's cached prompt
        prefix with the summary prompt — forcing the main request to
        re-process the whole history. Instead the UI schedules this call
        AFTER the answer has been rendered, typically while the user is
        typing and the server sits idle. (llama-server serializes requests,
        so even a flush still in flight when the next turn starts costs
        nothing extra over having run it inline.)

        Returns:
            {"summary": str, "last_summarized_message_id": int | None} when
            a new summary was generated and should be persisted, else None.
            On a model/server failure the pending turns are kept queued, so
            the next call retries instead of silently losing them (the
            messages themselves are always safe in the database anyway).
        """
        turns = self._pending_summary_turns
        last_id = self._pending_summary_last_id
        if not turns:
            return None
        self._pending_summary_turns = []
        self._pending_summary_last_id = None

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
        for msg in turns:
            # content can be None on assistant messages that only carried
            # tool calls; render those as empty rather than "None".
            transcript_lines.append(f"{msg['role'].upper()}: {msg.get('content') or ''}")
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
        except Exception:
            # Re-queue in front of anything trimmed in the meantime, so a
            # later flush covers these turns too (order preserved).
            self._pending_summary_turns = turns + self._pending_summary_turns
            if self._pending_summary_last_id is None:
                self._pending_summary_last_id = last_id
            return None

        self.summary = new_summary
        return {"summary": new_summary, "last_summarized_message_id": last_id}

    # ------------------------------------------------------------------
    # Prompt-based tool mode helpers
    # ------------------------------------------------------------------

    def _system_prompt_content(self) -> str:
        from llampaca import wiki, skills
        tool_defs = []
        if self.tools_enabled and not self.native_tools and self.registry:
            tool_defs = [d["function"] for d in self.registry.definitions()]
        return build_system_prompt(
            base_prompt=self._base_system_prompt,
            wiki_index=wiki.render_index(),
            skills_index=skills.render_index(),
            tool_definitions=tool_defs,
            summary=self.summary or "",
            context_size=self.context_size
        )

    def refresh_tools(self) -> None:
        """
        Re-sync the agent after the tool registry changed mid-session —
        e.g. search_documents activated by the first indexed attachment.

        Native mode needs nothing: definitions() is read from the registry
        on every request, so the next call already carries the new set.
        Prompt-based mode keeps the definitions inside messages[0], which
        is rebuilt here.

        Either way, changing the tool set changes the rendered prompt
        prefix, so the next request pays one full prompt re-processing
        (llama-server's prefix cache misses). That is a per-change cost by
        design — callers should change the registry when something real
        happens (a document was indexed), not speculatively.
        """
        self.tools_enabled = (
            self.registry is not None and len(self.registry.names()) > 0
        )
        if not self.native_tools:
            self.messages[0]["content"] = self._system_prompt_content()

    def _switch_to_prompt_mode(self) -> None:
        """
        Runtime fallback: native tools turned out to be unsupported, so
        flip the mode and rebuild the system prompt with the tool
        instructions included (rebuild, not append: see
        _system_prompt_content on idempotence).
        """
        self.native_tools = False
        self.messages[0]["content"] = self._system_prompt_content()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

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
