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
    """Run an async coroutine synchronously inside the request thread."""
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
        if self.path.startswith('/api/conversations'):
            self.handle_get_conversations()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/confirm'):
            self.handle_post_confirm()
        elif self.path.startswith('/api/conversations'):
            self.handle_post_conversations()
        else:
            self.send_error(404, "Not Found")

    def do_DELETE(self):
        if self.path.startswith('/api/conversations'):
            self.handle_delete_conversation()
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
