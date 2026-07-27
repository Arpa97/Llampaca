import http.server
import socketserver
import threading
import socket
import sys
import json
import asyncio
import traceback
import queue
from pathlib import Path

# Import database methods
from llampaca.engine.db import (
    list_conversations,
    get_conversation,
    create_conversation,
    add_message,
    delete_conversation,
    update_conversation_title,
    update_conversation_summary
)
from llampaca.config import load_config, DEFAULT_CONTEXT_SIZE
from llampaca.engine import state
from llampaca.engine.client import LlamaClient
from llampaca.engine.server import LlamaServer, _restart_active_server_fallback
from llampaca.tools import build_default_registry
from llampaca.config import MODELS_DIR
import time
import re
import uuid
def run_async(coro):
    """Run an async coroutine thread-safely inside the AgentManager's event loop if running."""
    if agent_manager and agent_manager.loop and agent_manager.loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, agent_manager.loop)
        return future.result()
    else:
        return asyncio.run(coro)

import re
import logging
logger = logging.getLogger(__name__)

# Matches an injected attachment block (build_attachment_block output) or a
# RAG index note, so the user's own words can be recovered from a merged
# message — used for auto-titling. DOTALL: the document body spans lines.
_ATTACHMENT_BLOCK_RE = re.compile(
    r"\[Attached file:.*?\[End of attached file:[^\]]*\]"
    r"|\[Attached and indexed:[^\]]*\]",
    re.DOTALL,
)


def _strip_attachment_blocks(text: str) -> str:
    """Remove attachment/index blocks from a merged user message, leaving the
    user's actual text (collapsing the whitespace they were joined with)."""
    stripped = _ATTACHMENT_BLOCK_RE.sub("", text or "")
    return stripped.strip()


def is_server_running(host="127.0.0.1", port=8080):
    """Quickly check if the llama-server is listening on the configured port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            s.connect((host, port))
            return True
        except (socket.timeout, ConnectionRefusedError):
            return False

class AgentManager:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.mcp_manager = None
        self.registry = None
        self.client = None
        self.ready_event = threading.Event()
        self.request_queue = None
        self.config = None
        self.pending_confirmations = {}
        self.current_task = None
        self._mcp_reload_lock = None
        self.mcp_config_mtime = 0
        # Serialises the startup cache warm-up against real user turns.
        # Both drive the same single llama-server: letting them overlap made
        # them fight for the GPU — measured, a message sent while the warm-up
        # was still running took 28.5 s (worse than the 19.3 s before any of
        # this existed) and stretched the warm-up itself from 8 s to 16 s.
        # Held for the whole turn, so a message arriving mid-warm-up simply
        # waits for it and then inherits the cache it just built.
        self._inference_lock = None

        # --- File attachments (GUI equivalent of the CLI's /attach) -------
        # Attachments uploaded but not yet sent, keyed by conversation id.
        # Same staging model as the CLI: a file is extracted/indexed the
        # moment it is uploaded, then merged into the NEXT user message so
        # the model receives "document + question" as one user turn (chat
        # templates like Gemma's reject two consecutive user messages).
        #   pending_attachments[conv_id] -> [(filename, extracted_text), ...]
        #       small files, injected verbatim into the next message.
        #   pending_index_notes[conv_id] -> [note_str, ...]
        #       large files, indexed for RAG; only a pointer travels with
        #       the message, never the document text.
        # Mutated from both the HTTP thread (upload/take) and this manager's
        # event-loop thread (indexing), so every access takes _pending_lock.
        self.pending_attachments = {}
        self.pending_index_notes = {}
        self._pending_lock = threading.Lock()
        # Lazily created on the first over-budget attachment: constructing it
        # only resolves the configured embedder; the embedding llama-server
        # subprocess starts on the first ensure_started()/index_document().
        self.embedding_service = None

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main_task())

    async def _main_task(self):
        self.request_queue = asyncio.Queue()
        # Initialize
        await self._init_async(self.config)
        self.ready_event.set()
        
        try:
            while True:
                req = await self.request_queue.get()
                if req is None: # Shutdown signal
                    break
                
                # Check for special server restart request
                if isinstance(req, tuple) and len(req) == 3 and req[0] == "restart_server":
                    _, params, response_future = req
                    try:
                        success = await self._restart_server_internal(params)
                        response_future.set_result(success)
                    except Exception as e:
                        response_future.set_exception(e)
                    continue

                # Check for special MCP reload request
                if isinstance(req, tuple) and len(req) == 2 and req[0] == "reload_mcp":
                    _, response_future = req
                    try:
                        await self._reload_mcp_manager_internal()
                        response_future.set_result(True)
                    except Exception as e:
                        response_future.set_exception(e)
                    continue

                # Check for special registry reload request
                if isinstance(req, tuple) and len(req) == 2 and req[0] == "reload_registry":
                    _, response_future = req
                    try:
                        await self._reload_registry_internal()
                        response_future.set_result(True)
                    except Exception as e:
                        response_future.set_exception(e)
                    continue

                from llampaca.config import MCP_CONFIG_PATH
                if MCP_CONFIG_PATH.exists():
                    try:
                        mtime = MCP_CONFIG_PATH.stat().st_mtime
                        if mtime > self.mcp_config_mtime:
                            self.mcp_config_mtime = mtime
                            await self._reload_mcp_manager_internal()
                    except Exception as e:
                        logger.error(f"[AgentManager] Error checking/reloading MCP config on message: {e}")

                # If the startup cache warm-up is still in flight, wait for it
                # instead of racing it: both talk to the same llama-server, and
                # overlapping them made each slower than either alone. Waiting
                # costs at most the tail of the warm-up and the turn then
                # inherits the cache it just built. Turns themselves are
                # already serialised by this loop, so nothing else contends.
                if self._inference_lock is not None:
                    async with self._inference_lock:
                        pass

                self.current_task = asyncio.create_task(self._process_message_coro(*req))
                try:
                    await self.current_task
                except asyncio.CancelledError:
                    logger.info("[AgentManager] Message processing task was cancelled.")
                finally:
                    self.current_task = None
        finally:
            if self.mcp_manager:
                try:
                    await asyncio.wait_for(self.mcp_manager.stop(), timeout=1.0)
                except Exception as e:
                    logger.error(f"Error stopping MCP: {e}")

    async def _restart_server_internal(self, params: dict) -> bool:
        if self._mcp_reload_lock is None:
            self._mcp_reload_lock = asyncio.Lock()
        async with self._mcp_reload_lock:
            import time
            # 1. Stop current MCP sessions (if any)
            if self.mcp_manager:
                logger.info("[AgentManager] Stopping active MCP manager in main task...")
                await self.mcp_manager.stop()
                self.mcp_manager = None
                
            # 2. Stop current llama-server and start new one
            from llampaca.engine.server import LlamaServer
            active_server = state.get_chat_server()
            if active_server:
                logger.info(f"[AgentManager] Stopping active llama-server on port {active_server.port}...")
                active_server.stop()
                time.sleep(0.5)
                state.set_chat_server(None)
                
            config = load_config()
            model_name = params.get("model_name") or config.get("default_model", "")
            
            from llampaca.config import MODELS_DIR, MODEL_PRESETS
            model_path = MODELS_DIR / model_name
            if not model_path.exists():
                model_path = Path(model_name)
                if not model_path.exists():
                    if model_name in MODEL_PRESETS:
                        preset_file = MODEL_PRESETS[model_name]["file"]
                        model_path = MODELS_DIR / preset_file
                        
            if not model_path.exists():
                logger.error(f"[AgentManager] Error: Model '{model_name}' could not be resolved.")
                return False

            resolved_port = params.get("port") or config.get("server_port", 8080)
            resolved_ctx = params.get("context_size") or config.get("context_size", DEFAULT_CONTEXT_SIZE)
            resolved_threads = params.get("n_threads") or config.get("n_threads", 4)
            resolved_gpu = params.get("gpu_layers") if params.get("gpu_layers") is not None else config.get("gpu_layers", -1)

            new_server = LlamaServer(
                model_path=model_path,
                port=resolved_port,
                context_size=resolved_ctx,
                n_threads=resolved_threads,
                gpu_layers=resolved_gpu
            )

            success = await new_server.start()
            if success:
                state.set_chat_server(new_server)
                from llampaca.engine.client import LlamaClient
                self.client = LlamaClient(port=new_server.port)
                
                # Update registry context size limit
                from llampaca.tools import build_default_registry
                self.registry = build_default_registry(max_result_chars=new_server.context_size)
                
                # Start fresh MCP manager matching the new registry
                from llampaca.config import load_mcp_config
                mcp_config = load_mcp_config()
                mcp_servers_config = mcp_config.get("mcp_servers", {})
                if mcp_servers_config:
                    from llampaca.engine.mcp_client import McpClientManager
                    self.mcp_manager = McpClientManager(mcp_servers_config)
                    await self.mcp_manager.start(self.registry)

                # A restart means a brand-new llama-server process, so its KV
                # cache is empty again: without this the first message after
                # switching model or context size would pay the full prefill.
                asyncio.create_task(self._warm_prompt_cache())

            return success

    async def reload_mcp_manager(self):
        fut = asyncio.get_running_loop().create_future()
        await self.request_queue.put(("reload_mcp", fut))
        await fut

    async def reload_registry(self):
        fut = asyncio.get_running_loop().create_future()
        await self.request_queue.put(("reload_registry", fut))
        await fut

    async def _reload_registry_internal(self):
        if self._mcp_reload_lock is None:
            self._mcp_reload_lock = asyncio.Lock()
        async with self._mcp_reload_lock:
            # 1. Stop current MCP sessions (if any)
            if self.mcp_manager:
                logger.info("[AgentManager] Reloading registry: stopping active MCP manager...")
                try:
                    await self.mcp_manager.stop()
                except Exception as e:
                    logger.error(f"Error stopping MCP on registry reload: {e}")
                self.mcp_manager = None

            # 2. Re-build default registry (which loads built-in + custom tools)
            from llampaca.tools import build_default_registry
            max_result_chars = self.registry.max_result_chars if self.registry else None
            self.registry = build_default_registry(max_result_chars=max_result_chars)

            # 3. Restart MCP sessions if configured
            from llampaca.config import load_mcp_config
            mcp_config = load_mcp_config()
            mcp_servers_config = mcp_config.get("mcp_servers", {})
            if mcp_servers_config:
                from llampaca.engine.mcp_client import McpClientManager
                self.mcp_manager = McpClientManager(mcp_servers_config)
                await self.mcp_manager.start(self.registry)
            logger.info("[AgentManager] Registry and MCP reloaded.")

    async def _reload_mcp_manager_internal(self):
        if self._mcp_reload_lock is None:
            self._mcp_reload_lock = asyncio.Lock()
        async with self._mcp_reload_lock:
            # 1. Stop current MCP sessions (if any)
            if self.mcp_manager:
                logger.info("[AgentManager] Reloading MCP: stopping active manager...")
                try:
                    await self.mcp_manager.stop()
                except Exception as e:
                    logger.error(f"Error stopping MCP on reload: {e}")
                self.mcp_manager = None
                
            # 2. Clear old MCP tools (tools with '__' prefix) from registry
            to_remove = [t for t in self.registry.names() if "__" in t]
            for t in to_remove:
                if t in self.registry._tools:
                    del self.registry._tools[t]
                    
            # 3. Load fresh config and start new sessions
            from llampaca.config import load_mcp_config
            mcp_config = load_mcp_config()
            mcp_servers_config = mcp_config.get("mcp_servers", {})
            if mcp_servers_config:
                from llampaca.engine.mcp_client import McpClientManager
                self.mcp_manager = McpClientManager(mcp_servers_config)
                await self.mcp_manager.start(self.registry)
            logger.info("[AgentManager] Reloading MCP: new sessions started and registered.")

    def start(self, config):
        self.config = config
        self.thread.start()
        self.ready_event.wait()
        
    async def _init_async(self, config):
        self._mcp_reload_lock = asyncio.Lock()
        self._inference_lock = asyncio.Lock()
        from llampaca.tools import build_default_registry
        from llampaca.engine.client import LlamaClient
        from llampaca.config import load_mcp_config
        
        self.client = LlamaClient(port=config.get("server_port", 8080))
        self.registry = build_default_registry(max_result_chars=config.get("context_size", DEFAULT_CONTEXT_SIZE))
        
        mcp_config = load_mcp_config()
        from llampaca.config import MCP_CONFIG_PATH
        if MCP_CONFIG_PATH.exists():
            self.mcp_config_mtime = MCP_CONFIG_PATH.stat().st_mtime
        mcp_servers_config = mcp_config.get("mcp_servers", {})
        legacy_mcp = config.get("mcp_servers", {})
        if legacy_mcp:
            mcp_servers_config = {**legacy_mcp, **mcp_servers_config}
            
        # Connecting the MCP servers happens OFF this function, in a
        # background task: _init_async gates ready_event, start() blocks on
        # ready_event, and start_gui_window() only creates the pywebview
        # window after start() returns. Connecting inline therefore put every
        # configured MCP server on the critical path of the window opening —
        # a server that never answers (typically one waiting for an
        # interactive OAuth grant, e.g. `npx @smithery/cli run <x>`) kept the
        # dashboard from appearing at all until it timed out. The prompt-cache
        # warm-up is chained after it there, for the reason documented in
        # _connect_mcp_then_warm.
        asyncio.create_task(self._connect_mcp_then_warm(mcp_servers_config))

    async def _connect_mcp_then_warm(self, mcp_servers_config: dict) -> None:
        """
        Connect the configured MCP servers, then warm the prompt cache.

        Runs detached from startup (see the call site) so that a slow or
        stuck MCP server delays only its own tools, never the GUI window.
        The tools land in the shared registry as soon as each server
        answers; a message sent before that simply sees the built-in tools.

        The warm-up stays chained AFTER the connection rather than running
        in parallel with it: the MCP tool schemas are part of the cached
        prompt prefix, so priming before they are registered would cache a
        prefix that no real request ever sends (and waste the warm-up).

        Takes the same lock as the reload paths: the GUI polls /api/mcp and
        can trigger a reload while this is still connecting, and two
        managers driving the same registry would double-register tools.

        Best-effort by design: a failing MCP server must degrade to "its
        tools are missing", never to a broken session.
        """
        try:
            if mcp_servers_config:
                from llampaca.engine.mcp_client import McpClientManager
                async with self._mcp_reload_lock:
                    manager = McpClientManager(mcp_servers_config)
                    await manager.start(self.registry)
                    self.mcp_manager = manager
        except Exception as e:
            logger.error(f"[AgentManager] MCP startup failed (session continues): {e}")
        await self._warm_prompt_cache()

    async def _warm_prompt_cache(self):
        """
        Pre-compute the KV cache for the shared prompt prefix, at startup.

        Every conversation sends the same opening block: system prompt, wiki
        index, skills index and the JSON schemas of all registered tools —
        measured at ~2270 tokens. llama-server caches the state it derives
        from those tokens and reuses it for every later request, so the cost
        is paid exactly once per server process: ~7.7 s on a 4B Q4 model for
        the first message, then ~0.15 s for each one after it.

        Without this, that 7.7 s lands on the user's first question. Firing
        the same prefix here moves it into the window's startup, where nobody
        is waiting on it. It does not reduce the work, only relocates it.

        Runs as a background task: startup must not block on it, and the
        HTTP API is already serving while it happens. If a message arrives
        mid-warm-up nothing breaks — llama-server serialises the two requests
        and the user's one still ends up hitting a warm cache.

        Best-effort by design: any failure is logged and ignored, because a
        missed optimisation must never prevent the app from starting.
        """
        if self.registry is None or self.client is None:
            return
        try:
            import time
            from llampaca.agent.loop import Agent
            from llampaca.agent.prompts import DEFAULT_SYSTEM_PROMPT
            from llampaca import wiki, skills

            # Built with the same calls as _process_message_coro. The cache is
            # keyed on the exact token sequence, so a hand-written copy of the
            # prompt would silently drift and waste the whole warm-up.
            system_prompt = DEFAULT_SYSTEM_PROMPT
            skills_idx = skills.render_skills_index()
            if skills_idx:
                system_prompt += "\n\n" + skills_idx

            config = self.config or {}
            model_name = config.get("default_model", "local-model")

            # Agent.__init__ appends today's date to the prompt and, in
            # prompt-based tool mode, the tool instructions too. Reproducing
            # that by hand would drift the moment either changes, so we build a
            # throwaway Agent and read its real messages[0]. Its constructor
            # calls get_chat_template() with blocking `requests`, hence the
            # thread: this coroutine shares the loop with the request queue.
            agent = await asyncio.to_thread(
                lambda: Agent(
                    client=self.client,
                    registry=self.registry,
                    system_prompt=system_prompt,
                    model=model_name,
                    context_size=config.get("context_size", DEFAULT_CONTEXT_SIZE),
                )
            )

            # Same condition the agent uses per request: tools travel as a
            # request parameter only in native mode. In prompt-based mode they
            # are already inside messages[0].
            tools = (
                self.registry.definitions()
                if agent.tools_enabled and agent.native_tools
                else None
            )
            # A minimal user turn so the template closes the system block
            # exactly as it will for a real first message.
            messages = agent.messages + [{"role": "user", "content": "."}]

            # Thinking on/off changes how the template renders, so the warm-up
            # has to use the same setting as the real turns or it would cache a
            # prefix nothing matches.
            no_think = config.get("no_think", False)

            t0 = time.time()
            async with self._inference_lock:
                timings = await self.client.prime_prompt_cache(
                    messages, model=model_name, tools=tools, no_think=no_think
                )
            elapsed = time.time() - t0

            if "error" in timings:
                logger.info(f"[AgentManager] Prompt cache warm-up skipped: {timings['error']}")
            else:
                logger.info(
                    f"[AgentManager] Prompt cache warmed: "
                    f"{timings.get('prompt_n', '?')} tokens in {elapsed:.1f}s "
                    f"— the first message no longer pays for them."
                )
        except Exception as e:
            logger.info(f"[AgentManager] Prompt cache warm-up failed (harmless): {e}")

    def stop(self):
        # Stop active llama-server if any
        try:
            active = state.get_chat_server()
            if active:
                logger.info("[AgentManager] Stopping active llama-server on shutdown...")
                active.stop()
                state.set_chat_server(None)
        except Exception as e:
            logger.error(f"Error stopping active llama-server on shutdown: {e}")

        # Stop the embedding llama-server too, if a large attachment ever
        # started it (idempotent no-op otherwise).
        try:
            if self.embedding_service is not None:
                self.embedding_service.stop()
        except Exception as e:
            logger.error(f"Error stopping embedding server on shutdown: {e}")

        if self.thread.is_alive() and self.request_queue:
            # 1. Cancel active processing task
            if self.current_task:
                self.loop.call_soon_threadsafe(self.current_task.cancel)
                
            # 2. Unblock any pending confirmations
            for confirm_id, conf_data in list(self.pending_confirmations.items()):
                conf_data["result"] = False
                self.loop.call_soon_threadsafe(conf_data["event"].set)
                
            # 3. Clear queue and put shutdown signal thread-safely
            def _shutdown_cleanup():
                while not self.request_queue.empty():
                    try:
                        self.request_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                self.request_queue.put_nowait(None)
                
            self.loop.call_soon_threadsafe(_shutdown_cleanup)
            self.thread.join(timeout=5)

    async def confirm_tool(self, name: str, arguments_json: str, result_queue) -> bool:
        import uuid
        confirm_id = str(uuid.uuid4())
        event = asyncio.Event()
        self.pending_confirmations[confirm_id] = {"event": event, "result": False}
        
        result_queue.put(("event", "tool_confirm_request", {
            "name": name, 
            "arguments": arguments_json, 
            "confirm_id": confirm_id
        }))
        
        try:
            await asyncio.wait_for(event.wait(), timeout=300.0)
            return self.pending_confirmations.get(confirm_id, {}).get("result", False)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.info(f"[confirm_tool] Conferma per '{name}' (id: {confirm_id}) annullata o scaduta.")
            result_queue.put(("event", "tool_confirm_cancel", {"confirm_id": confirm_id}))
            return False
        finally:
            self.pending_confirmations.pop(confirm_id, None)

    # ------------------------------------------------------------------ #
    # File attachments                                                    #
    # ------------------------------------------------------------------ #
    def _ensure_embedding_service(self):
        """Create the EmbeddingService once (lazy). The subprocess it drives
        is NOT started here — that happens on ensure_started()/index_document,
        so a session that never attaches a large file never spawns it."""
        if self.embedding_service is None:
            from llampaca.engine.embedding import EmbeddingService
            self.embedding_service = EmbeddingService()
        return self.embedding_service

    async def stage_attachment(self, conv_id, filename, text, context_size):
        """
        Stage an already-extracted document for a conversation, mirroring the
        CLI's /attach decision: within the attachment budget the text is
        injected verbatim into the next message; beyond it the document is
        indexed for RAG and only a search pointer is staged.

        Runs on this manager's event loop (index_document is async). Returns a
        dict the HTTP handler serializes back to the browser.
        """
        from llampaca.attachments import (
            attachment_token_budget,
            estimate_tokens,
        )

        budget = attachment_token_budget(context_size)
        with self._pending_lock:
            staged = sum(
                estimate_tokens(t)
                for _, t in self.pending_attachments.get(conv_id, [])
            )
        new_tokens = estimate_tokens(text)

        # Fits the budget: inject directly (phase 1). The check is on the
        # cumulative staged size, so many small files that jointly overflow
        # get the same treatment as one big one.
        if staged + new_tokens <= budget:
            with self._pending_lock:
                self.pending_attachments.setdefault(conv_id, []).append(
                    (filename, text)
                )
            used_pct = (staged + new_tokens) * 100 // budget if budget else 0
            return {
                "kind": "inject",
                "filename": filename,
                "tokens": new_tokens,
                "budget_used_pct": used_pct,
            }

        # Too large for direct injection: index for search (RAG). Useless
        # without a tool registry to expose search_documents, so guard it.
        if self.registry is None:
            raise RuntimeError(
                f"'{filename}' is too large to inject (~{new_tokens} tokens vs "
                f"a budget of ~{budget}) and tools are disabled, so it cannot "
                "be indexed for search either. Attach a smaller file."
            )

        service = self._ensure_embedding_service()
        info = await service.index_document(conv_id, filename, text)
        pages_part = f", {info['pages']} pages" if info["pages"] else ""
        note = (
            f"[Attached and indexed: {filename} "
            f"({info['chunks']} searchable passages{pages_part}). "
            "This document is NOT in your context: use the search_documents "
            "tool to read passages from it.]"
        )
        with self._pending_lock:
            self.pending_index_notes.setdefault(conv_id, []).append(note)
        return {
            "kind": "rag",
            "filename": filename,
            "chunks": info["chunks"],
            "pages": info["pages"],
        }

    def take_pending(self, conv_id):
        """Atomically pop and return (attachments, index_notes) staged for a
        conversation, clearing them. Called from the HTTP thread right before
        the user message is persisted, so the attachments ride along with it
        exactly once."""
        with self._pending_lock:
            attachments = self.pending_attachments.pop(conv_id, [])
            notes = self.pending_index_notes.pop(conv_id, [])
        return attachments, notes

    async def activate_document_search(self, conv_id):
        """
        Ensure the search_documents tool on self.registry is bound to
        `conv_id` when that conversation has indexed documents, and absent
        otherwise. Because the GUI rebuilds the Agent per message from one
        shared registry, the tool must be (re)bound to the CURRENT
        conversation each turn — otherwise a message in conversation B could
        search conversation A's documents.
        """
        if self.registry is None:
            return
        from llampaca.engine.db import list_documents
        from llampaca.tools import register_document_tools

        docs = await list_documents(conv_id)
        has_tool = "search_documents" in self.registry.names()

        if not docs:
            # No documents for this conversation: drop any stale binding left
            # over from a previously active conversation.
            if has_tool and "search_documents" in self.registry._tools:
                del self.registry._tools["search_documents"]
            return

        # (Re)bind the tool to this conversation. Registering unconditionally
        # is cheapest and guarantees the closure captures the right conv_id;
        # remove the previous one first so registration never conflicts.
        service = self._ensure_embedding_service()
        await service.ensure_started()
        if "search_documents" in self.registry._tools:
            del self.registry._tools["search_documents"]
        register_document_tools(
            self.registry,
            embed_query=service.query_embedder(),
            conversation_id=conv_id,
        )

    def cancel_current(self) -> bool:
        """Cancel the message currently being processed, if any."""
        for cid, data in list(self.pending_confirmations.items()):
            try:
                data["result"] = False
                self.loop.call_soon_threadsafe(data["event"].set)
            except Exception:
                pass
        task = self.current_task
        if task is not None and not task.done():
            self.loop.call_soon_threadsafe(task.cancel)
            return True
        return False

    def process_message(self, conv_id, content, user_msg_id, conv_data, config, result_queue):
        if self.request_queue:
            asyncio.run_coroutine_threadsafe(
                self.request_queue.put((conv_id, content, user_msg_id, conv_data, config, result_queue)),
                self.loop
            )
        
    async def _process_message_coro(self, conv_id, content, user_msg_id, conv_data, config, result_queue):
        try:
            from llampaca.agent.loop import Agent
            from llampaca.agent.prompts import DEFAULT_SYSTEM_PROMPT
            from llampaca import wiki

            last_tool_call = {}

            async def _confirm_wrapper(prompt, name=None, arguments=None):
                tool_name = name or last_tool_call.get("name") or "strumento"
                tool_args = arguments or last_tool_call.get("arguments") or prompt
                return await self.confirm_tool(tool_name, tool_args, result_queue)

            # Bind (or unbind) the search_documents tool to THIS conversation
            # before the agent is built: if the conversation has documents
            # that were indexed by an over-budget attachment, the model must
            # be able to search them; if it has none, any stale binding from a
            # previously active conversation must be removed. Best-effort — a
            # missing embedder must not break plain chat.
            try:
                await self.activate_document_search(conv_id)
            except Exception as e:
                result_queue.put((
                    "event", "warning",
                    f"Ricerca documenti non disponibile: {e}",
                ))

            # The wiki index (the model's persistent memory: page names +
            # descriptions) must ride in the system prompt so the model
            # knows what it actually remembers instead of guessing page
            # names. The CLI does this at session start (cli.py); the GUI
            # rebuilds the agent per message, so we build the index here.
            # render_index() reads the real ~/.llampaca/wiki, so this stays
            # in sync with what /remember stores from the terminal too.
            from llampaca import skills
            system_prompt = None
            if self.registry is not None:
                system_prompt = DEFAULT_SYSTEM_PROMPT
                skills_idx = skills.render_skills_index()
                if skills_idx:
                    system_prompt += "\n\n" + skills_idx

            agent = Agent(
                client=self.client,
                registry=self.registry,
                confirm=_confirm_wrapper,
                system_prompt=system_prompt,
                model=conv_data.get("model_name", "local-model"),
                context_size=config.get("context_size", DEFAULT_CONTEXT_SIZE),
                summary=conv_data.get("summary"),
                no_think=config.get("no_think", False)
            )
            
            last_summarized_id = conv_data.get("last_summarized_message_id")
            active_messages = []
            for m in conv_data.get("messages", []):
                if m.get("role") == "system":
                    continue
                if m.get("id") == user_msg_id:
                    continue
                if last_summarized_id is not None and m.get("id") is not None and m["id"] <= last_summarized_id:
                    continue
                active_messages.append({"role": m["role"], "content": m["content"]})
                
            agent.messages.extend(active_messages)

            response_content = ""
            reasoning_content = ""
            logger.info(f"\n[Agent Session] Processing message for conversation {conv_id}...")

            import time as _time
            turn_started = _time.monotonic()
            gen_tokens = 0
            gen_ms = 0.0

            result_queue.put(("event", "status_update", "Elaborazione contesto in corso con llama-server..."))
            async for kind, data in agent.send(content, message_id=user_msg_id):
                if kind == "tool_call":
                    last_tool_call["name"] = data.get("name")
                    last_tool_call["arguments"] = data.get("arguments")
                    response_content = ""  # Reset accumulated text: text before tool call was invocation syntax
                    reasoning_content = ""  # Reset: reasoning from tool-invocation iteration is not the final answer
                elif kind == "stats":
                    gen_tokens += data.get("predicted_n") or 0
                    gen_ms += data.get("predicted_ms") or 0.0
                result_queue.put(("event", kind, data))
                if kind == "text":
                    response_content += data
                elif kind == "reasoning":
                    reasoning_content += data
                elif kind == "summary_updated":
                    await update_conversation_summary(
                        conv_id,
                        data["summary"],
                        data["last_summarized_message_id"]
                    )

            turn_seconds = _time.monotonic() - turn_started
            logger.info("\n[Agent Session] Completed.")
            if not response_content and reasoning_content:
                # Clean any raw <tool_call> tags that may remain from
                # iterations where the model tried to call unregistered tools
                # or emitted tool syntax in the reasoning stream.
                import re as _re
                cleaned_reasoning = _re.sub(r"<tool_call>\s*\{.*?\}\s*</tool_call>", "", reasoning_content, flags=_re.S)
                cleaned_reasoning = _re.sub(r"<tool_call>.*", "", cleaned_reasoning, flags=_re.S)
                cleaned_reasoning = cleaned_reasoning.strip()
                if cleaned_reasoning:
                    response_content = cleaned_reasoning
                    result_queue.put(("event", "text", cleaned_reasoning))

            if response_content:
                assistant_msg_id = await add_message(conv_id, "assistant", response_content)
                result_queue.put(("done", {"assistant_message_id": assistant_msg_id}))

                # Turn footer: use the *same* estimate as the CLI so both
                # surfaces report identical context occupancy. context_usage()
                # returns (estimated_used_tokens, context_size) using the
                # chars/4 heuristic plus per-message overhead and, in native
                # mode, the tool-definition tokens — none of which the old
                # raw-chars calculation here accounted for. We also ship the
                # generation speed and elapsed time so the GUI can show them.
                used_tokens, total_tokens = agent.context_usage()
                used_percent = min(100, (used_tokens * 100 // total_tokens)) if total_tokens else 0
                tok_s = (gen_tokens / (gen_ms / 1000)) if (gen_tokens and gen_ms > 0) else None
                result_queue.put(("event", "context_status", {
                    "used_tokens": used_tokens,
                    "total_tokens": total_tokens,
                    "used_percent": used_percent,
                    "turn_seconds": round(turn_seconds, 1),
                    "gen_tokens": gen_tokens,
                    "tok_s": round(tok_s, 1) if tok_s is not None else None,
                }))
                
                # Auto-titling for new conversations
                if len(conv_data.get("messages", [])) <= 1:
                    try:
                        # Title on the user's own words, not on the injected
                        # document text (which would produce a title made of
                        # the attachment's first sentence).
                        title_source = _strip_attachment_blocks(content)
                        title_prompt = f"Scrivi un titolo molto breve (massimo 4-5 parole) che riassuma questo messaggio. Restituisci SOLO il titolo senza virgolette o preamboli: {title_source}"
                        title_res = ""
                        # Thinking off and a hard cap: naming a conversation is
                        # a throwaway generation, but on a reasoning model it
                        # was the single most expensive part of the first turn —
                        # measured at 195 tokens and 6.7 s (worst case seen:
                        # 834 tokens, 29.5 s) to produce four words, with the
                        # user waiting for the stream to close the whole time.
                        # Without thinking the same title costs 8 tokens and
                        # 0.3 s. max_tokens is the safety net for a model whose
                        # template ignores the toggle and reasons anyway.
                        async for chunk in self.client.chat_stream(
                            [{"role": "user", "content": title_prompt}],
                            max_tokens=25,
                            no_think=True,
                        ):
                            title_res += chunk
                        new_title = title_res.strip().strip('"').strip("'")
                        if new_title:
                            await update_conversation_title(conv_id, new_title)
                            result_queue.put(("event", "title_updated", {"title": new_title}))
                    except Exception as e:
                        logger.error(f"Error auto-titling: {e}")
            else:
                result_queue.put(("done", {}))

        except asyncio.CancelledError:
            # User pressed Stop (/api/cancel cancelled this task). Tell the
            # client, then RE-RAISE so cancellation propagates correctly (the
            # _main_task awaiting this task expects it). The `finally` below
            # still closes the SSE queue. Partial output is intentionally not
            # persisted: a cancelled turn leaves the user message but no saved
            # answer, which is the expected "aborted" outcome.
            logger.info("[Agent Session] Cancelled by user.")
            result_queue.put(("event", "cancelled", {}))
            raise
        except Exception as e:
            traceback.print_exc()
            result_queue.put(("error", str(e)))
        finally:
            result_queue.put(("close", None))

agent_manager = AgentManager()
