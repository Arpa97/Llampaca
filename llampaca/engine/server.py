import os
import sys
import time
import socket
import subprocess
from pathlib import Path
import requests
from llampaca.config import BIN_DIR, LOGS_DIR, load_config

def is_port_in_use(port: int) -> bool:
    """Check if a port is already open on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # connect_ex returns 0 if connection was successful
        return s.connect_ex(('127.0.0.1', port)) == 0

def is_pid_running(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    if sys.platform == "win32":
        try:
            # Query tasklist for the PID
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}"],
                stderr=subprocess.DEVNULL,
                text=True
            )
            return str(pid) in out
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

def kill_pid(pid: int):
    """Terminate the process with the given PID."""
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    else:
        try:
            import signal
            os.kill(pid, signal.SIGTERM)
            # Wait a moment for graceful shutdown
            for _ in range(10):
                if not is_pid_running(pid):
                    return
                time.sleep(0.1)
            # Force kill if still running
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

def cleanup_orphans():
    """
    Scan for stale PID files in the logs directory and kill any orphaned
    llama-server processes left over from crashed or unclean previous sessions.
    Called automatically at the start of LlamaServer.start().
    """
    from llampaca.config import LOGS_DIR as _LOGS_DIR
    pid_files = list(_LOGS_DIR.glob("llama-server-*.pid"))
    if not pid_files:
        return

    for pid_file in pid_files:
        try:
            pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            # Corrupted or unreadable PID file — remove it
            try:
                pid_file.unlink()
            except OSError:
                pass
            continue

        if is_pid_running(pid):
            print(f"Found orphaned llama-server process (PID {pid}), killing it...")
            kill_pid(pid)

        # Remove the stale PID file regardless
        try:
            pid_file.unlink()
        except OSError:
            pass

_active_server = None

def get_active_server():
    global _active_server
    return _active_server

def set_active_server(server):
    global _active_server
    _active_server = server

class LlamaServer:
    def __init__(self, model_path: Path, port: int = None, context_size: int = None, n_threads: int = None, gpu_layers: int = None):
        config = load_config()
        
        self.model_path = Path(model_path)
        self.port = port or config.get("server_port", 8080)
        self.context_size = context_size or config.get("context_size", 32768)
        self.n_threads = n_threads or config.get("n_threads", 4)
        self.gpu_layers = gpu_layers if gpu_layers is not None else config.get("gpu_layers", -1)
        
        # Determine the binary path
        custom_binary = config.get("llama_server_path", "")
        if custom_binary:
            self.binary_path = Path(custom_binary)
        else:
            binary_name = "llama-server"
            if sys.platform == "win32":
                binary_name += ".exe"
            self.binary_path = BIN_DIR / binary_name
            
        self.process = None
        self.log_file_path = LOGS_DIR / f"llama-server-{self.port}.log"
        self.pid_file_path = LOGS_DIR / f"llama-server-{self.port}.pid"
        from llampaca.engine import db
        self.db = db

    def is_binary_available(self) -> bool:
        """Check if the llama-server binary exists and is executable."""
        return self.binary_path.exists() and (sys.platform == "win32" or os.access(self.binary_path, os.X_OK))

    async def start(self, timeout_seconds: int = 60) -> bool:
        """
        Start the llama-server subprocess.
        Returns True if the server started successfully and is healthy, False otherwise.
        """
        if not self.is_binary_available():
            print(f"Error: llama-server binary not found at {self.binary_path}.")
            print("Please run 'llampaca init' to download it first.")
            return False

        if not self.model_path.exists():
            print(f"Error: Model file not found at {self.model_path}.")
            return False

        # Kill any orphaned llama-server processes from previous sessions
        cleanup_orphans()

        # Check if the port is in use, and automatically find the next available port
        original_port = self.port
        attempts = 0
        max_attempts = 100
        while is_port_in_use(self.port) and attempts < max_attempts:
            self.port += 1
            attempts += 1
            
        if attempts >= max_attempts:
            print(f"Error: Could not find an available port in the range {original_port} to {original_port + max_attempts - 1}.")
            return False
            
        if self.port != original_port:
            print(f"Port {original_port} is already in use. Automatically switched to port {self.port}.")
            # Update log and PID file paths with the resolved port
            self.log_file_path = LOGS_DIR / f"llama-server-{self.port}.log"
            self.pid_file_path = LOGS_DIR / f"llama-server-{self.port}.pid"

        # Build command arguments
        # --jinja enables the model's jinja chat template, which is required
        # for OpenAI-compatible tool calling (the agent loop depends on it).
        # It is the default on recent llama.cpp builds but we pass it
        # explicitly to support older binaries.
        cmd = [
            str(self.binary_path),
            "-m", str(self.model_path),
            "--port", str(self.port),
            "-c", str(self.context_size),
            "-t", str(self.n_threads),
            "--jinja",
            # Reuse KV-cache chunks (of at least 256 tokens) via context
            # shifting when a new prompt only partially matches the cached
            # prefix. This matters because the agent trims old history when
            # the context fills up: a trim changes the prompt prefix, and
            # without cache reuse each trim would force recomputing the whole
            # prompt — the slowest phase on local hardware.
            "--cache-reuse", "256",
            # Enable Flash Attention to speed up self-attention computation
            # (especially for large prompts/contexts) and reduce memory footprint.
            "-fa", "on",
            # Quantize Key-Value cache to 8-bit (q8_0) to halve its VRAM/RAM footprint,
            # preventing memory paging/swapping and keeping generation speeds fast
            # as the context window fills.
            "-ctk", "q8_0",
            "-ctv", "q8_0"
        ]
        
        # Configure GPU layers
        # For llama.cpp, if gpu_layers is -1 (auto), we default to offloading all layers (e.g. 99) 
        # to utilize Apple Silicon Metal or CUDA if available.
        ngl = 99 if self.gpu_layers == -1 else self.gpu_layers
        if ngl > 0:
            cmd.extend(["-ngl", str(ngl)])

        print(f"Starting llama-server on port {self.port}...")
        print(f"Command: {' '.join(cmd)}")
        print(f"Logging outputs to: {self.log_file_path}")
        
        await self.db.init_db()
        print(f"Database path: {self.db.db_path}")
        print(f"Database OK!")

        # Ensure logs directory exists
        self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Open log file
        log_file = open(self.log_file_path, "w", encoding="utf-8")
        
        # Launch subprocess
        # On Windows, start it without showing a new console window
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
        self.process = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            startupinfo=startupinfo,
            text=True
        )

        # Wait for the server to become healthy
        start_time = time.time()
        health_url = f"http://127.0.0.1:{self.port}/health"
        
        print("Waiting for server to initialize (this can take a few seconds)...")
        while time.time() - start_time < timeout_seconds:
            # Check if subprocess died early
            if self.process.poll() is not None:
                print(f"Error: llama-server process terminated immediately with exit code {self.process.returncode}.")
                print(f"Check the log file at {self.log_file_path} for errors.")
                log_file.close()
                return False
                
            try:
                response = requests.get(health_url, timeout=1)
                if response.status_code == 200:
                    data = response.json()
                    # llama-server health check returns {"status": "ok"} or similar
                    if data.get("status") in ["ok", "healthy"] or "status" in data:
                        print(f"llama-server is up and running on port {self.port}!")
                        log_file.close()
                        # Write PID to file so future sessions can clean up if we crash
                        try:
                            self.pid_file_path.write_text(str(self.process.pid))
                        except OSError:
                            pass
                        global _active_server
                        _active_server = self
                        return True
            except requests.RequestException:
                pass
                
            time.sleep(0.5)

        print(f"Error: llama-server failed to initialize within {timeout_seconds} seconds.")
        print(f"Check the log file at {self.log_file_path} for details.")
        self.stop()
        log_file.close()
        return False

    def stop(self):
        """Stop the llama-server subprocess."""
        if not self.process:
            return

        print("Shutting down llama-server...")
        try:
            # Try soft termination
            if sys.platform == "win32":
                self.process.terminate()
            else:
                # Send SIGINT (like Ctrl+C) to let it save states if needed
                import signal
                self.process.send_signal(signal.SIGINT)
                
            # Wait for process to exit
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if still running
                print("Force killing process...")
                self.process.kill()
                self.process.wait()
        except ProcessLookupError:
            pass
        except Exception as e:
            print(f"Error terminating server: {e}")
        finally:
            self.process = None
            # Remove PID file on clean shutdown
            try:
                if self.pid_file_path.exists():
                    self.pid_file_path.unlink()
            except OSError:
                pass

    def __del__(self):
        # Destructor to ensure process is stopped if object is garbage collected
        if hasattr(self, "process") and self.process:
            self.stop()


def restart_active_server(model_name: str = None, port: int = None, context_size: int = None, n_threads: int = None, gpu_layers: int = None) -> bool:
    """
    Restart the llama-server. If the GUI event loop is running, we delegate
    the restart to the main AgentManager loop task to avoid AnyIO cancel scope conflicts.
    Otherwise, we fall back to a direct local restart.
    """
    try:
        from llampaca.gui.server import agent_manager
    except ImportError:
        agent_manager = None

    if agent_manager and agent_manager.loop and agent_manager.loop.is_running():
        import concurrent.futures
        fut = concurrent.futures.Future()
        params = {
            "model_name": model_name,
            "port": port,
            "context_size": context_size,
            "n_threads": n_threads,
            "gpu_layers": gpu_layers
        }
        def put():
            agent_manager.request_queue.put_nowait(("restart_server", params, fut))
        agent_manager.loop.call_soon_threadsafe(put)
        return fut.result(timeout=65)

    import asyncio
    return asyncio.run(_restart_active_server_fallback(model_name, port, context_size, n_threads, gpu_layers))


async def _restart_active_server_fallback(model_name: str = None, port: int = None, context_size: int = None, n_threads: int = None, gpu_layers: int = None) -> bool:
    global _active_server
    import traceback
    
    config = load_config()
    
    # 1. Resolve model name/path
    if not model_name:
        model_name = config.get("default_model", "")
        
    from llampaca.config import MODELS_DIR, MODEL_PRESETS
    model_path = MODELS_DIR / model_name
    if not model_path.exists():
        model_path = Path(model_name)
        if not model_path.exists():
            if model_name in MODEL_PRESETS:
                preset_file = MODEL_PRESETS[model_name]["file"]
                model_path = MODELS_DIR / preset_file
                
    if not model_path.exists():
        print(f"[Server Restart] Error: Model '{model_name}' could not be resolved.")
        return False

    # 2. Stop current server if running
    if _active_server:
        print(f"[Server Restart] Stopping active server on port {_active_server.port}...")
        _active_server.stop()
        # Give a small pause to release port
        time.sleep(0.5)
        _active_server = None

    # 3. Resolve parameters
    resolved_port = port or config.get("server_port", 8080)
    resolved_ctx = context_size or config.get("context_size", 32768)
    resolved_threads = n_threads or config.get("n_threads", 4)
    resolved_gpu = gpu_layers if gpu_layers is not None else config.get("gpu_layers", -1)

    # 4. Instantiate new server
    new_server = LlamaServer(
        model_path=model_path,
        port=resolved_port,
        context_size=resolved_ctx,
        n_threads=resolved_threads,
        gpu_layers=resolved_gpu
    )

    # 5. Start new server
    success = await new_server.start()
    if success:
        _active_server = new_server
        # Update LlamaClient port dynamically in GUI agent manager if running
        try:
            from llampaca.gui.server import agent_manager
            if agent_manager:
                from llampaca.engine.client import LlamaClient
                agent_manager.client = LlamaClient(port=new_server.port)
                
                # Update registry context size limit
                from llampaca.tools import build_default_registry
                agent_manager.registry = build_default_registry(max_result_chars=new_server.context_size)
                
                # Re-initialize MCP manager with the new registry
                from llampaca.config import load_mcp_config
                mcp_config = load_mcp_config()
                mcp_servers_config = mcp_config.get("mcp_servers", {})
                if mcp_servers_config:
                    from llampaca.engine.mcp_client import McpClientManager
                    agent_manager.mcp_manager = McpClientManager(mcp_servers_config)
                    await agent_manager.mcp_manager.start(agent_manager.registry)
        except Exception as e:
            print(f"[Server Restart] Error updating agent_manager: {e}")
            traceback.print_exc()

    return success
