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
from llampaca.config import load_config

def run_async(coro):
    """Run an async coroutine thread-safely inside the AgentManager's event loop if running."""
    if agent_manager and agent_manager.loop and agent_manager.loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, agent_manager.loop)
        return future.result()
    else:
        return asyncio.run(coro)

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
        resolved_ctx = params.get("context_size") or config.get("context_size", 32768)
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
                
        return success

    def start(self, config):
        self.config = config
        self.thread.start()
        self.ready_event.wait()
        
    async def _init_async(self, config):
        from llampaca.tools import build_default_registry
        from llampaca.engine.client import LlamaClient
        from llampaca.engine.mcp_client import McpClientManager
        from llampaca.config import load_mcp_config
        
        self.client = LlamaClient(port=config.get("server_port", 8080))
        self.registry = build_default_registry(max_result_chars=config.get("context_size", 4096))
        
        mcp_config = load_mcp_config()
        mcp_servers_config = mcp_config.get("mcp_servers", {})
        legacy_mcp = config.get("mcp_servers", {})
        if legacy_mcp:
            mcp_servers_config = {**legacy_mcp, **mcp_servers_config}
            
        if mcp_servers_config:
            self.mcp_manager = McpClientManager(mcp_servers_config)
            await self.mcp_manager.start(self.registry)
            
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
        
        await event.wait()
        
        result = self.pending_confirmations.pop(confirm_id)["result"]
        return result

    def process_message(self, conv_id, content, user_msg_id, conv_data, config, result_queue):
        if self.request_queue:
            asyncio.run_coroutine_threadsafe(
                self.request_queue.put((conv_id, content, user_msg_id, conv_data, config, result_queue)),
                self.loop
            )
        
    async def _process_message_coro(self, conv_id, content, user_msg_id, conv_data, config, result_queue):
        try:
            from llampaca.agent.loop import Agent
            
            last_tool_call = {}
            
            async def _confirm_wrapper(prompt):
                if last_tool_call:
                    return await self.confirm_tool(last_tool_call.get("name"), last_tool_call.get("arguments"), result_queue)
                return False
                
            agent = Agent(
                client=self.client,
                registry=self.registry,
                confirm=_confirm_wrapper,
                model=conv_data.get("model_name", "local-model"),
                context_size=config.get("context_size", 4096),
                summary=conv_data.get("summary")
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
            print(f"\n[Agent Session] Processing message for conversation {conv_id}...", flush=True)
            
            async for kind, data in agent.send(content, message_id=user_msg_id):
                if kind == "tool_call":
                    last_tool_call["name"] = data.get("name")
                    last_tool_call["arguments"] = data.get("arguments")
                result_queue.put(("event", kind, data))
                if kind == "text":
                    response_content += data
                elif kind == "summary_updated":
                    await update_conversation_summary(
                        conv_id,
                        data["summary"],
                        data["last_summarized_message_id"]
                    )
                    
            print("\n[Agent Session] Completed.", flush=True)
            if response_content:
                assistant_msg_id = await add_message(conv_id, "assistant", response_content)
                result_queue.put(("done", {"assistant_message_id": assistant_msg_id}))
                
                # Context budget status
                from llampaca.agent.loop import CHARS_PER_TOKEN
                current_chars = sum(len(m.get("content") or "") for m in agent.messages)
                max_chars = agent.context_size * CHARS_PER_TOKEN
                result_queue.put(("event", "context_status", {"used_chars": current_chars, "max_chars": max_chars}))
                
                # Auto-titling for new conversations
                if len(conv_data.get("messages", [])) <= 1:
                    try:
                        title_prompt = f"Scrivi un titolo molto breve (massimo 4-5 parole) che riassuma questo messaggio. Restituisci SOLO il titolo senza virgolette o preamboli: {content}"
                        title_res = ""
                        async for chunk in self.client.chat_stream([{"role": "user", "content": title_prompt}]):
                            title_res += chunk
                        new_title = title_res.strip().strip('"').strip("'")
                        if new_title:
                            await update_conversation_title(conv_id, new_title)
                            result_queue.put(("event", "title_updated", {"title": new_title}))
                    except Exception as e:
                        print(f"Error auto-titling: {e}")
            else:
                result_queue.put(("done", {}))
                
        except Exception as e:
            traceback.print_exc()
            result_queue.put(("error", str(e)))
        finally:
            result_queue.put(("close", None))

agent_manager = AgentManager()

class QuietSimpleHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Log only API requests to the console, ignore static asset prints
        if self.path.startswith('/api/'):
            sys.stdout.write(f"[{self.log_date_time_string()}] API REQUEST: {self.command} {self.path} -> Response Code: {args[1]}\n")
            sys.stdout.flush()

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
        else:
            super().do_GET()

    def do_POST(self):
        self.path = self.path.split('?')[0]
        if self.path.startswith('/api/confirm'):
            self.handle_post_confirm()
        elif self.path.startswith('/api/conversations'):
            self.handle_post_conversations()
        elif self.path.startswith('/api/settings'):
            self.handle_post_settings()
        elif self.path.startswith('/api/models/default'):
            self.handle_post_models_default()
        elif self.path.startswith('/api/models/download'):
            self.handle_post_models_download()
        else:
            self.send_error(404, "Not Found")

    def do_DELETE(self):
        self.path = self.path.split('?')[0]
        if self.path.startswith('/api/conversations'):
            self.handle_delete_conversation()
        elif self.path.startswith('/api/models/'):
            self.handle_delete_model()
        else:
            self.send_error(404, "Not Found")

    def handle_post_confirm(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            confirm_id = payload.get("confirm_id")
            allow = payload.get("allow", False)
            
            def resolve():
                if confirm_id in agent_manager.pending_confirmations:
                    agent_manager.pending_confirmations[confirm_id]["result"] = allow
                    agent_manager.pending_confirmations[confirm_id]["event"].set()
                    
            agent_manager.loop.call_soon_threadsafe(resolve)
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
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
            self.send_header('Connection', 'keep-alive')
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

    def emit_sse(self, kind, data):
        """Helper to send event stream chunks to the frontend."""
        try:
            event_data = json.dumps({"kind": kind, "data": data})
            payload = f"data: {event_data}\n\n".encode('utf-8')
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
                
            from llampaca.config import save_config
            save_config(config)
            
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
                
                is_installed = filename in installed_filenames
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
                    "repo_id": repo_id
                })
                
            # 2. Add other installed GGUF files (not in presets)
            preset_filenames = {preset["file"] for preset in MODEL_PRESETS.values()}
            for filename in installed_filenames:
                if filename in preset_filenames:
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
                        
                is_active = (default_model == filename)
                
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
                    "repo_id": None
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
                        "repo_id": dl["repo_id"]
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
            
            results = search_hf_models(query=search_query)
            
            body = json.dumps(results).encode('utf-8')
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
            
            if not model_name:
                raise Exception("model_name non specificato.")
                
            config = load_config()
            config["default_model"] = model_name
            from llampaca.config import save_config
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
            
            from llampaca.config import MODEL_PRESETS
            preset_file = None
            if default_model in MODEL_PRESETS:
                preset_file = MODEL_PRESETS[default_model]["file"]
                
            if model_filename == default_model or model_filename == preset_file:
                raise Exception("Non è possibile eliminare il modello attualmente attivo/predefinito.")
                
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

def search_hf_models(query: str = None, limit: int = 8):
    from huggingface_hub import HfApi
    api = HfApi()
    
    # 1. Search GGUF repos
    search_term = query if query else None
    try:
        repos = api.list_models(
            filter="gguf",
            search=search_term,
            sort="downloads",
            limit=limit
        )
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

    server = socketserver.ThreadingTCPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server

def start_gui_window():
    """Start the background HTTP server and launch the pywebview standalone native window."""
    import sys
    
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
    port = find_free_port()
    server = start_http_server(gui_dir, port)

    print(f"GUI HTTP Server running locally at http://127.0.0.1:{port}", flush=True)
    print("Opening native window...", flush=True)

    try:
        webview.create_window(
            "Llampaca Dashboard",
            f"http://127.0.0.1:{port}/index.html",
            width=1150,
            height=780,
            min_size=(950, 680)
        )
        webview.start()
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
