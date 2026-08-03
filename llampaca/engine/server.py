import os
import sys
import time
import socket
import subprocess
from pathlib import Path
import requests
from llampaca.config import (
    BIN_DIR,
    LOGS_DIR,
    load_config,
    DEFAULT_CONTEXT_SIZE,
    EMBEDDING_CONTEXT_SIZE,
)
from llampaca.engine import state
import logging
logger = logging.getLogger(__name__)

def check_gpu_vram_gb() -> float:
    """
    Check available GPU VRAM in GB.
    On macOS (Darwin): returns infinity (Apple Silicon Metal Unified Memory, no VRAM check required).
    On Linux / Windows: queries nvidia-smi, rocm-smi, or wmic to find max dedicated VRAM in GB.
    Returns 0.0 if no discrete GPU or VRAM < detection threshold is found.
    """
    if sys.platform == "darwin":
        return float("inf")

    # 1. Try nvidia-smi (NVIDIA CUDA GPUs on Linux / Windows)
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            text=True
        )
        totals_mib = [float(line.strip()) for line in out.strip().splitlines() if line.strip() and line.strip().replace('.', '', 1).isdigit()]
        if totals_mib:
            return max(totals_mib) / 1024.0
    except Exception:
        pass

    # 2. Try rocm-smi (AMD ROCm GPUs on Linux)
    if sys.platform.startswith("linux"):
        try:
            out = subprocess.check_output(
                ["rocm-smi", "--showmeminfo", "vram"],
                stderr=subprocess.DEVNULL,
                text=True
            )
            import re
            matches = re.findall(r"VRAM Total Memory \(B\):\s*(\d+)", out)
            if matches:
                return max(int(m) for m in matches) / (1024.0 ** 3)
        except Exception:
            pass

    # 3. Try Windows WMIC for Windows GPUs
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["wmic", "path", "win32_VideoController", "get", "AdapterRAM"],
                stderr=subprocess.DEVNULL,
                text=True
            )
            rams = [int(line.strip()) for line in out.strip().splitlines() if line.strip().isdigit() and int(line.strip()) > 0]
            if rams:
                return max(rams) / (1024.0 ** 3)
        except Exception:
            pass

    return 0.0


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

def is_llama_server_pid(pid: int) -> bool:
    """Check if a running process with the given PID is actually a llama-server or sd-cli instance."""
    if not is_pid_running(pid):
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine"],
                stderr=subprocess.DEVNULL,
                text=True
            )
            return "llama-server" in out.lower() or "sd-cli" in out.lower()
        except Exception:
            return False
    else:
        try:
            out = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "command="],
                stderr=subprocess.DEVNULL,
                text=True
            )
            return "llama-server" in out or "sd-cli" in out
        except Exception:
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

        if is_llama_server_pid(pid):
            logger.info(f"Found orphaned llama-server process (PID {pid}), killing it...")
            kill_pid(pid)

        # Remove the stale PID file regardless
        try:
            pid_file.unlink()
        except OSError:
            pass

class LlamaServer:
    def __init__(self, model_path: Path, port: int = None, context_size: int = None,
                 n_threads: int = None, gpu_layers: int = None,
                 embedding: bool = False, pooling: str = "last",
                 no_think: bool = False,
                 draft_model: str = None, draft_model_gpu_layers: int = None,
                 ssd_offload: bool = False,
                 speculative_mode: str = "none"):
        """
        Args:
            embedding: Run llama-server as an *embedding* server instead of a
                chat server (--embedding). Used by the RAG pipeline as a
                second, lazily-started instance alongside the chat server.
            pooling: Pooling mode for embedding mode (llama-server's
                --pooling). Model-specific — e.g. "last" for Qwen3-Embedding,
                "mean" for bge-style models. Wrong pooling silently produces
                meaningless vectors, so the value comes from the model preset.
                Ignored in chat mode.
            no_think: Disable the model's "thinking" phase (llama-server's
                --reasoning-budget 0). Reasoning models like Qwen3/3.5 emit a
                hidden <think> block before every answer and every tool call
                — often hundreds of tokens the user never sees, perceived as
                dead time before the response starts. With the budget at 0
                the model skips straight to the answer: much lower latency
                per turn, at some quality cost on complex tasks. Ignored in
                embedding mode (embedders don't generate).
        """
        config = load_config()

        self.model_path = Path(model_path)
        self.embedding = embedding
        self.pooling = pooling
        self.no_think = no_think
        self.ssd_offload = ssd_offload
        if embedding:
            # Dedicated defaults for embedding mode: its own port range (so
            # it never races the chat server's auto-increment scan) and a
            # context sized for a single chunk (target ~500 tokens, but
            # the real tokenizer can run over the chars/4 estimate — 4096
            # leaves comfortable headroom) served to ONE parallel slot
            # (see --parallel in _build_command) so the chunk never has to
            # share it.
            self.port = port or config.get("embedding_port", 8180)
            self.context_size = context_size or EMBEDDING_CONTEXT_SIZE
        else:
            self.port = port or config.get("server_port", 8080)
            self.context_size = context_size or config.get("context_size", DEFAULT_CONTEXT_SIZE)
        self.n_threads = n_threads or config.get("n_threads", 4)
        self.gpu_layers = gpu_layers if gpu_layers is not None else config.get("gpu_layers", -1)
        
        self.draft_model = draft_model if draft_model is not None else config.get("draft_model", "")
        self.draft_model_gpu_layers = draft_model_gpu_layers if draft_model_gpu_layers is not None else config.get("draft_model_gpu_layers", -1)
        
        self.speculative_mode = speculative_mode if speculative_mode is not None else config.get("speculative_mode", "none")

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
        # Distinct file names per role, both still matching the
        # "llama-server-*" glob that cleanup_orphans() scans.
        prefix = "llama-server-embed" if embedding else "llama-server"
        self._file_prefix = prefix
        
        # Check for mmproj metadata
        self.mmproj_path = None
        metadata_path = Path(self.model_path).with_suffix(Path(self.model_path).suffix + ".json")
        if metadata_path.exists():
            import json
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                    mmproj_filename = meta.get("mmproj")
                    if mmproj_filename:
                        from llampaca.config import MODELS_DIR
                        mmproj_full = MODELS_DIR / mmproj_filename
                        if mmproj_full.exists():
                            self.mmproj_path = mmproj_full
            except Exception as e:
                logger.error(f"Failed to read model metadata from {metadata_path}: {e}")
        self.log_file_path = LOGS_DIR / f"{prefix}-{self.port}.log"
        self.pid_file_path = LOGS_DIR / f"{prefix}-{self.port}.pid"
        from llampaca.engine import db
        self.db = db

    def _build_command(self) -> list:
        """
        Assemble the llama-server command line for this instance's role.

        Chat mode: jinja chat template (tool calling), prompt-cache reuse,
        flash attention and q8_0 KV-cache quantization — all about long
        generative conversations.

        Embedding mode: --embedding with the model's pooling type. None of
        the chat flags apply (there is no KV-cache reuse or generation), so
        the command stays minimal on purpose: fewer flags, fewer ways an
        older binary can refuse to start.
        """
        cmd = [
            str(self.binary_path),
            "-m", str(self.model_path),
        ]
        
        config = load_config()
        if not self.embedding:
            if self.speculative_mode == "draft_model" and self.draft_model:
                from llampaca.config import MODELS_DIR
                draft_path = MODELS_DIR / self.draft_model
                if not draft_path.exists():
                    draft_path = Path(self.draft_model)
                if draft_path.exists():
                    cmd.extend(["-md", str(draft_path)])
                    draft_ngl = self.draft_model_gpu_layers
                    if draft_ngl == -1:
                        draft_ngl = 99
                    if draft_ngl > 0:
                        cmd.extend(["-ngld", str(draft_ngl)])
            elif self.speculative_mode == "mtp":
                cmd.extend(["--spec-type", "draft-mtp"])
            elif self.speculative_mode == "ngram":
                cmd.extend(["--spec-type", "ngram-simple"])

        cmd.extend([
            "--port", str(self.port),
            "-c", str(self.context_size),
            "-t", str(self.n_threads),
        ])

        if self.embedding:
            cmd.extend([
                "--embedding",
                "--pooling", self.pooling,
                # One chunk must fit in a single physical batch or the
                # server rejects the request: keep ubatch == context.
                "--ubatch-size", str(self.context_size),
                # llama-server's default --parallel is -1 (auto), which
                # picked 4 slots in practice, splitting -c between them
                # (n_ctx_slot = context_size / n_slots = 2048/4 = 512).
                # RAG chunks target ~500 tokens but the real tokenizer can
                # run over that (observed up to 630 for a 500-token-
                # estimate chunk), so a 512-token slot was too small: the
                # server logged "failed to find a memory slot" and then
                # hard-crashed on an internal assert. A single slot gives
                # each request the FULL context; the indexing pipeline
                # embeds one batch at a time anyway; no concurrent
                # embedding requests are expected in one session.
                "--parallel", "1",
            ])
        else:
            cmd.extend([
                # --jinja enables the model's jinja chat template, which is
                # required for OpenAI-compatible tool calling (the agent
                # loop depends on it). It is the default on recent
                # llama.cpp builds but we pass it explicitly to support
                # older binaries.
                "--jinja",
                # Reuse KV-cache chunks (of at least 256 tokens) via context
                # shifting when a new prompt only partially matches the
                # cached prefix. This matters because the agent trims old
                # history when the context fills up: a trim changes the
                # prompt prefix, and without cache reuse each trim would
                # force recomputing the whole prompt — the slowest phase on
                # local hardware.
                "--cache-reuse", "256",
                # Enable Flash Attention to speed up self-attention
                # computation (especially for large prompts/contexts) and
                # reduce memory footprint.
                "-fa", "on",
                # Quantize Key-Value cache to 8-bit (q8_0) to halve its
                # VRAM/RAM footprint, preventing memory paging/swapping and
                # keeping generation speeds fast as the context fills.
                "-ctk", load_config().get("kv_cache_quant_k", "q8_0"),
                "-ctv", load_config().get("kv_cache_quant_v", "q8_0"),
            ])
            if self.no_think:
                # Render the chat template with thinking disabled (e.g.
                # Qwen3's enable_thinking=false), so reasoning models skip
                # their <think> phase and answer directly. Harmless on
                # models that don't reason. CAVEAT: only effective when the
                # GGUF's embedded chat template actually implements the
                # toggle — official Qwen GGUFs do, some third-party
                # conversions ship a stripped template without it and keep
                # thinking anyway. Requires a llama.cpp build recent enough
                # to know the flag; older binaries will refuse to start and
                # point the user at the log.
                cmd.extend(["--reasoning", "off"])

        # Configure GPU layers
        # For llama.cpp, if gpu_layers is -1 (auto), we default to offloading
        # all layers (e.g. 99) to utilize Apple Silicon Metal or CUDA if
        # available. On Linux/Windows, we verify that at least 8GB of dedicated
        # GPU VRAM is present; if not, we fallback to CPU + RAM (gpu_layers = 0).
        if self.gpu_layers == -1:
            if self.ssd_offload:
                ngl = 0
            elif sys.platform != "darwin":
                vram_gb = check_gpu_vram_gb()
                if vram_gb < 8.0:
                    logger.info(f"[LlamaServer] Linux/Windows system without at least 8GB dedicated GPU VRAM (detected {vram_gb:.1f} GB). Running on CPU+RAM (gpu_layers=0).")
                    ngl = 0
                else:
                    ngl = 99
            else:
                ngl = 99
        else:
            ngl = self.gpu_layers
            
        if ngl > 0:
            cmd.extend(["-ngl", str(ngl)])
            
        if self.ssd_offload:
            # Explicitly request memory mapping. llama.cpp does this by default,
            # but we pass it anyway to ensure it's on.
            cmd.extend(["--mmap"])

        if self.mmproj_path:
            cmd.extend(["--mmproj", str(self.mmproj_path)])

        return cmd

    def is_binary_available(self) -> bool:
        """Check if the llama-server binary exists and is executable."""
        return self.binary_path.exists() and (sys.platform == "win32" or os.access(self.binary_path, os.X_OK))

    async def start(self, timeout_seconds: int = 60) -> bool:
        """
        Start the llama-server subprocess.
        Returns True if the server started successfully and is healthy, False otherwise.
        """
        if not self.is_binary_available():
            logger.error(f"Error: llama-server binary not found at {self.binary_path}.")
            logger.info("Please run 'llampaca init' to download it first.")
            return False

        if not self.model_path.exists():
            logger.error(f"Error: Model file not found at {self.model_path}.")
            return False

        # Kill any orphaned llama-server processes from previous sessions.
        # Only the CHAT server does this, because it is the first to start
        # in a session: by the time an embedding server starts lazily, the
        # chat server is already running — cleanup here would kill it.
        if not self.embedding:
            cleanup_orphans()

        # Check if the port is in use, and automatically find the next available port
        original_port = self.port
        attempts = 0
        max_attempts = 100
        while is_port_in_use(self.port) and attempts < max_attempts:
            self.port += 1
            attempts += 1
            
        if attempts >= max_attempts:
            logger.error(f"Error: Could not find an available port in the range {original_port} to {original_port + max_attempts - 1}.")
            return False
            
        if self.port != original_port:
            logger.info(f"Port {original_port} is already in use. Automatically switched to port {self.port}.")
            # Update log and PID file paths with the resolved port
            self.log_file_path = LOGS_DIR / f"{self._file_prefix}-{self.port}.log"
            self.pid_file_path = LOGS_DIR / f"{self._file_prefix}-{self.port}.pid"

        # Build the role-specific command line (chat vs embedding)
        cmd = self._build_command()

        logger.info(f"Starting llama-server on port {self.port}...")
        logger.info(f"Command: {' '.join(cmd)}")
        logger.info(f"Logging outputs to: {self.log_file_path}")
        
        await self.db.init_db()
        logger.info(f"Database path: {self.db.db_path}")
        logger.info(f"Database OK!")

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
        
        logger.info("Waiting for server to initialize (this can take a few seconds)...")
        while time.time() - start_time < timeout_seconds:
            # Check if subprocess died early
            if self.process.poll() is not None:
                logger.error(f"Error: llama-server process terminated immediately with exit code {self.process.returncode}.")
                logger.info(f"Check the log file at {self.log_file_path} for errors.")
                log_file.close()
                return False
                
            try:
                response = requests.get(health_url, timeout=1)
                if response.status_code == 200:
                    data = response.json()
                    # llama-server health check returns {"status": "ok"} or similar
                    if data.get("status") in ["ok", "healthy"] or "status" in data:
                        logger.info(f"llama-server is up and running on port {self.port}!")
                        log_file.close()
                        # Write PID to file so future sessions can clean up if we crash
                        try:
                            self.pid_file_path.write_text(str(self.process.pid))
                        except OSError:
                            pass
                        state.set_chat_server(self)
                        return True
            except requests.RequestException:
                pass
                
            time.sleep(0.5)

        logger.error(f"Error: llama-server failed to initialize within {timeout_seconds} seconds.")
        logger.info(f"Check the log file at {self.log_file_path} for details.")
        self.stop()
        log_file.close()
        return False

    def stop(self):
        """Stop the llama-server subprocess."""
        if not self.process:
            return

        logger.info("Shutting down llama-server...")
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
                logger.info("Force killing process...")
                self.process.kill()
                self.process.wait()
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.error(f"Error terminating server: {e}")
        finally:
            self.process = None
            # Remove PID file on clean shutdown
            try:
                if self.pid_file_path.exists():
                    self.pid_file_path.unlink()
            except OSError:
                pass
            
            if state.get_chat_server() is self:
                state.set_chat_server(None)




def restart_active_server(model_name: str = None, port: int = None, context_size: int = None, n_threads: int = None, gpu_layers: int = None) -> bool:
    """
    Restart the llama-server.
    """
    import asyncio
    return asyncio.run(_restart_active_server_fallback(model_name, port, context_size, n_threads, gpu_layers))


async def _restart_active_server_fallback(model_name: str = None, port: int = None, context_size: int = None, n_threads: int = None, gpu_layers: int = None) -> bool:
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
        logger.error(f"[Server Restart] Error: Model '{model_name}' could not be resolved.")
        return False

    # 2. Stop current server if running
    active_server = state.get_chat_server()
    if active_server:
        logger.info(f"[Server Restart] Stopping active server on port {active_server.port}...")
        active_server.stop()
        # Give a small pause to release port
        time.sleep(0.5)
        state.set_chat_server(None)

    # 3. Resolve parameters
    resolved_port = port or config.get("server_port", 8080)
    resolved_ctx = context_size or config.get("context_size", DEFAULT_CONTEXT_SIZE)
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
        state.set_chat_server(new_server)

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
            logger.error(f"[Server Restart] Error updating agent_manager: {e}")
            traceback.print_exc()

    return success
