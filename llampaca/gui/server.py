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

def run_async(coro):
    """Run an async coroutine thread-safely inside the AgentManager's event loop if running."""
    if agent_manager and agent_manager.loop and agent_manager.loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, agent_manager.loop)
        return future.result()
    else:
        return asyncio.run(coro)

import re

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
                        print(f"[AgentManager] Error checking/reloading MCP config on message: {e}", flush=True)

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
                    print("[AgentManager] Message processing task was cancelled.", flush=True)
                finally:
                    self.current_task = None
        finally:
            if self.mcp_manager:
                try:
                    await asyncio.wait_for(self.mcp_manager.stop(), timeout=1.0)
                except Exception as e:
                    print(f"Error stopping MCP: {e}", flush=True)

    async def _restart_server_internal(self, params: dict) -> bool:
        if self._mcp_reload_lock is None:
            self._mcp_reload_lock = asyncio.Lock()
        async with self._mcp_reload_lock:
            import time
            # 1. Stop current MCP sessions (if any)
            if self.mcp_manager:
                print("[AgentManager] Stopping active MCP manager in main task...", flush=True)
                await self.mcp_manager.stop()
                self.mcp_manager = None
                
            # 2. Stop current llama-server and start new one
            from llampaca.engine.server import get_active_server, LlamaServer, set_active_server
            active_server = get_active_server()
            if active_server:
                print(f"[AgentManager] Stopping active llama-server on port {active_server.port}...", flush=True)
                active_server.stop()
                time.sleep(0.5)
                set_active_server(None)
                
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
                print(f"[AgentManager] Error: Model '{model_name}' could not be resolved.")
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
                set_active_server(new_server)
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
                print("[AgentManager] Reloading registry: stopping active MCP manager...", flush=True)
                try:
                    await self.mcp_manager.stop()
                except Exception as e:
                    print(f"Error stopping MCP on registry reload: {e}", flush=True)
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
            print("[AgentManager] Registry and MCP reloaded.", flush=True)

    async def _reload_mcp_manager_internal(self):
        if self._mcp_reload_lock is None:
            self._mcp_reload_lock = asyncio.Lock()
        async with self._mcp_reload_lock:
            # 1. Stop current MCP sessions (if any)
            if self.mcp_manager:
                print("[AgentManager] Reloading MCP: stopping active manager...", flush=True)
                try:
                    await self.mcp_manager.stop()
                except Exception as e:
                    print(f"Error stopping MCP on reload: {e}", flush=True)
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
            print("[AgentManager] Reloading MCP: new sessions started and registered.", flush=True)

    def start(self, config):
        self.config = config
        self.thread.start()
        self.ready_event.wait()
        
    async def _init_async(self, config):
        self._mcp_reload_lock = asyncio.Lock()
        self._inference_lock = asyncio.Lock()
        from llampaca.tools import build_default_registry
        from llampaca.engine.client import LlamaClient
        from llampaca.engine.mcp_client import McpClientManager
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
            
        if mcp_servers_config:
            self.mcp_manager = McpClientManager(mcp_servers_config)
            await self.mcp_manager.start(self.registry)

        # Warm the shared prompt prefix in the background. Deliberately AFTER
        # the MCP servers have registered their tools: their schemas are part
        # of the prefix, so warming any earlier would cache a prompt that no
        # real request ever sends.
        asyncio.create_task(self._warm_prompt_cache())

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
            from llampaca.agent.loop import Agent, DEFAULT_SYSTEM_PROMPT
            from llampaca import wiki, skills

            # Built with the same calls as _process_message_coro. The cache is
            # keyed on the exact token sequence, so a hand-written copy of the
            # prompt would silently drift and waste the whole warm-up.
            system_prompt = DEFAULT_SYSTEM_PROMPT + "\n\n" + wiki.render_index()
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
                print(f"[AgentManager] Prompt cache warm-up skipped: {timings['error']}", flush=True)
            else:
                print(
                    f"[AgentManager] Prompt cache warmed: "
                    f"{timings.get('prompt_n', '?')} tokens in {elapsed:.1f}s "
                    f"— the first message no longer pays for them.",
                    flush=True,
                )
        except Exception as e:
            print(f"[AgentManager] Prompt cache warm-up failed (harmless): {e}", flush=True)

    def stop(self):
        # Stop active llama-server if any
        try:
            from llampaca.engine.server import get_active_server, set_active_server
            active = get_active_server()
            if active:
                print("[AgentManager] Stopping active llama-server on shutdown...", flush=True)
                active.stop()
                set_active_server(None)
        except Exception as e:
            print(f"Error stopping active llama-server on shutdown: {e}", flush=True)

        # Stop the embedding llama-server too, if a large attachment ever
        # started it (idempotent no-op otherwise).
        try:
            if self.embedding_service is not None:
                self.embedding_service.stop()
        except Exception as e:
            print(f"Error stopping embedding server on shutdown: {e}", flush=True)

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
            print(f"[confirm_tool] Conferma per '{name}' (id: {confirm_id}) annullata o scaduta.", flush=True)
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
            from llampaca.agent.loop import Agent, DEFAULT_SYSTEM_PROMPT
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
                system_prompt = DEFAULT_SYSTEM_PROMPT + "\n\n" + wiki.render_index()
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
            print(f"\n[Agent Session] Processing message for conversation {conv_id}...", flush=True)

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
            print("\n[Agent Session] Completed.", flush=True)
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
                        print(f"Error auto-titling: {e}")
            else:
                result_queue.put(("done", {}))

        except asyncio.CancelledError:
            # User pressed Stop (/api/cancel cancelled this task). Tell the
            # client, then RE-RAISE so cancellation propagates correctly (the
            # _main_task awaiting this task expects it). The `finally` below
            # still closes the SSE queue. Partial output is intentionally not
            # persisted: a cancelled turn leaves the user message but no saved
            # answer, which is the expected "aborted" outcome.
            print("[Agent Session] Cancelled by user.", flush=True)
            result_queue.put(("event", "cancelled", {}))
            raise
        except Exception as e:
            traceback.print_exc()
            result_queue.put(("error", str(e)))
        finally:
            result_queue.put(("close", None))

agent_manager = AgentManager()

class QuietSimpleHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # I font in llampaca/gui/vendor/fonts/ vanno serviti con il MIME giusto.
    # La tabella di `mimetypes` conosce .woff2 solo da Python 3.11 in poi, ma
    # pyproject.toml dichiara requires-python = ">=3.10": su 3.10 il font
    # uscirebbe come application/octet-stream. Lo registriamo esplicitamente
    # invece di dipendere dalla versione dell'interprete.
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        '.woff2': 'font/woff2',
        '.woff': 'font/woff',
    }

    def log_message(self, format, *args):
        # Log only API requests to the console, ignore static asset prints
        if self.path.startswith('/api/'):
            sys.stdout.write(f"[{self.log_date_time_string()}] API REQUEST: {self.command} {self.path} -> Response Code: {args[1]}\n")
            sys.stdout.flush()

    def end_headers(self):
        # Force revalidation of static frontend assets (JS/CSS/HTML).
        # Without an explicit Cache-Control, both browsers and the pywebview
        # WebKit backend apply "heuristic caching" and keep serving an old
        # cached copy of the frontend WITHOUT revalidating — so after the
        # source is updated the running window still executes stale JS. That
        # is exactly what caused the multi-tool confirmation bug to persist
        # for some users while others (with a fresh cache) never saw it.
        # 'no-cache' does not mean "never cache": paired with the
        # Last-Modified/304 handling SimpleHTTPRequestHandler already does, it
        # means "always revalidate first", so unchanged files stay fast (304)
        # and changed files are always re-fetched. API responses are skipped
        # (they set their own cache semantics, e.g. the SSE stream).
        if not self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-cache, must-revalidate')
        super().end_headers()

    def do_GET(self):
        original_path = self.path
        self.path = self.path.split('?')[0]
        if self.path.startswith('/api/conversations'):
            self.handle_get_conversations()
        elif self.path.startswith('/api/settings'):
            self.handle_get_settings()
        elif self.path.startswith('/api/models/search'):
            self.handle_get_models_search(original_path)
        elif self.path.startswith('/api/models'):
            self.handle_get_models()
        elif self.path.startswith('/api/mcp/config-schema'):
            self.handle_get_mcp_schema(original_path)
        elif self.path.startswith('/api/mcp/search'):
            self.handle_get_mcp_search(original_path)
        elif self.path.startswith('/api/mcp'):
            self.handle_get_mcp()
        elif self.path.startswith('/api/tools'):
            self.handle_get_tools()
        elif self.path.startswith('/api/wiki'):
            self.handle_get_wiki()
        elif self.path.startswith('/api/skills'):
            self.handle_get_skills()
        elif self.path.startswith('/api/clients/status'):
            self.handle_get_clients_status()
        elif self.path.startswith('/api/clients/snippets'):
            self.handle_get_clients_snippets()
        elif self.path.startswith('/api/media'):
            self.handle_get_media(original_path)
        else:
            super().do_GET()

    def do_POST(self):
        self.path = self.path.split('?')[0]
        if self.path.startswith('/api/confirm'):
            self.handle_post_confirm()
        elif self.path.startswith('/api/cancel'):
            self.handle_post_cancel()
        elif self.path.startswith('/api/conversations'):
            self.handle_post_conversations()
        elif self.path.startswith('/api/settings'):
            self.handle_post_settings()
        elif self.path.startswith('/api/models/default'):
            self.handle_post_models_default()
        elif self.path.startswith('/api/models/download'):
            self.handle_post_models_download()
        elif self.path.startswith('/api/mcp/install'):
            self.handle_post_mcp_install()
        elif self.path.startswith('/api/tools/custom'):
            self.handle_post_tools_custom()
        elif self.path.startswith('/api/wiki'):
            self.handle_post_wiki()
        elif self.path.startswith('/api/skills'):
            self.handle_post_skills()
        elif self.path.startswith('/api/clients/mcp/setup'):
            self.handle_post_clients_mcp_setup()
        elif self.path.startswith('/api/open-file'):
            self.handle_post_open_file()
        else:
            self.send_error(404, "Not Found")

    def do_DELETE(self):
        self.path = self.path.split('?')[0]
        if self.path.startswith('/api/conversations'):
            self.handle_delete_conversation()
        elif self.path.startswith('/api/models/'):
            self.handle_delete_model()
        elif self.path.startswith('/api/mcp/uninstall/'):
            self.handle_delete_mcp()
        elif self.path.startswith('/api/tools/custom/'):
            self.handle_delete_tools_custom()
        elif self.path.startswith('/api/wiki/'):
            self.handle_delete_wiki()
        elif self.path.startswith('/api/skills/'):
            self.handle_delete_skills()
        else:
            self.send_error(404, "Not Found")

    def handle_post_cancel(self):
        """POST /api/cancel — stop the in-flight generation, if any. Runs on a
        separate handler thread from the blocked streaming request (the HTTP
        server is threaded), so it can interrupt it mid-stream."""
        try:
            cancelled = agent_manager.cancel_current()
            self._send_json({"cancelled": cancelled})
        except Exception as e:
            print("!!! [API POST CANCEL ERROR]", flush=True)
            traceback.print_exc()
            self._send_json({"error": str(e)}, status=500)

    def handle_post_confirm(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            confirm_id = payload.get("confirm_id")
            allow = payload.get("allow", False)
            
            found = False
            if confirm_id and confirm_id in agent_manager.pending_confirmations:
                found = True
                def resolve():
                    if confirm_id in agent_manager.pending_confirmations:
                        agent_manager.pending_confirmations[confirm_id]["result"] = allow
                        agent_manager.pending_confirmations[confirm_id]["event"].set()
                agent_manager.loop.call_soon_threadsafe(resolve)
                
            status_str = "ok" if found else "expired"
            body = json.dumps({"status": status_str}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            print("!!! [API POST ERROR] Exception in /api/confirm:", flush=True)
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_get_conversations(self):
        print(f"\n>>> [API GET] {self.path}", flush=True)
        try:
            parts = self.path.strip('/').split('/')
            if len(parts) == 3:  # GET /api/conversations/<id>
                conv_id = parts[2]
                print(f"--- [API GET] Fetching details for conversation UUID: {conv_id}", flush=True)
                conv = run_async(get_conversation(conv_id))
                if conv:
                    print(f"<<< [API GET] Found details with {len(conv.get('messages', []))} messages.", flush=True)
                    body = json.dumps(conv).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    print(f"<<< [API GET] Conversation NOT FOUND for UUID: {conv_id}", flush=True)
                    self.send_error(404, "Conversation not found")
            else:  # GET /api/conversations
                print("--- [API GET] Listing all conversations...", flush=True)
                convs = run_async(list_conversations())
                print(f"<<< [API GET] Found {len(convs)} conversations.", flush=True)
                body = json.dumps(convs).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            print("!!! [API GET ERROR] Exception occurred during fetch:", flush=True)
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_post_conversations(self):
        print(f"\n>>> [API POST] {self.path}", flush=True)
        try:
            parts = self.path.strip('/').split('/')
            if len(parts) == 4 and parts[3] == 'messages':  # POST /api/conversations/<id>/messages
                conv_id = parts[2]
                self.handle_post_message(conv_id)
                return

            if len(parts) == 4 and parts[3] == 'attach':  # POST /api/conversations/<id>/attach
                conv_id = parts[2]
                self.handle_post_attach(conv_id)
                return

            # Otherwise: POST /api/conversations (Create new conversation)
            content_length = int(self.headers.get('Content-Length', 0))
            payload = {}
            if content_length > 0:
                payload = json.loads(self.rfile.read(content_length).decode('utf-8'))

            print(f"--- [API POST] Creating conversation with payload: {payload}", flush=True)
            config = load_config()
            default_model = config.get("default_model", "qwen3.5-4b-instruct")

            # Create in SQLite DB
            conv_id = run_async(create_conversation(
                model_name=default_model,
                title=payload.get('title', 'New Conversation')
            ))

            # Retrieve created conversation
            conv = run_async(get_conversation(conv_id))
            print(f"<<< [API POST] Conversation created with UUID: {conv_id}", flush=True)

            body = json.dumps(conv).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            print("!!! [API POST ERROR] Exception occurred during creation:", flush=True)
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_post_message(self, conv_id):
        print(f"\n>>> [API POST MESSAGE] Conversation ID: {conv_id}", flush=True)
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = {}
            if content_length > 0:
                payload = json.loads(self.rfile.read(content_length).decode('utf-8'))

            role = payload.get('role', 'user')
            content = payload.get('content', '')
            print(f"--- [API POST MESSAGE] Content payload: '{content[:100]}...'", flush=True)

            # /remember <fact>: the GUI equivalent of the CLI chat command.
            # Not written to disk directly — the fact is forwarded to the model
            # as a normal turn, so IT picks (or creates) the right wiki page and
            # the write still passes through the standard update_wiki_page
            # confirmation (which appears as the GUI's Allow/Decline prompt).
            if content.strip().lower().startswith("/remember"):
                fact = content.strip()[len("/remember"):].strip()
                if fact:
                    content = (
                        f"{fact}\n\n"
                        "[The user asked to remember the fact above permanently. "
                        "Store it in your wiki with update_wiki_page, copying the "
                        "fact FAITHFULLY — the page content must state exactly the "
                        "fact above, never something invented. Add it to the "
                        "existing page it fits best (read the page first and keep "
                        "its still-valid content), or create a new page if none "
                        "fits. Then confirm in one short line where you stored it.]"
                    )

            # Merge any files staged for this conversation into the user turn,
            # mirroring the CLI: injected attachments (document text) and RAG
            # index notes go BEFORE the user's request, as one user message.
            # Done here — before persisting — so the document travels with the
            # message in the DB and stays available for follow-up questions on
            # resume, not just for the single turn it was attached on.
            attachments, index_notes = agent_manager.take_pending(conv_id)
            if attachments or index_notes:
                from llampaca.attachments import build_attachment_block
                blocks = [build_attachment_block(n, t) for n, t in attachments]
                blocks.extend(index_notes)
                content = "\n\n".join(blocks + [content])
                names = ", ".join(n for n, _ in attachments)
                if index_notes:
                    names = f"{names + ', ' if names else ''}{len(index_notes)} indexed doc(s)"
                print(f"--- [API POST MESSAGE] Merged attachments: {names}", flush=True)

            # 1. Add user message to DB
            user_msg_id = run_async(add_message(conv_id, role, content))
            print(f"--- [API POST MESSAGE] Saved user message with ID: {user_msg_id}", flush=True)

            # Fetch conversation to get full history
            conv_data = run_async(get_conversation(conv_id))
            if not conv_data:
                print("<<< [API POST MESSAGE] Conversation NOT FOUND", flush=True)
                self.send_error(404, "Conversation not found")
                return

            # Start Server-Sent Events (SSE) Response
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'close')
            self.send_header('Transfer-Encoding', 'chunked')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()
            self.wfile.flush()

            # Verify if Llama model server is active
            config = load_config()
            server_port = config.get("server_port", 8080)

            if not is_server_running(port=server_port):
                warn_msg = (
                    f"Il server dei modelli (llama-server) non è attivo sulla porta {server_port}. "
                    "Avvialo nel tuo terminale con 'llampaca run' per parlare con l'agente."
                )
                print(f"⚠️ [API POST MESSAGE] llama-server NOT running on port {server_port}! Sending warning to client...", flush=True)
                assistant_msg_id = run_async(add_message(conv_id, "assistant", warn_msg))
                self.emit_sse("text", warn_msg)
                self.emit_sse("done", {"assistant_message_id": assistant_msg_id})
                self.end_sse()
                return

            print(f"--- [API POST MESSAGE] llama-server is ACTIVE on port {server_port}. Enqueuing to AgentManager...", flush=True)
            
            q = queue.Queue()
            agent_manager.process_message(conv_id, content, user_msg_id, conv_data, config, q)
            
            while True:
                msg_type, *args = q.get()
                if msg_type == "event":
                    kind, data = args
                    if kind == "text":
                        self.emit_sse("text", data)
                        sys.stdout.write(data)
                        sys.stdout.flush()
                    elif kind == "reasoning":
                        self.emit_sse("reasoning", data)
                    elif kind == "tool_call":
                        self.emit_sse("tool_call", data)
                        print(f"\n  [tool] {data['name']}({data['arguments']})", flush=True)
                    elif kind == "tool_result":
                        self.emit_sse("tool_result", data)
                        preview = data["result"].replace("\n", " ")
                        if len(preview) > 120:
                            preview = preview[:120] + "..."
                        print(f"  [result] {preview}", flush=True)
                    elif kind == "tool_confirm_request":
                        self.emit_sse("tool_confirm_request", data)
                    elif kind == "tool_confirm_cancel":
                        self.emit_sse("tool_confirm_cancel", data)
                    elif kind == "summary_updated":
                        # The summary DB update is now handled by the Agent internally
                        # We just emit the SSE
                        self.emit_sse("summary_updated", data)
                    elif kind == "context_status":
                        self.emit_sse("context_status", data)
                    elif kind == "title_updated":
                        self.emit_sse("title_updated", data)
                    elif kind == "warning":
                        self.emit_sse("warning", data)
                        print(f"\n  [warning] {data}", flush=True)
                    elif kind == "error":
                        self.emit_sse("error", data)
                        print(f"\n  [error] {data}", flush=True)
                    elif kind == "cancelled":
                        self.emit_sse("cancelled", data)
                        print("\n  [cancelled] generation stopped by user", flush=True)
                elif msg_type == "done":
                    self.emit_sse("done", args[0])
                elif msg_type == "error":
                    self.emit_sse("error", args[0])
                    print(f"\n  [error] {args[0]}", flush=True)
                elif msg_type == "close":
                    self.end_sse()
                    break

        except Exception as e:
            print("!!! [API POST MESSAGE ERROR] Exception occurred:", flush=True)
            traceback.print_exc()
            try:
                self.send_error(500, str(e))
            except Exception:
                pass

    def handle_post_attach(self, conv_id):
        """
        Upload a file to stage for the conversation's next message (the GUI
        equivalent of the CLI's /attach). The raw file bytes are the request
        body; the original filename rides in the X-Attachment-Filename header
        (URL-encoded) because do_POST already stripped the query string and
        the suffix is what extract_text dispatches on.

        Responds with JSON describing whether the file was injected directly
        or indexed for RAG, or a 4xx/5xx with a user-facing error message.
        """
        import tempfile
        import os
        from urllib.parse import unquote

        print(f"\n>>> [API POST ATTACH] Conversation ID: {conv_id}", flush=True)
        tmp_path = None
        try:
            raw_name = self.headers.get('X-Attachment-Filename', '')
            filename = os.path.basename(unquote(raw_name)) or "attachment"

            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                self.send_error(400, "Empty upload")
                return
            data = self.rfile.read(content_length)

            # Persist to a temp file preserving the suffix: extract_text keys
            # off the extension, and pypdf/python-docx read from a path.
            suffix = Path(filename).suffix
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(data)

            from llampaca.attachments import extract_text, AttachmentError
            try:
                text = extract_text(Path(tmp_path))
            except AttachmentError as e:
                # User-facing extraction problem (unsupported type, encrypted
                # PDF, empty file...): 400 with the message shown verbatim.
                self._send_json({"error": str(e)}, status=400)
                return

            config = load_config()
            context_size = config.get("context_size", DEFAULT_CONTEXT_SIZE)

            # Staging (and, for large files, indexing) happens on the manager's
            # event loop: index_document is async and shares its embedding
            # server. run_coroutine_threadsafe blocks this HTTP thread until
            # it finishes, which is exactly the request/response we want.
            future = asyncio.run_coroutine_threadsafe(
                agent_manager.stage_attachment(
                    conv_id, filename, text, context_size
                ),
                agent_manager.loop,
            )
            result = future.result()
            print(f"<<< [API POST ATTACH] {result}", flush=True)
            self._send_json(result)

        except Exception as e:
            print("!!! [API POST ATTACH ERROR] Exception occurred:", flush=True)
            traceback.print_exc()
            self._send_json({"error": str(e)}, status=500)
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _send_json(self, obj, status=200):
        """Serialize a dict as a JSON response (small helper for the attach
        endpoint's success/error replies)."""
        body = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_delete_conversation(self):
        print(f"\n>>> [API DELETE] {self.path}", flush=True)
        try:
            parts = self.path.strip('/').split('/')
            if len(parts) == 3:  # DELETE /api/conversations/<id>
                conv_id = parts[2]
                run_async(delete_conversation(conv_id))
                print(f"<<< [API DELETE] Deleted conversation UUID: {conv_id}", flush=True)
                body = json.dumps({"success": True}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                print("<<< [API DELETE] Invalid path structure", flush=True)
                self.send_error(400, "Bad Request")
        except Exception as e:
            print("!!! [API DELETE ERROR] Exception occurred:", flush=True)
            traceback.print_exc()
            self.send_error(500, str(e))

    # WebKit (Safari and the pywebview WKWebView backend) buffers a streamed
    # fetch() response body and does not hand small chunks to the JS
    # ReadableStream reader until roughly ~1 KB has accumulated. Chrome/Blink
    # delivers each chunk immediately. That buffering deadlocks the tool
    # confirmation flow: an isolated `tool_confirm_request` event that is not
    # followed by more streamed bytes stays trapped in WebKit's buffer, so the
    # UI never shows the prompt and the server blocks forever waiting for a
    # confirmation the user can't give. (The FIRST confirmation usually works
    # because enough model text streamed just before it to flush the buffer;
    # a SECOND back-to-back tool call often has little text in between, so it
    # hangs — exactly the reported symptom.) Padding every event up to this
    # size with an ignored SSE comment line guarantees each event on its own
    # exceeds the threshold and is delivered instantly.
    # 65536 is used to guarantee flushing of all browser network buffers (Safari and some WebKit variants buffer up to 64KB of streaming responses).
    _SSE_MIN_CHUNK_BYTES = 65536

    def emit_sse(self, kind, data):
        """Helper to send event stream chunks to the frontend."""
        try:
            event_data = json.dumps({"kind": kind, "data": data})
            body = f"data: {event_data}\n"
            # Pad to _SSE_MIN_CHUNK_BYTES with an SSE comment line (starts with
            # ':'). The frontend parser only reads lines beginning with
            # "data: ", so the padding is inert; native EventSource would treat
            # it as a comment too. The trailing blank line terminates the event.
            pad_needed = self._SSE_MIN_CHUNK_BYTES - len(body.encode('utf-8')) - 3
            if pad_needed > 0:
                body += ":" + (" " * pad_needed) + "\n"
            body += "\n"
            payload = body.encode('utf-8')
            chunk_size = f"{len(payload):X}\r\n".encode('utf-8')
            self.wfile.write(chunk_size + payload + b"\r\n")
            self.wfile.flush()
        except Exception:
            pass

    def end_sse(self):
        """Helper to send the final zero chunk."""
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass

    def handle_get_settings(self):
        try:
            config = load_config()
            body = json.dumps(config).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    # ------------------------------------------------------------------ #
    # Personal wiki ("Profilo") — view/edit the same ~/.llampaca/wiki     #
    # pages the model reads/writes and the CLI's /remember stores.        #
    # ------------------------------------------------------------------ #
    def handle_get_wiki(self):
        """GET /api/wiki -> list of {name, description};
        GET /api/wiki/<name> -> {name, content} for one page."""
        from urllib.parse import unquote
        from llampaca import wiki
        try:
            parts = self.path.strip('/').split('/')
            if len(parts) == 3:  # /api/wiki/<name>
                name = unquote(parts[2])
                try:
                    content = wiki.read_page(name)
                except FileNotFoundError:
                    self._send_json({"error": "Pagina non trovata"}, status=404)
                    return
                self._send_json({"name": wiki.slugify(name), "content": content})
            else:  # /api/wiki
                pages = [
                    {"name": name, "description": desc}
                    for name, desc in wiki.list_pages()
                ]
                # max_chars lets the editor warn before the write would be
                # rejected server-side (same cap the model's tool obeys).
                self._send_json({"pages": pages, "max_chars": wiki.MAX_PAGE_CHARS})
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": str(e)}, status=500)

    def handle_post_wiki(self):
        """POST /api/wiki with {name, content} -> create/overwrite a page.
        Returns {name} with the slugified name actually written."""
        from llampaca import wiki
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8')) if content_length else {}
            name = (payload.get("name") or "").strip()
            content = payload.get("content", "")
            if not name:
                self._send_json({"error": "Il nome della pagina è obbligatorio."}, status=400)
                return
            try:
                # write_page enforces the same rules as the model's tool:
                # non-empty content and the whole-page size cap.
                slug = wiki.write_page(name, content)
            except ValueError as e:
                self._send_json({"error": str(e)}, status=400)
                return
            self._send_json({"name": slug})
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": str(e)}, status=500)

    def handle_delete_wiki(self):
        """DELETE /api/wiki/<name> -> remove a page file. Deleting is a user
        action (the model can only write), which is exactly what this is."""
        from urllib.parse import unquote
        from llampaca import wiki
        try:
            parts = self.path.strip('/').split('/')
            if len(parts) != 3:
                self.send_error(400, "Bad Request")
                return
            name = unquote(parts[2])
            path = wiki.page_path(name)  # slugified, inside WIKI_DIR
            if path.is_file():
                path.unlink()
            self._send_json({"success": True, "name": wiki.slugify(name)})
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": str(e)}, status=500)

    # ------------------------------------------------------------------ #
    # Markdown Skills ("Skills .md") — view/edit/import ~/.llampaca/skills #
    # ------------------------------------------------------------------ #
    def handle_get_skills(self):
        """GET /api/skills -> list of all skills with metadata;
        GET /api/skills/<name> -> {slug, name, content} for one skill."""
        from urllib.parse import unquote
        from llampaca import skills
        try:
            parts = self.path.strip('/').split('/')
            if len(parts) == 3:  # /api/skills/<name>
                name = unquote(parts[2])
                try:
                    content = skills.read_skill(name)
                except FileNotFoundError:
                    self._send_json({"error": "Skill non trovata"}, status=404)
                    return
                self._send_json({"slug": skills.slugify(name), "content": content})
            else:  # /api/skills
                skill_list = skills.list_skills()
                self._send_json({"skills": skill_list, "max_chars": skills.MAX_SKILL_CHARS})
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": str(e)}, status=500)

    def handle_post_skills(self):
        """POST /api/skills with {name, content, url} -> create, overwrite or import a skill.
        Returns {slug} with the slugified name written."""
        from llampaca import skills
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8')) if content_length else {}
            name = (payload.get("name") or "").strip()
            content = payload.get("content", "")
            url = (payload.get("url") or "").strip()

            if url and not content.strip():
                import urllib.request
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    content = resp.read().decode('utf-8', errors='replace')
                if not name:
                    name = url.split('/')[-1].replace('.md', '')

            if not name:
                self._send_json({"error": "Il nome della skill è obbligatorio."}, status=400)
                return

            try:
                slug = skills.write_skill(name, content)
            except ValueError as e:
                self._send_json({"error": str(e)}, status=400)
                return
            self._send_json({"slug": slug, "success": True})
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": str(e)}, status=500)

    def handle_delete_skills(self):
        """DELETE /api/skills/<name> -> remove a skill file."""
        from urllib.parse import unquote
        from llampaca import skills
        try:
            parts = self.path.strip('/').split('/')
            if len(parts) != 3:
                self.send_error(400, "Bad Request")
                return
            name = unquote(parts[2])
            deleted = skills.delete_skill(name)
            self._send_json({"success": deleted, "slug": skills.slugify(name)})
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": str(e)}, status=500)

    def handle_post_settings(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            
            config = load_config()
            need_restart = False
            
            # Map keys and check differences
            if "server_port" in payload and payload["server_port"] != config.get("server_port"):
                config["server_port"] = int(payload["server_port"])
                need_restart = True
            if "context_size" in payload and payload["context_size"] != config.get("context_size"):
                config["context_size"] = int(payload["context_size"])
                need_restart = True
            if "n_threads" in payload and payload["n_threads"] != config.get("n_threads"):
                config["n_threads"] = int(payload["n_threads"])
                need_restart = True
            if "gpu_layers" in payload and payload["gpu_layers"] != config.get("gpu_layers"):
                config["gpu_layers"] = int(payload["gpu_layers"])
                need_restart = True

            # The embedder's GPU offload is independent from the chat model's:
            # it drives a SEPARATE, lazily-started llama-server, so changing it
            # must NOT restart the chat server (that would interrupt the
            # conversation for a setting the chat server ignores). We only
            # persist it and drop any already-built EmbeddingService, so the
            # next RAG/attach rebuilds the embedding server with the new value.
            embedding_gpu_changed = False
            if "embedding_gpu_layers" in payload and payload["embedding_gpu_layers"] != config.get("embedding_gpu_layers"):
                config["embedding_gpu_layers"] = int(payload["embedding_gpu_layers"])
                embedding_gpu_changed = True

            # Thinking on/off is applied per request (the Agent reads it from
            # the config on every turn), so it needs no restart: the very next
            # message uses the new value. Driven from the chat composer, where
            # it is a per-turn decision rather than a configuration.
            if "no_think" in payload:
                config["no_think"] = bool(payload["no_think"])

            from llampaca.config import save_config
            save_config(config)

            # The manager caches the config it was started with; without this
            # the Agent would keep reading the old value until the next launch.
            if agent_manager.config is not None:
                agent_manager.config = config

            # No cache re-priming when no_think changes: measured against
            # llama-server, the toggle only alters the tail of the rendered
            # prompt (the assistant's generation prefix), not the system block,
            # so the warmed prefix stays valid — 13 tokens to compute after a
            # switch versus 2271 from cold. Re-priming would have stalled the
            # next message by ~8 s for nothing.

            if embedding_gpu_changed:
                try:
                    if agent_manager.embedding_service is not None:
                        agent_manager.embedding_service.stop()
                        agent_manager.embedding_service = None
                    print("[GUI Server] Embedding GPU layers changed; embedding service reset.", flush=True)
                except Exception as e:
                    print(f"[GUI Server] Could not reset embedding service: {e}", flush=True)
            
            if need_restart:
                print(f"[GUI Server] Config changed. Restarting model server...", flush=True)
                from llampaca.engine.server import restart_active_server
                success = restart_active_server(
                    port=config.get("server_port"),
                    context_size=config.get("context_size"),
                    n_threads=config.get("n_threads"),
                    gpu_layers=config.get("gpu_layers")
                )
                if not success:
                    raise Exception("Impossibile riavviare il server dei modelli.")
            
            body = json.dumps({"status": "ok", "config": config}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_get_models(self):
        try:
            from llampaca.config import MODELS_DIR, MODEL_PRESETS, load_config
            from llampaca.engine.downloader import active_downloads, downloads_lock
            
            config = load_config()
            default_model = config.get("default_model", "")
            # The embedding default is tracked SEPARATELY from the chat default:
            # picking an embedding model must never overwrite the chat model
            # (and vice versa), so the two "active" flags are computed against
            # two different config keys below.
            embedding_model = config.get("embedding_model", "")
            image_model = config.get("image_model", "")

            # List local GGUF files
            from pathlib import Path
            gguf_files = list(MODELS_DIR.glob("*.gguf"))
            installed_filenames = {f.name for f in gguf_files}
            installed_paths = {f.name: f for f in gguf_files}
            
            # Clean up completed or failed downloads from active_downloads
            with downloads_lock:
                to_remove = []
                for fname, dl in active_downloads.items():
                    if dl["status"] in ["completed", "failed"]:
                        if fname in installed_filenames or dl["status"] == "failed":
                            to_remove.append(fname)
                for fname in to_remove:
                    del active_downloads[fname]
            
            models_list = []
            
            # 1. Add presets (either installed, downloading, or available to download)
            for p_name, preset in MODEL_PRESETS.items():
                filename = preset["file"]
                repo_id = preset["repo"]
                desc = preset["description"]
                
                # "kind" separates chat (LLM) models from embedding models.
                # Presets without an explicit "kind" are chat models (see
                # config.MODEL_PRESETS). The active flag is compared against the
                # matching config key: embedding models against embedding_model,
                # chat models against default_model.
                kind = preset.get("kind", "chat")
                is_installed = filename in installed_filenames
                if kind == "embedding":
                    is_active = (embedding_model == p_name or embedding_model == filename)
                elif kind == "image":
                    is_active = (image_model == p_name or image_model == filename)
                else:
                    is_active = (default_model == p_name or default_model == filename)

                is_downloading = False
                progress = 0
                with downloads_lock:
                    if filename in active_downloads:
                        dl = active_downloads[filename]
                        if dl["status"] == "downloading":
                            is_downloading = True
                            progress = dl["progress"]
                        elif dl["status"] == "completed":
                            is_installed = True
                            
                size_str = "Sconosciuta"
                quant_str = "Sconosciuta"
                if is_installed and filename in installed_paths:
                    file = installed_paths[filename]
                    size_bytes = file.stat().st_size
                    from llampaca.cli import format_size
                    size_str = format_size(size_bytes)
                    
                    name_lower = filename.lower()
                    for q in ["q4_k_m", "q8_0", "q4_0", "q4_k_s", "q5_k_m", "q5_k_s", "q6_k", "q2_k", "f16"]:
                        if q in name_lower:
                            quant_str = q.upper()
                            break
                else:
                    size_gb = preset.get("size_gb", 0)
                    size_str = f"{size_gb} GB" if size_gb else "Consigliato"
                    if "q8_0" in filename.lower():
                        quant_str = "Q8_0"
                    elif "q4_k_m" in filename.lower():
                        quant_str = "Q4_K_M"
                    elif "q4_0" in filename.lower():
                        quant_str = "Q4_0"
                        
                models_list.append({
                    "id": filename,
                    "name": filename,
                    "preset_id": p_name,
                    "description": desc,
                    "size": size_str,
                    "quant": quant_str,
                    "installed": is_installed,
                    "downloading": is_downloading,
                    "progress": progress,
                    "active": is_active,
                    "repo_id": repo_id,
                    "kind": kind
                })
                
            # 2. Add other installed GGUF files (not in presets)
            preset_filenames = {preset["file"] for preset in MODEL_PRESETS.values()}
            for filename in installed_filenames:
                if filename in preset_filenames:
                    continue
                with downloads_lock:
                    if filename in active_downloads and active_downloads[filename]["status"] == "downloading":
                        continue
                
                file = installed_paths[filename]
                size_bytes = file.stat().st_size
                from llampaca.cli import format_size
                size_str = format_size(size_bytes)
                
                quant_str = "Sconosciuta"
                name_lower = filename.lower()
                for q in ["q4_k_m", "q8_0", "q4_0", "q4_k_s", "q5_k_m", "q5_k_s", "q6_k", "q2_k", "f16"]:
                    if q in name_lower:
                        quant_str = q.upper()
                        break
                
                kind = "chat"
                is_active = (default_model == filename)
                if any(kw in name_lower for kw in ["sdxl", "sd1", "sd2", "sd3", "flux", "stable-diffusion", "diffusion"]):
                    kind = "image"
                    is_active = (image_model == filename)
                
                models_list.append({
                    "id": filename,
                    "name": filename,
                    "preset_id": None,
                    "description": "Modello GGUF personalizzato caricato localmente.",
                    "size": size_str,
                    "quant": quant_str,
                    "installed": True,
                    "downloading": False,
                    "progress": 100,
                    "active": is_active,
                    "repo_id": None,
                    "kind": kind
                })
                
            # 3. Add other active custom downloads (not in presets)
            with downloads_lock:
                for filename, dl in active_downloads.items():
                    if filename in preset_filenames:
                        continue
                    if dl["status"] != "downloading":
                        continue
                        
                    models_list.append({
                        "id": filename,
                        "name": filename,
                        "preset_id": None,
                        "description": f"Download in corso da repository {dl['repo_id']}...",
                        "size": "In scaricamento",
                        "quant": "Sconosciuta",
                        "installed": False,
                        "downloading": True,
                        "progress": dl["progress"],
                        "active": False,
                        "repo_id": dl["repo_id"],
                        "kind": "chat"
                    })
                    
            # Sort: active first, then installed, then downloading, then available
            def sort_key(m):
                if m["active"]:
                    return 0
                if m["installed"]:
                    return 1
                if m["downloading"]:
                    return 2
                return 3
                
            models_list.sort(key=sort_key)
            
            body = json.dumps(models_list).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_post_models_download(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            
            repo_id = payload.get("repo_id")
            filename = payload.get("filename")
            input_val = payload.get("input_val")
            
            if input_val:
                parsed_repo, parsed_file = parse_hf_input(input_val)
                if parsed_repo and parsed_file:
                    repo_id = parsed_repo
                    filename = parsed_file
                    
            if not repo_id or not filename:
                raise Exception("Impossibile identificare repository HF o nome file. Usa il formato 'utente/repo/nomefile.gguf'.")
                
            # Start background download
            from llampaca.engine.downloader import download_hf_model_async
            download_hf_model_async(repo_id, filename)
            
            body = json.dumps({"status": "download_started", "repo_id": repo_id, "filename": filename}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            
        except Exception as e:
            traceback.print_exc()
            self.send_error(400, str(e))


    def handle_get_models_search(self, original_path):
        try:
            import urllib.parse
            query_str = ""
            if "?" in original_path:
                query_str = original_path.split("?", 1)[1]
            
            params = urllib.parse.parse_qs(query_str)
            search_query = params.get("q", [""])[0].strip()
            page = int(params.get("page", ["1"])[0].strip())
            
            limit = 8
            results = search_hf_models(query=search_query, page=page, limit=limit)
            has_next = len(results) == limit
            
            response_data = {
                "models": results,
                "pagination": {
                    "currentPage": page,
                    "hasNextPage": has_next
                }
            }
            
            body = json.dumps(response_data).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_get_mcp(self):
        try:
            from llampaca.config import MCP_CONFIG_PATH, load_mcp_config
            if MCP_CONFIG_PATH.exists():
                try:
                    mtime = MCP_CONFIG_PATH.stat().st_mtime
                    if mtime > agent_manager.mcp_config_mtime:
                        agent_manager.mcp_config_mtime = mtime
                        import asyncio
                        fut = asyncio.run_coroutine_threadsafe(agent_manager.reload_mcp_manager(), agent_manager.loop)
                        fut.result(timeout=60.0)
                except Exception as e:
                    print(f"Error checking/reloading MCP config on get: {e}", flush=True)

            mcp_config = load_mcp_config()
            servers = mcp_config.get("mcp_servers", {})
            
            result_list = []
            for name, cfg in servers.items():
                connected = False
                tools_count = 0
                if agent_manager.mcp_manager and name in agent_manager.mcp_manager.sessions:
                    connected = True
                    tools_count = sum(1 for t in agent_manager.registry.names() if t.startswith(f"{name}__"))
                
                result_list.append({
                    "name": name,
                    "command": cfg.get("command"),
                    "args": cfg.get("args", []),
                    "env": cfg.get("env", {}),
                    "connected": connected,
                    "toolsCount": tools_count
                })
                
            body = json.dumps(result_list).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_get_mcp_schema(self, original_path):
        try:
            import urllib.parse
            import requests
            
            query_str = ""
            if "?" in original_path:
                query_str = original_path.split("?", 1)[1]
                
            params = urllib.parse.parse_qs(query_str)
            name = params.get("name", [""])[0].strip()
            
            if not name:
                raise Exception("Parametro 'name' obbligatorio.")
                
            url = f"https://api.smithery.ai/servers/{name}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            config_schema = {"type": "object", "properties": {}}
            connections = data.get("connections", [])
            if connections:
                config_schema = connections[0].get("configSchema", config_schema)
                
            body = json.dumps(config_schema).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_get_mcp_search(self, original_path):
        try:
            import urllib.parse
            import requests
            
            query_str = ""
            if "?" in original_path:
                query_str = original_path.split("?", 1)[1]
                
            params = urllib.parse.parse_qs(query_str)
            search_query = params.get("q", [""])[0].strip()
            page = params.get("page", ["1"])[0].strip()

            url = f"https://api.smithery.ai/servers?pageSize=12&page={page}"
            if search_query:
                url += f"&q={urllib.parse.quote(search_query)}"
                
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            servers = data.get("servers", [])
            mapped_servers = []
            for s in servers:
                mapped_servers.append({
                    "name": s.get("displayName") or s.get("qualifiedName"),
                    "slug": s.get("qualifiedName"),
                    "description": s.get("description"),
                    "repository": {"url": s.get("homepage") or f"https://smithery.ai/server/{s.get('qualifiedName')}"},
                    "environmentVariablesJsonSchema": {"type": "object", "properties": {}}
                })
            
            response_data = {
                "servers" : mapped_servers,
                "pagination": data.get("pagination", {"currentPage":1, "totalPages":1})
            }

            body = json.dumps(response_data).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_post_mcp_install(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            
            name = payload.get("name")
            command = payload.get("command")
            args = payload.get("args", [])
            env = payload.get("env", {})
            
            if not name or not command:
                raise Exception("Parametri 'name' e 'command' obbligatori.")
                
            from llampaca.config import load_mcp_config, save_mcp_config
            mcp_config = load_mcp_config()
            mcp_config["mcp_servers"][name] = {
                "command": command,
                "args": args,
                "env": env
            }
            save_mcp_config(mcp_config)
            
            # Reload MCP manager thread-safely
            import asyncio
            fut = asyncio.run_coroutine_threadsafe(agent_manager.reload_mcp_manager(), agent_manager.loop)
            fut.result(timeout=60.0)
            
            body = json.dumps({"success": True}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_delete_mcp(self):
        try:
            parts = self.path.strip('/').split('/')
            if len(parts) < 4:
                raise Exception("Nome server MCP non specificato.")
            import urllib.parse
            name = urllib.parse.unquote(parts[3])
            
            from llampaca.config import load_mcp_config, save_mcp_config
            mcp_config = load_mcp_config()
            if name in mcp_config.get("mcp_servers", {}):
                del mcp_config["mcp_servers"][name]
                save_mcp_config(mcp_config)
                
            # Reload MCP manager thread-safely
            import asyncio
            fut = asyncio.run_coroutine_threadsafe(agent_manager.reload_mcp_manager(), agent_manager.loop)
            fut.result(timeout=60.0)
            
            body = json.dumps({"success": True}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_post_models_default(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            model_name = payload.get("model_name")
            # "kind" tells us WHICH default to change: the chat model
            # (default_model) or the embedding model (embedding_model). It
            # defaults to "chat" so older frontends keep working unchanged.
            kind = payload.get("kind", "chat")

            if not model_name:
                raise Exception("model_name non specificato.")

            from llampaca.config import save_config
            config = load_config()

            if kind == "embedding":
                # Embedding default: only persist it. The embedding llama-server
                # is started lazily per chat session (see EmbeddingService), so
                # there is no long-lived chat server to restart here — the new
                # embedder is picked up the next time embeddings are needed.
                config["embedding_model"] = model_name
                save_config(config)
                # Drop any already-constructed EmbeddingService so the next
                # RAG/attach operation rebuilds it against the new model instead
                # of the stale one resolved at construction time.
                try:
                    if agent_manager.embedding_service is not None:
                        agent_manager.embedding_service.stop()
                        agent_manager.embedding_service = None
                except Exception as e:
                    print(f"[GUI Server] Could not reset embedding service: {e}", flush=True)
                print(f"[GUI Server] Default embedding model changed to {model_name}.", flush=True)
                body = json.dumps({"status": "ok", "embedding_model": model_name}).encode('utf-8')
            elif kind == "image":
                config["image_model"] = model_name
                save_config(config)
                print(f"[GUI Server] Default image model changed to {model_name}.", flush=True)
                body = json.dumps({"status": "ok", "image_model": model_name}).encode('utf-8')
            else:
                config["default_model"] = model_name
                save_config(config)

                print(f"[GUI Server] Default model changed to {model_name}. Restarting llama-server...", flush=True)
                from llampaca.engine.server import restart_active_server
                success = restart_active_server(model_name=model_name)
                if not success:
                    raise Exception("Impossibile caricare il nuovo modello.")

                body = json.dumps({"status": "ok", "default_model": model_name}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_delete_model(self):
        try:
            parts = self.path.strip('/').split('/')
            if len(parts) < 3:
                raise Exception("Nome modello non specificato.")
            
            model_filename = "/".join(parts[2:])
            import urllib.parse
            model_filename = urllib.parse.unquote(model_filename)
            
            config = load_config()
            default_model = config.get("default_model", "")
            embedding_model = config.get("embedding_model", "")

            from llampaca.config import MODEL_PRESETS
            # Resolve both active defaults (chat and embedding) to their GGUF
            # filenames so we can block deletion whether the config stores a
            # preset name or a raw filename.
            preset_file = None
            if default_model in MODEL_PRESETS:
                preset_file = MODEL_PRESETS[default_model]["file"]
            embedding_preset_file = None
            if embedding_model in MODEL_PRESETS:
                embedding_preset_file = MODEL_PRESETS[embedding_model]["file"]

            if model_filename in (default_model, preset_file):
                raise Exception("Non è possibile eliminare il modello di chat attualmente attivo/predefinito.")
            if model_filename in (embedding_model, embedding_preset_file):
                raise Exception("Non è possibile eliminare il modello di embedding attualmente predefinito.")
                
            from llampaca.config import MODELS_DIR
            model_path = MODELS_DIR / model_filename
            if not model_path.exists():
                raise Exception("Modello non trovato su disco.")
                
            model_path.unlink()
            print(f"[GUI Server] Deleted model file: {model_filename}", flush=True)
            
            body = json.dumps({"success": True}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_get_tools(self):
        try:
            if agent_manager.registry is None:
                body = json.dumps([]).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            import inspect
            results = []
            for name, tool in agent_manager.registry._tools.items():
                is_mcp = False
                mcp_server = ""
                display_name = name
                if "__" in name:
                    is_mcp = True
                    parts = name.split("__", 1)
                    mcp_server = parts[0]
                    display_name = parts[1]

                is_custom = False
                source_code = ""
                file_path = ""
                try:
                    file_path = inspect.getsourcefile(tool.func) or ""
                    if "custom_tools" in file_path:
                        is_custom = True
                        source_code = inspect.getsource(tool.func)
                except Exception:
                    pass

                results.append({
                    "name": name,
                    "display_name": display_name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    "requires_confirmation": tool.requires_confirmation,
                    "is_custom": is_custom,
                    "is_mcp": is_mcp,
                    "mcp_server": mcp_server,
                    "file_path": file_path,
                    "source_code": source_code
                })

            # Sort tools: custom first, then built-in, then MCP, alphabetically
            def sort_key(t):
                if t["is_custom"]:
                    return (0, t["name"])
                elif t["is_mcp"]:
                    return (2, t["mcp_server"], t["name"])
                else:
                    return (1, t["name"])

            results.sort(key=sort_key)

            body = json.dumps(results).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_post_tools_custom(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            
            name = payload.get("name", "").strip()
            code = payload.get("code", "")
            requires_confirmation = bool(payload.get("requires_confirmation", False))

            if not name:
                raise Exception("Il nome dello strumento è obbligatorio.")
            
            import re
            if not re.match("^[a-zA-Z0-9_]+$", name):
                raise Exception("Nome non valido. Usa solo lettere, numeri e underscore.")

            # Ensure the primary function in code is named `name`
            if re.search(r'^\s*def\s+[a-zA-Z0-9_]+', code, flags=re.MULTILINE):
                code = re.sub(r'^\s*def\s+[a-zA-Z0-9_]+', f'def {name}', code, count=1, flags=re.MULTILINE)

            # Validate syntax using ast and extract top-level function names only
            import ast
            try:
                tree = ast.parse(code)
            except SyntaxError as se:
                raise Exception(f"Errore di sintassi in Python: {se.msg} alla riga {se.lineno}")

            # Strip existing requires_confirmation lines to prevent duplicate accumulation
            cleaned_code = re.sub(r'\n[a-zA-Z0-9_]+\.requires_confirmation\s*=\s*(True|False)\s*', '', code)
            tree_cleaned = ast.parse(cleaned_code)

            # Find all TOP-LEVEL function definitions (ignore nested inner functions)
            func_names = [
                node.name for node in tree_cleaned.body
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
            ]
            if not func_names:
                raise Exception("Il codice deve contenere almeno una funzione pubblica (def nome_funzione(...):).")

            # Append requires_confirmation flag for each top-level public function
            flag_lines = "".join(f"\n{fn}.requires_confirmation = {requires_confirmation}\n" for fn in func_names)
            code_to_write = cleaned_code.rstrip() + "\n" + flag_lines

            from llampaca.config import LLAMPACA_DIR
            custom_dir = LLAMPACA_DIR / "custom_tools"
            custom_dir.mkdir(parents=True, exist_ok=True)

            file_path = custom_dir / f"{name}.py"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code_to_write)

            # Reload registry thread-safely
            import asyncio
            fut = asyncio.run_coroutine_threadsafe(agent_manager.reload_registry(), agent_manager.loop)
            fut.result(timeout=60.0)

            body = json.dumps({"success": True}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(400, str(e))

    def handle_delete_tools_custom(self):
        try:
            parts = self.path.strip('/').split('/')
            if len(parts) < 4:
                raise Exception("Nome dello strumento non specificato.")
            
            import urllib.parse
            name = urllib.parse.unquote(parts[3]).strip()

            from llampaca.config import LLAMPACA_DIR
            custom_dir = LLAMPACA_DIR / "custom_tools"
            
            # 1. Direct file stem match
            file_path = custom_dir / f"{name}.py"
            if file_path.exists():
                file_path.unlink()

            # 2. Search for any .py file in custom_tools containing function `name`
            if custom_dir.exists():
                for py_file in list(custom_dir.glob("*.py")):
                    try:
                        with open(py_file, "r", encoding="utf-8") as f:
                            content = f.read()
                        import ast
                        tree = ast.parse(content)
                        funcs = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
                        if name in funcs or py_file.stem == name:
                            py_file.unlink()
                    except Exception:
                        pass

            # Evict cached modules from sys.modules
            import sys
            modules_to_del = [m for m in sys.modules if m.startswith("llampaca_custom_")]
            for m in modules_to_del:
                if name in m or f"llampaca_custom_{name}" == m:
                    del sys.modules[m]

            # Reload registry thread-safely
            import asyncio
            fut = asyncio.run_coroutine_threadsafe(agent_manager.reload_registry(), agent_manager.loop)
            fut.result(timeout=60.0)

            body = json.dumps({"success": True}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_get_clients_status(self):
        try:
            paths = _get_client_config_paths()
            cmd = _resolve_llampaca_command()
            
            # Check Claude Desktop
            claude_cfg = paths["claude_desktop"]
            claude_installed = claude_cfg.parent.exists() if claude_cfg else False
            claude_mcp = False
            if claude_cfg and claude_cfg.exists():
                try:
                    with open(claude_cfg, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    claude_mcp = "llampaca" in data.get("mcpServers", {})
                except Exception:
                    pass

            # Check VS Code
            vscode_installed = any(p.parent.exists() for p in paths["vscode"])
            vscode_mcp = False
            vscode_active_path = None
            for p in paths["vscode"]:
                if p.exists():
                    vscode_active_path = str(p)
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if "llampaca" in data.get("mcpServers", {}) or "llampaca" in data.get("servers", {}):
                            vscode_mcp = True
                            break
                    except Exception:
                        pass

            # Check Continue.dev
            cont_cfg = paths["continue"]
            cont_installed = cont_cfg.parent.exists()
            cont_connected = False
            if cont_cfg.exists():
                try:
                    with open(cont_cfg, "r", encoding="utf-8") as f:
                        content = f.read()
                    if "127.0.0.1" in content or "localhost" in content or "Llampaca" in content:
                        cont_connected = True
                except Exception:
                    pass

            res = {
                "llampaca_command": cmd,
                "vscode": {
                    "installed": vscode_installed,
                    "mcp_connected": vscode_mcp,
                    "target_path": vscode_active_path or str(paths["vscode"][0])
                },
                "claude_desktop": {
                    "installed": claude_installed,
                    "mcp_connected": claude_mcp,
                    "target_path": str(claude_cfg) if claude_cfg else ""
                },
                "continue": {
                    "installed": cont_installed,
                    "connected": cont_connected,
                    "target_path": str(cont_cfg)
                }
            }
            body = json.dumps(res).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_post_clients_mcp_setup(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))

            target = payload.get("target")  # "vscode" or "claude_desktop"
            action = payload.get("action", "install")  # "install" or "remove"

            paths = _get_client_config_paths()
            cmd = _resolve_llampaca_command()

            target_files = []
            if target == "vscode":
                target_files = paths["vscode"]
            elif target == "claude_desktop":
                if paths["claude_desktop"]:
                    target_files = [paths["claude_desktop"]]

            if not target_files:
                raise Exception(f"Target '{target}' non valido o non supportato su questo OS.")

            updated_any = False
            for file_path in target_files:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                data = {}
                if file_path.exists():
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        data = {}
                
                mcp_key = "mcpServers"
                if mcp_key not in data or not isinstance(data[mcp_key], dict):
                    data[mcp_key] = {}

                if action == "install":
                    data[mcp_key]["llampaca"] = {
                        "command": cmd,
                        "args": ["mcp"]
                    }
                else:
                    data[mcp_key].pop("llampaca", None)

                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                updated_any = True

            body = json.dumps({"success": True, "updated": updated_any}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(400, str(e))

    def handle_get_clients_snippets(self):
        try:
            config = load_config()
            port = config.get("server_port", 8080)
            active_model = config.get("default_model", "qwen3.5-4b-instruct")
            cmd = _resolve_llampaca_command()
            base_url = f"http://127.0.0.1:{port}/v1"

            snippets = {
                "endpoint_url": base_url,
                "active_model": active_model,
                "llampaca_cmd": cmd,
                "continue": f"""name: Llampaca Config
version: 1.0.0
schema: v1

models:
  - name: Llampaca Local
    provider: openai
    model: {active_model}
    apiBase: {base_url}
    roles:
      - chat
      - edit
      - apply
      - autocomplete""",
                "cline_roo": json.dumps({
                    "apiProvider": "openai-compatible",
                    "openAiBaseUrl": base_url,
                    "openAiModelId": active_model,
                    "openAiApiKey": "not-needed"
                }, indent=2),
                "mcp": json.dumps({
                    "mcpServers": {
                        "llampaca": {
                            "command": cmd,
                            "args": ["mcp"]
                        }
                    }
                }, indent=2),
                "python": f"""from openai import OpenAI

client = OpenAI(
    base_url="{base_url}",
    api_key="not-needed"
)

response = client.chat.completions.create(
    model="{active_model}",
    messages=[{{"role": "user", "content": "Ciao!"}}]
)
print(response.choices[0].message.content)"""
            }

            body = json.dumps(snippets).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_get_media(self, original_path):
        """Serves local media files (e.g., generated images in LlampacaDocs) to the webview UI."""
        try:
            import urllib.parse
            import mimetypes
            query_str = ""
            if "?" in original_path:
                query_str = original_path.split("?", 1)[1]
            params = urllib.parse.parse_qs(query_str)
            raw_path = params.get("path", [""])[0]
            if not raw_path:
                self.send_error(400, "Missing path parameter")
                return

            raw_path = urllib.parse.unquote(raw_path)
            if raw_path.startswith("file://"):
                raw_path = raw_path[7:]

            file_path = Path(raw_path).expanduser().resolve()
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404, "File not found")
                return

            mime_type, _ = mimetypes.guess_type(str(file_path))
            if not mime_type:
                mime_type = "image/png" if file_path.suffix.lower() == ".png" else "application/octet-stream"

            file_size = file_path.stat().st_size
            self.send_response(200)
            self.send_header('Content-Type', mime_type)
            self.send_header('Content-Length', str(file_size))
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers()

            with open(file_path, "rb") as f:
                import shutil
                shutil.copyfileobj(f, self.wfile)
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

    def handle_post_open_file(self):
        """Opens a local file or reveals it in OS File Manager (Finder / Explorer)."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8')) if content_length else {}
            raw_path = (payload.get("path") or "").strip()
            if not raw_path:
                self._send_json({"error": "Missing path parameter"}, status=400)
                return

            import urllib.parse
            raw_path = urllib.parse.unquote(raw_path)
            if raw_path.startswith("file://"):
                raw_path = raw_path[7:]

            if "?path=" in raw_path:
                raw_path = raw_path.split("?path=", 1)[1]
                raw_path = urllib.parse.unquote(raw_path)

            file_path = Path(raw_path).expanduser().resolve()
            if not file_path.exists():
                self._send_json({"error": f"File non trovato: {file_path}"}, status=404)
                return

            import subprocess, sys
            if sys.platform == 'darwin':
                subprocess.run(['open', '-R', str(file_path)])
            elif sys.platform == 'win32':
                subprocess.run(['explorer', '/select,', str(file_path)])
            else:
                subprocess.run(['xdg-open', str(file_path.parent)])

            self._send_json({"success": True})
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": str(e)}, status=500)


def _get_client_config_paths():
    home = Path.home()
    system = sys.platform

    claude_path = None
    if system == 'darwin':
        claude_path = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == 'win32':
        appdata = os.environ.get("APPDATA")
        if appdata:
            claude_path = Path(appdata) / "Claude" / "claude_desktop_config.json"
    else:
        claude_path = home / ".config" / "Claude" / "claude_desktop_config.json"

    vscode_paths = [
        home / ".vscode" / "mcp.json",
    ]
    if system == 'darwin':
        code_user = home / "Library" / "Application Support" / "Code" / "User"
        vscode_paths.extend([
            code_user / "settings.json",
            code_user / "mcp.json",
            code_user / "globalStorage" / "mcp.json"
        ])
    elif system == 'win32':
        appdata = os.environ.get("APPDATA")
        if appdata:
            code_user = Path(appdata) / "Code" / "User"
            vscode_paths.extend([
                code_user / "settings.json",
                code_user / "mcp.json",
                code_user / "globalStorage" / "mcp.json"
            ])
    else:
        code_user = home / ".config" / "Code" / "User"
        vscode_paths.extend([
            code_user / "settings.json",
            code_user / "mcp.json",
            code_user / "globalStorage" / "mcp.json"
        ])

    continue_path = home / ".continue" / "config.json"

    cursor_paths = [
        home / ".cursor" / "mcp.json"
    ]
    if system == 'darwin':
        cursor_paths.append(home / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "mcp.json")

    return {
        "claude_desktop": claude_path,
        "vscode": vscode_paths,
        "continue": continue_path,
        "cursor": cursor_paths
    }

def _resolve_llampaca_command():
    venv_bin = Path(sys.executable).parent / "llampaca"
    if venv_bin.exists():
        return str(venv_bin)
    import shutil
    which_cmd = shutil.which("llampaca")
    if which_cmd:
        return which_cmd
    return "llampaca"

def parse_hf_input(input_str: str):
    """
    Parses a Hugging Face input string into (repo_id, filename).
    Supports:
    1. Full URL: https://huggingface.co/repo_user/repo_name/resolve/main/filename.gguf
    2. Short identifier: repo_user/repo_name/filename.gguf
    """
    input_str = input_str.strip()
    if not input_str:
        return None, None
        
    # Case 1: Full URL
    if "huggingface.co" in input_str:
        parts = input_str.split("huggingface.co/")[-1].split("/")
        if len(parts) >= 5:
            # Reconstruct repo_id from the first two parts
            repo_id = f"{parts[0]}/{parts[1]}"
            filename = parts[-1]
            # Strip query params from filename if any
            filename = filename.split("?")[0]
            return repo_id, filename
            
    # Case 2: Short identifier
    parts = input_str.split("/")
    if len(parts) >= 3:
        repo_id = f"{parts[0]}/{parts[1]}"
        filename = parts[-1]
        return repo_id, filename
        
    return None, None

def search_hf_models(query: str = None, page: int = 1, limit: int = 8):
    from huggingface_hub import HfApi
    api = HfApi()
    
    # 1. Search GGUF repos
    search_term = query if query else None
    try:
        repos_iter = api.list_models(
            filter="gguf",
            search=search_term,
            sort="downloads"
        )
        import itertools
        start_idx = (page - 1) * limit
        repos = list(itertools.islice(repos_iter, start_idx, start_idx + limit))
    except Exception as e:
        print(f"HF Search error: {e}")
        return []
        
    results = []
    for r in repos:
        repo_id = r.modelId
        downloads = getattr(r, "downloads", 0)
        
        # 2. Get files metadata
        try:
            info = api.model_info(repo_id, files_metadata=True)
            gguf_files = []
            for f in info.siblings:
                if f.rfilename.endswith(".gguf"):
                    size_bytes = getattr(f, "size", None)
                    size_gb = round(size_bytes / (1024**3), 2) if size_bytes else None
                    gguf_files.append({
                        "filename": f.rfilename,
                        "size_bytes": size_bytes,
                        "size_gb": size_gb
                    })
                    
            if gguf_files:
                # Sort files alphabetically/by name
                gguf_files.sort(key=lambda x: x["filename"])
                
                results.append({
                    "repo_id": repo_id,
                    "downloads": downloads,
                    "files": gguf_files,
                    "description": f"Repository con {len(gguf_files)} file GGUF."
                })
        except Exception as e:
            print(f"Error reading model info for {repo_id}: {e}")
            
    return results

def find_free_port(start_port=8090):
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                port += 1

def start_http_server(directory, port):
    config = load_config()
    agent_manager.start(config)
    
    class Handler(QuietSimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

    class ThreadedServer(socketserver.ThreadingTCPServer):
        def server_bind(self):
            import socket
            try:
                self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            super().server_bind()

        def get_request(self):
            import socket
            sock, addr = super().get_request()
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            return sock, addr

        def handle_error(self, request, client_address):
            import sys, socket
            exc_type, exc_val = sys.exc_info()[:2]
            if exc_type and issubclass(exc_type, (ConnectionResetError, BrokenPipeError, socket.error)):
                return
            super().handle_error(request, client_address)

    server = ThreadedServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server

def start_gui_window():
    """Start the background HTTP server and launch the pywebview standalone native window."""
    import sys
    import os
    
    # macOS runtime hack: override Application Menu Name in menu bar
    if sys.platform == 'darwin':
        try:
            from Foundation import NSBundle
            bundle = NSBundle.mainBundle()
            if bundle:
                info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
                if info:
                    info['CFBundleName'] = 'Llampaca'
                    info['CFBundleDisplayName'] = 'Llampaca'
        except Exception:
            pass

    try:
        import webview
    except ImportError:
        print("Error: 'pywebview' is not installed.")
        print("Please install it running: pip install pywebview")
        sys.exit(1)

    gui_dir = Path(__file__).parent.resolve()

    def _set_app_icon():
        """Set OS-specific icon simple and fast without impacting startup time."""
        if sys.platform == 'darwin':
            # macOS: use padded HIG-compliant icon asset (fallback to logo.png)
            mac_icon = gui_dir / "app_icon_mac.png"
            target_path = mac_icon if mac_icon.exists() else (gui_dir / "logo.png")
            if target_path.exists():
                try:
                    from AppKit import NSApplication, NSImage, NSAlert
                    app = NSApplication.sharedApplication()
                    icon_image = NSImage.alloc().initWithContentsOfFile_(str(target_path))
                    if icon_image and icon_image.isValid():
                        app.setApplicationIconImage_(icon_image)

                        # Auto-set icon on all macOS alert / confirmation dialogs (NSAlert)
                        try:
                            import ctypes
                            import objc
                            objc_lib = ctypes.cdll.LoadLibrary('/usr/lib/libobjc.dylib')
                            objc_lib.objc_getClass.restype = ctypes.c_void_p
                            objc_lib.objc_getClass.argtypes = [ctypes.c_char_p]
                            objc_lib.class_getInstanceMethod.restype = ctypes.c_void_p
                            objc_lib.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                            objc_lib.method_getImplementation.restype = ctypes.c_void_p
                            objc_lib.method_getImplementation.argtypes = [ctypes.c_void_p]
                            objc_lib.sel_registerName.restype = ctypes.c_void_p
                            objc_lib.sel_registerName.argtypes = [ctypes.c_char_p]

                            nsalert_cls_ptr = objc_lib.objc_getClass(b'NSAlert')
                            init_sel = objc_lib.sel_registerName(b'init')
                            m_init = objc_lib.class_getInstanceMethod(nsalert_cls_ptr, init_sel)
                            orig_init_imp_ptr = objc_lib.method_getImplementation(m_init)

                            IMP_TYPE = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
                            orig_init_fn = IMP_TYPE(orig_init_imp_ptr)

                            def _swizzled_nsalert_init(self):
                                self_ptr = objc.pyobjc_id(self)
                                res_ptr = orig_init_fn(self_ptr, init_sel)
                                alert = objc.objc_object(c_void_p=res_ptr)
                                if alert:
                                    try:
                                        alert.setIcon_(icon_image)
                                    except Exception:
                                        pass
                                return alert

                            _set_app_icon._imp_holder = _swizzled_nsalert_init
                            NSAlert.init = _swizzled_nsalert_init
                        except Exception:
                            pass
                except Exception:
                    pass
        elif sys.platform == 'win32':
            # Windows: set explicit AppUserModelID for taskbar process icon
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Llampaca.Dashboard.App.1")
            except Exception:
                pass
        elif sys.platform.startswith('linux'):
            # Linux: icon handling relies on window manager / pywebview favicon
            pass

    # Set icon before starting window
    _set_app_icon()

    port = find_free_port()
    server = start_http_server(gui_dir, port)

    print(f"GUI HTTP Server running locally at http://127.0.0.1:{port}", flush=True)
    print("Opening native window...", flush=True)

    try:
        # Per-launch cache-buster on the window URL. pywebview's WebKit
        # backend keeps its own persistent HTTP cache (not fully cleared by
        # wiping ~/Library/WebKit/<app>/WebsiteData), so it can keep loading a
        # stale index.html / JS bundle across launches — making already-fixed
        # frontend bugs reappear. A fresh query string every launch forces the
        # main document (and, via the no-cache headers, its subresources) to
        # be re-fetched. Files are local, so always-fresh has no real cost.
        import time as _time
        cache_buster = int(_time.time())
        webview.create_window(
            "Llampaca Dashboard",
            f"http://127.0.0.1:{port}/index.html?v={cache_buster}",
            width=1150,
            height=780,
            min_size=(950, 680)
        )
        # Setting LLAMPACA_DEBUG=1 enables the WebKit Web Inspector
        # (right-click -> Inspect Element) so the Console/Network tabs can be
        # used to diagnose frontend issues live.
        debug = os.environ.get("LLAMPACA_DEBUG", "").strip() in ("1", "true", "yes")
        webview.start(func=_set_app_icon, debug=debug)
    finally:
        print("Window closed. Stopping HTTP server...", flush=True)
        server.shutdown()
        server.server_close()
        agent_manager.stop()

def start_api_server(port=8090):
    """Start the Llampaca HTTP API server in the foreground (blocking)."""
    gui_dir = Path(__file__).parent.resolve()
    server = start_http_server(gui_dir, port)
    print(f"Llampaca HTTP API Server running locally at http://127.0.0.1:{port}", flush=True)
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("Stopping HTTP server...", flush=True)
        server.shutdown()
        server.server_close()
        agent_manager.stop()
