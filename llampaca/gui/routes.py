
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Union
import json, uuid, os, shutil, re, time, urllib.parse, io, base64, queue
from pathlib import Path
import logging

from llampaca.engine.db import (
    list_conversations, get_conversation, create_conversation,
    add_message, delete_conversation, update_conversation_title,
    update_conversation_summary, update_conversation_workspace
)
from llampaca.config import load_config, save_config, MODELS_DIR, MODEL_PRESETS, DEFAULT_CONTEXT_SIZE, resolve_workspace_dir
from llampaca.gui.agent_manager import agent_manager, run_async, is_server_running
from llampaca.gui.restart_bridge import restart_via_agent_manager
from llampaca.security import confine_path

logger = logging.getLogger(__name__)

app = FastAPI(title="Llampaca GUI API")

# Hostnames the API answers to. The server only ever binds to 127.0.0.1, but
# binding is not the same as validating: a request carrying any Host header
# still reached every route, which is precisely what a DNS rebinding attack
# needs. An attacker page on evil.example whose domain briefly resolves to
# 127.0.0.1 becomes same-origin with the API — same-origin, so CORS stops
# applying and preflights are no longer involved — and can then drive every
# endpoint, including the custom-tool endpoint that writes and imports Python.
# Pinning the accepted Host values removes that: the browser sends the
# attacker's domain in Host, and the request is refused.
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def _hostname_of(value: str) -> str:
    """Strip the port (and scheme, for Origin) from a Host/Origin header."""
    value = value.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    # IPv6 literals are bracketed: keep the brackets, drop only a real port.
    if value.startswith("["):
        closing = value.find("]")
        if closing != -1:
            return value[: closing + 1].lower()
    return value.rsplit(":", 1)[0].lower() if ":" in value else value.lower()


@app.middleware("http")
async def restrict_to_local_origin(request: Request, call_next):
    """
    Reject requests that are not genuinely local:

    - **Host** must name the loopback interface. Blocks DNS rebinding.
    - **Origin**, when present, must also be loopback. Blocks cross-site
      requests from an ordinary web page: the browser attaches Origin to every
      cross-origin request, including the "simple" POSTs that escape a
      preflight (``/api/conversations/{id}/messages`` parses the body with
      ``request.json()``, so a text/plain POST used to reach the agent).
      ``Origin: null`` — a file:// page or a sandboxed iframe — is refused too.

    Same-origin requests from the desktop window either omit Origin (GET,
    images, SSE) or send the loopback origin, so the UI is unaffected.
    """
    host_header = request.headers.get("host", "")
    if host_header and _hostname_of(host_header) not in _ALLOWED_HOSTS:
        return JSONResponse(status_code=403, content={"detail": "Forbidden host."})

    origin = request.headers.get("origin")
    if origin and (origin == "null" or _hostname_of(origin) not in _ALLOWED_HOSTS):
        return JSONResponse(status_code=403, content={"detail": "Forbidden origin."})

    return await call_next(request)

# --- CONVERSATIONS ---
@app.get("/api/conversations")
async def get_conversations():
    convs = await list_conversations()
    return convs

@app.get("/api/conversations/{conv_id}")
async def get_conversation_by_id(conv_id: str):
    conv = await get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv

class CreateConvPayload(BaseModel):
    title: Optional[str] = None
    system_prompt: Optional[str] = None
    workspace_dir: Optional[str] = None

@app.post("/api/conversations")
async def create_new_conversation(payload: CreateConvPayload):
    config = load_config()
    default_model = config.get("default_model", "qwen3.5-4b-instruct")
    title = payload.title if payload.title else 'New Conversation'
    
    conv_id = await create_conversation(
        model_name=default_model,
        title=title,
        workspace_dir=payload.workspace_dir
    )
    
    conv = await get_conversation(conv_id)
    return conv

@app.delete("/api/conversations/{conv_id}")
async def delete_conversation_route(conv_id: str):
    await delete_conversation(conv_id)
    return {"status": "ok"}

class UpdateWorkspacePayload(BaseModel):
    workspace_dir: str

@app.post("/api/conversations/{conv_id}/workspace")
async def set_conversation_workspace(conv_id: str, payload: UpdateWorkspacePayload):
    resolved = resolve_workspace_dir(payload.workspace_dir)
    await update_conversation_workspace(conv_id, str(resolved))
    return {"status": "ok", "workspace_dir": str(resolved)}

@app.post("/api/workspace/browse_dialog")
def browse_workspace_dialog():
    """Open a native OS directory picker dialog (pywebview, osascript on macOS, powershell on Windows, zenity on Linux)."""
    import sys, subprocess

    # 1. Try pywebview native window dialog if GUI window is active
    try:
        import webview
        if webview.windows and len(webview.windows) > 0:
            res = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if res and len(res) > 0 and res[0]:
                return {"status": "ok", "path": str(res[0])}
            return {"status": "cancelled", "path": None}
    except Exception:
        pass

    # 2. macOS native Finder folder picker via osascript
    if sys.platform == 'darwin':
        try:
            cmd = ["osascript", "-e", 'POSIX path of (choose folder with prompt "Seleziona la cartella di lavoro (Workspace)")']
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode == 0:
                chosen = proc.stdout.strip().rstrip('/')
                if chosen:
                    return {"status": "ok", "path": chosen}
            return {"status": "cancelled", "path": None}
        except Exception as e:
            logger.error(f"osascript folder picker error: {e}")

    # 3. Windows native folder picker via PowerShell
    if sys.platform == 'win32':
        try:
            ps_cmd = (
                "[System.Reflection.Assembly]::LoadWithPartialName('System.windows.forms') | Out-Null; "
                "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$dialog.Description = 'Seleziona la cartella di lavoro (Workspace)'; "
                "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $dialog.SelectedPath }"
            )
            proc = subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, text=True, timeout=120)
            if proc.returncode == 0:
                chosen = proc.stdout.strip()
                if chosen:
                    return {"status": "ok", "path": chosen}
            return {"status": "cancelled", "path": None}
        except Exception as e:
            logger.error(f"PowerShell folder picker error: {e}")

    # 4. Linux native folder picker via zenity
    if sys.platform.startswith("linux"):
        try:
            proc = subprocess.run(["zenity", "--file-selection", "--directory", "--title=Seleziona la cartella di lavoro (Workspace)"], capture_output=True, text=True, timeout=120)
            if proc.returncode == 0:
                chosen = proc.stdout.strip()
                if chosen:
                    return {"status": "ok", "path": chosen}
            return {"status": "cancelled", "path": None}
        except Exception:
            pass

    # 5. Tkinter fallback if available
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected_dir = filedialog.askdirectory(title="Seleziona la cartella di lavoro (Workspace)")
        root.destroy()
        if selected_dir:
            return {"status": "ok", "path": selected_dir}
        return {"status": "cancelled", "path": None}
    except Exception as e:
        logger.warning(f"Tkinter not available or failed: {e}")

    return {"status": "cancelled", "path": None}

@app.post("/api/conversations/{conv_id}/attach")
async def attach_file(conv_id: str, request: Request):
    filename_encoded = request.headers.get("X-Attachment-Filename", "")
    filename = urllib.parse.unquote(filename_encoded)
    if not filename:
        raise HTTPException(status_code=400, detail="Missing X-Attachment-Filename header")
        
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty file body")
        
    import tempfile
    from pathlib import Path
    import os
    from llampaca.attachments import extract_text, AttachmentError
    
    config = load_config()
    context_size = config.get("context_size", 8192)
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
        tmp.write(body)
        tmp_path = Path(tmp.name)
        
    try:
        text = extract_text(tmp_path)
    except AttachmentError as e:
        os.unlink(tmp_path)
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        os.unlink(tmp_path)
        return JSONResponse(status_code=500, content={"error": f"Extraction failed: {e}"})
        
    os.unlink(tmp_path)
    
    try:
        result = await agent_manager.stage_attachment(conv_id, filename, text, context_size)
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/conversations/{conv_id}/messages")
async def post_message(conv_id: str, request: Request):
    payload = await request.json()
    role = payload.get('role', 'user')
    content = payload.get('content', '')
    
    is_multimodal = isinstance(content, list)
    
    if not is_multimodal and content.strip().lower().startswith("/remember"):
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
            
    if not is_multimodal and content.strip().lower().startswith("/deep-search"):
        query = content.strip()[len("/deep-search"):].strip()
        if query:
            content = (
                f"{query}\n\n"
                "[The user requested a Deep Search. Before answering, you MUST use the "
                "web_search tool to perform a comprehensive and deep research on the query. "
                "Synthesize the findings into a highly detailed response.]"
            )

    attachments, index_notes = agent_manager.take_pending(conv_id)
    if attachments or index_notes:
        from llampaca.attachments import build_attachment_block
        blocks = [build_attachment_block(n, t) for n, t in attachments]
        blocks.extend(index_notes)
        if is_multimodal:
            blocks_str = "\n\n".join(blocks)
            if content and content[0].get("type") == "text":
                content[0]["text"] = blocks_str + "\n\n" + content[0].get("text", "")
            else:
                content.insert(0, {"type": "text", "text": blocks_str})
        else:
            content = "\n\n".join(blocks + [content])

    content_for_db = json.dumps(content) if is_multimodal else content
    user_msg_id = run_async(add_message(conv_id, role, content_for_db))

    conv_data = run_async(get_conversation(conv_id))
    if not conv_data:
        raise HTTPException(status_code=404, detail="Conversation not found")

    config = load_config()
    server_port = config.get("server_port", 8080)

    async def sse_generator():
        def _format_sse(kind, data):
            event_data = json.dumps({"kind": kind, "data": data})
            body = f"data: {event_data}\n"
            pad_needed = 512 - len(body.encode('utf-8')) - 3
            if pad_needed > 0:
                body += ":" + (" " * pad_needed) + "\n"
            return body + "\n"

        def _format_sse_comment():
            # Pure SSE comment (": ..."): every consumer must ignore it, and
            # our chat_controller.js parser only reacts to "data: " lines.
            # Padded like the events so it reliably flushes WKWebView's
            # withheld tail (see the keepalive loop below).
            return ":" + (" " * 509) + "\n\n"

        if not is_server_running(port=server_port):
            warn_msg = (
                f"Il server dei modelli non è attivo sulla porta {server_port}. "
                "Per parlare con l'agente, scarica o seleziona un modello dalla sezione 'Modelli' nell'interfaccia di LLAMPACA."
            )
            assistant_msg_id = run_async(add_message(conv_id, "assistant", warn_msg))
            yield _format_sse("text", warn_msg)
            yield _format_sse("done", {'assistant_message_id': assistant_msg_id})
            return

        q = queue.Queue()
        agent_manager.process_message(conv_id, content, user_msg_id, conv_data, config, q, params=payload)

        import asyncio
        import time as _time
        # WKWebView (the pywebview GUI window) withholds the tail of a
        # streamed fetch response (~1KB) until MORE bytes arrive on the
        # socket: measured here, the JS reader always ran ~2 events behind
        # the server. That is fatal for tool confirmations: the
        # tool_confirm_request event is the LAST thing written before the
        # server goes silent waiting for the user's answer — so it sat in
        # CFNetwork's buffer forever, the banner never rendered, and the
        # confirmation expired (Chrome delivers immediately, which is why
        # the bug never reproduced there). The fix: while the queue is
        # idle, emit a padded SSE comment (ignored by the JS parser) every
        # 0.4s to keep pushing withheld bytes out to the page.
        last_yield = _time.monotonic()
        try:
            while True:
                try:
                    msg = q.get_nowait()
                except queue.Empty:
                    if _time.monotonic() - last_yield > 0.4:
                        last_yield = _time.monotonic()
                        yield _format_sse_comment()
                    await asyncio.sleep(0.05)
                    continue

                last_yield = _time.monotonic()
                msg_type = msg[0]
                if msg_type == "event":
                    kind = msg[1]
                    data = msg[2]
                    yield _format_sse(kind, data)
                elif msg_type == "done":
                    data = msg[1]
                    yield _format_sse("done", data)
                elif msg_type == "error":
                    err_msg = msg[1]
                    yield _format_sse("error", err_msg)
                elif msg_type == "close":
                    break
        except asyncio.CancelledError:
            logger.info("Client disconnected, cancelling agent...")
            agent_manager.cancel_current()
            raise
                
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    }
    return StreamingResponse(sse_generator(), media_type="text/event-stream", headers=headers)

# --- USER CONFIRMATION ---
# The frontend (chat_controller.js resolveConfirmation) sends the confirm_id
# it received in the "tool_confirm_request" SSE event, plus the user's choice.
# This payload shape MUST match that JS call — the FastAPI refactor originally
# declared a different model (message_id/action) and called a method that did
# not exist, so every click on the banner failed with 422 and the confirmation
# eventually expired after 300s.
class ConfirmPayload(BaseModel):
    confirm_id: str
    allow: bool = False

@app.post("/api/confirm")
async def confirm_action(payload: ConfirmPayload):
    """Resolve a pending tool confirmation (the banner's Allow/Deny buttons).

    Wakes the agent coroutine parked in AgentManager.confirm_tool(). Returns
    "expired" when the id is unknown — e.g. the 300s timeout already fired —
    so the frontend can tell a stale click from a successful one.
    """
    found = agent_manager.resolve_confirmation(payload.confirm_id, payload.allow)
    return {"status": "ok" if found else "expired"}

@app.post("/api/cancel")
async def cancel_action():
    """Stop the in-flight generation (the Stop button). No payload: the
    frontend (chat_model.js cancel()) POSTs with an empty body, and there is
    at most one active turn to cancel. Also unblocks any pending
    confirmations (cancel_current denies them), so a turn stopped while the
    banner was up does not linger until the confirmation timeout."""
    cancelled = agent_manager.cancel_current()
    return {"cancelled": cancelled}

# --- OPEN FILE ---
class OpenFilePayload(BaseModel):
    path: str

@app.post("/api/open-file")
async def open_file(payload: OpenFilePayload):
    import subprocess, sys
    from llampaca.security import media_roots, PathNotAllowed

    # Handing an unchecked path to `open` / `os.startfile` / `xdg-open` means
    # handing it to the OS launcher: a .app bundle, .exe or .desktop file
    # would be executed, not displayed. Confine it to the same roots the media
    # endpoint uses — those are the files the UI can legitimately link to.
    try:
        resolved = confine_path(payload.path, media_roots())
    except PathNotAllowed:
        raise HTTPException(status_code=403, detail="Path outside the allowed directories.")

    path = str(resolved)
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        if sys.platform == 'darwin':
            subprocess.run(['open', path], check=True)
        elif sys.platform == 'win32':
            os.startfile(path)
        else:
            subprocess.run(['xdg-open', path], check=True)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- SETTINGS ---
@app.get("/api/settings")
async def get_settings():
    return load_config()

class SettingsPayload(BaseModel):
    server_port: Optional[int] = None
    context_size: Optional[int] = None
    n_threads: Optional[int] = None
    gpu_layers: Optional[int] = None
    embedding_gpu_layers: Optional[int] = None
    no_think: Optional[bool] = None
    kv_cache_type: Optional[str] = None
    draft_model: Optional[str] = None
    draft_model_gpu_layers: Optional[int] = None
    ssd_offload: Optional[bool] = None
    speculative_mode: Optional[str] = None
    temperature: Optional[float] = None
    mcp_servers: Optional[dict] = None

@app.post("/api/settings")
async def post_settings(payload: SettingsPayload, background_tasks: BackgroundTasks):
    config = load_config()
    
    requires_restart = False
    if payload.server_port is not None:
        config["server_port"] = payload.server_port
        requires_restart = True
    if payload.context_size is not None:
        config["context_size"] = payload.context_size
        requires_restart = True
    if payload.n_threads is not None:
        config["n_threads"] = payload.n_threads
        requires_restart = True
    if payload.gpu_layers is not None:
        config["gpu_layers"] = payload.gpu_layers
        requires_restart = True
    if payload.embedding_gpu_layers is not None:
        config["embedding_gpu_layers"] = payload.embedding_gpu_layers
        requires_restart = True
    if payload.kv_cache_type is not None:
        config["kv_cache_quant_k"] = payload.kv_cache_type
        config["kv_cache_quant_v"] = payload.kv_cache_type
        requires_restart = True
    if payload.no_think is not None:
        config["no_think"] = payload.no_think
    if payload.draft_model is not None:
        config["draft_model"] = payload.draft_model
        requires_restart = True
    if payload.draft_model_gpu_layers is not None:
        config["draft_model_gpu_layers"] = payload.draft_model_gpu_layers
        requires_restart = True
    if payload.ssd_offload is not None:
        config["ssd_offload"] = payload.ssd_offload
        requires_restart = True
    if payload.speculative_mode is not None:
        config["speculative_mode"] = payload.speculative_mode
        requires_restart = True
    if payload.temperature is not None:
        config["temperature"] = payload.temperature

    save_config(config)

    if requires_restart:
        agent_manager.is_restarting = True
        def restart_task():
            restart_via_agent_manager(
                agent_manager,
                port=config.get("server_port", 8080),
                context_size=config.get("context_size", DEFAULT_CONTEXT_SIZE),
                n_threads=config.get("n_threads", 4),
                gpu_layers=config.get("gpu_layers", -1),
                draft_model=config.get("draft_model", ""),
                draft_model_gpu_layers=config.get("draft_model_gpu_layers", -1),
                ssd_offload=config.get("ssd_offload", False),
                speculative_mode=config.get("speculative_mode", "none")
            )
        background_tasks.add_task(restart_task)
        return {"status": "ok", "message": "Settings saved. Restarting server in background...", "config": config}
    
    return {"status": "ok", "message": "Settings saved.", "config": config}

@app.get("/api/cli-shortcut-status")
def get_cli_shortcut_status():
    from llampaca.gui.cli_installer import is_cli_installed, CLI_PATH, USER_CLI_PATH
    installed = is_cli_installed()
    path_found = str(CLI_PATH) if CLI_PATH.exists() else (str(USER_CLI_PATH) if USER_CLI_PATH.exists() else None)
    return {"installed": installed, "path": path_found}

@app.post("/api/install-cli-shortcut")
def install_cli_shortcut():
    from llampaca.gui.cli_installer import install_cli_symlink
    return install_cli_symlink()

@app.get("/api/server/status")
async def server_status():
    from llampaca.config import load_config, MODELS_DIR, MODEL_PRESETS
    config = load_config()
    default_model = config.get("default_model", "")
    is_restarting = getattr(agent_manager, 'is_restarting', False)

    from llampaca.engine import state
    active = state.get_chat_server()
    if active and active.process and active.process.poll() is None:
        if is_server_running(port=active.port):
            active_model_name = active.model_path.name if hasattr(active, "model_path") else default_model
            return {"status": "ok", "running": True, "active_model": active_model_name, "is_restarting": is_restarting}

    has_installed_default = False
    if default_model:
        preset_file = MODEL_PRESETS[default_model]["file"] if default_model in MODEL_PRESETS else default_model
        if (MODELS_DIR / preset_file).exists() or (MODELS_DIR / default_model).exists():
            has_installed_default = True

    target_model = default_model if (has_installed_default and is_restarting) else ""
    return {"status": "ok", "running": False, "active_model": target_model, "is_restarting": is_restarting}

# --- MODELS ---


@app.get("/api/models")
def get_models():
        try:
            from llampaca.config import MODELS_DIR, MODEL_PRESETS, load_config
            from llampaca.engine.downloader import active_downloads, downloads_lock
            from llampaca.engine import state
            
            config = load_config()
            default_model = config.get("default_model", "")
            embedding_model = config.get("embedding_model", "")
            image_model = config.get("image_model", "")
            is_restarting = getattr(agent_manager, 'is_restarting', False)

            # Check which model is currently running in active llama-server processes
            active_chat = state.get_chat_server()
            running_chat_model = None
            if active_chat and active_chat.process and active_chat.process.poll() is None:
                if is_server_running(port=active_chat.port):
                    running_chat_model = active_chat.model_path.name if hasattr(active_chat, "model_path") else None

            active_embed = state.get_embed_server()
            running_embed_model = None
            if active_embed and active_embed.process and active_embed.process.poll() is None:
                if is_server_running(port=active_embed.port):
                    running_embed_model = active_embed.model_path.name if hasattr(active_embed, "model_path") else None

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
                
                kind = preset.get("kind", "chat")
                is_installed = filename in installed_filenames
                if kind == "embedding":
                    is_active = is_installed and (
                        (running_embed_model is not None and (running_embed_model == filename or running_embed_model == p_name))
                        or (is_restarting and (embedding_model == p_name or embedding_model == filename))
                    )
                elif kind == "image":
                    is_active = False
                else:
                    is_active = is_installed and (
                        (running_chat_model is not None and (running_chat_model == filename or running_chat_model == p_name))
                        or (is_restarting and (default_model == p_name or default_model == filename))
                    )

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
                    "filename": filename,
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
                # Identifichiamo i projector per una sezione separata
                if filename.startswith("mmproj-") or filename.startswith("mmproj_"):
                    linked_models = []
                    for meta_file in MODELS_DIR.glob("*.gguf.json"):
                        try:
                            with open(meta_file, 'r', encoding='utf-8') as f:
                                meta = json.load(f)
                                if meta.get("mmproj") == filename:
                                    linked_models.append(meta_file.name.replace(".json", ""))
                        except:
                            pass
                    
                    file = installed_paths[filename]
                    size_bytes = file.stat().st_size
                    from llampaca.cli import format_size
                    size_str = format_size(size_bytes)
                    
                    models_list.append({
                        "id": filename,
                        "name": filename,
                        "preset_id": None,
                        "description": "Modulo aggiuntivo per l'analisi di immagini.",
                        "size": size_str,
                        "quant": "Sconosciuta",
                        "installed": True,
                        "downloading": False,
                        "progress": 100,
                        "active": False,
                        "repo_id": None,
                        "kind": "projector",
                        "linked_models": linked_models
                    })
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
                if any(kw in name_lower for kw in ["sdxl", "sd1", "sd2", "sd3", "flux", "stable-diffusion", "diffusion"]):
                    kind = "image"
                    is_active = False
                else:
                    is_active = (
                        (running_chat_model is not None and running_chat_model == filename)
                        or (is_restarting and default_model == filename)
                    )
                
                models_list.append({
                    "id": filename,
                    "name": filename,
                    "filename": filename,
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
                        "filename": filename,
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
            
            
            
            
            
            
            return models_list
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))

    

@app.get("/api/models/search")
def search_models(q: str = "", page: int = 1):
    try:
        limit = 8
        results = search_hf_models(query=q, page=page, limit=limit)
        has_next = len(results) == limit
        
        return {
            "models": results,
            "pagination": {
                "currentPage": page,
                "hasNextPage": has_next
            }
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

class DefaultModelPayload(BaseModel):
    model_name: str
    kind: Optional[str] = "chat"

@app.post("/api/models/default")
def set_default_model(payload: DefaultModelPayload, background_tasks: BackgroundTasks):
    config = load_config()
    
    if payload.kind == "embedding":
        config["embedding_model"] = payload.model_name
        save_config(config)
        try:
            if agent_manager.embedding_service is not None:
                agent_manager.embedding_service.stop()
                agent_manager.embedding_service = None
        except Exception as e:
            logger.info(f"[GUI Server] Could not reset embedding service: {e}")
        logger.info(f"[GUI Server] Default embedding model changed to {payload.model_name}.")
        return {"status": "ok", "embedding_model": payload.model_name}
        
    elif payload.kind == "image":
        config["image_model"] = payload.model_name
        save_config(config)
        logger.info(f"[GUI Server] Default image model changed to {payload.model_name}.")
        return {"status": "ok", "image_model": payload.model_name}
        
    else:
        config["default_model"] = payload.model_name
        save_config(config)
        
        agent_manager.is_restarting = True
        def restart_task():
            restart_via_agent_manager(
                agent_manager,
                model_name=payload.model_name,
                port=config.get("server_port", 8080),
                context_size=config.get("context_size", DEFAULT_CONTEXT_SIZE),
                n_threads=config.get("n_threads", 6),
                gpu_layers=config.get("gpu_layers", -1)
            )
        background_tasks.add_task(restart_task)
        logger.info(f"[GUI Server] Default model changed to {payload.model_name}. Restarting llama-server in background...")
        return {"status": "ok", "default_model": payload.model_name}



class DownloadModelPayload(BaseModel):
    repo_id: Optional[str] = None
    filename: Optional[str] = None
    input_val: Optional[str] = None
    mmproj_filename: Optional[str] = None

@app.post("/api/models/download")
def download_model(payload: DownloadModelPayload):
    try:
        repo_id = payload.repo_id
        filename = payload.filename
        input_val = payload.input_val
        
        if input_val:
            parsed_repo, parsed_file = parse_hf_input(input_val)
            if parsed_repo and parsed_file:
                repo_id = parsed_repo
                filename = parsed_file
                
        if not repo_id or not filename:
            raise HTTPException(status_code=400, detail="Impossibile identificare repository HF o nome file.")
            
        mmproj_filename = payload.mmproj_filename
            
        from llampaca.engine.downloader import download_hf_model_async
        download_hf_model_async(repo_id, filename)
        
        if mmproj_filename:
            download_hf_model_async(repo_id, mmproj_filename)
            from llampaca.config import MODELS_DIR
            metadata_path = MODELS_DIR / f"{filename}.json"
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump({"mmproj": mmproj_filename}, f)
        
        return {"status": "download_started", "repo_id": repo_id, "filename": filename, "mmproj_filename": mmproj_filename}
        
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/models/{model_name}")
def delete_model(model_name: str):
    import urllib.parse
    model_name = urllib.parse.unquote(model_name)
    
    config = load_config()
    default_model = config.get("default_model", "")
    embedding_model = config.get("embedding_model", "")

    preset_file = None
    if default_model in MODEL_PRESETS:
        preset_file = MODEL_PRESETS[default_model]["file"]
    embedding_preset_file = None
    if embedding_model in MODEL_PRESETS:
        embedding_preset_file = MODEL_PRESETS[embedding_model]["file"]

    if model_name in (default_model, preset_file):
        raise HTTPException(status_code=400, detail="Non è possibile eliminare il modello di chat attualmente attivo/predefinito.")
    if model_name in (embedding_model, embedding_preset_file):
        raise HTTPException(status_code=400, detail="Non è possibile eliminare il modello di embedding attualmente predefinito.")

    # model_name arrives URL-decoded from the path, so '../../..' would walk
    # straight out of MODELS_DIR and the unlink() below would delete an
    # arbitrary file. Reject anything that is not a plain file name.
    if "/" in model_name or "\\" in model_name or model_name in ("", ".", ".."):
        raise HTTPException(status_code=400, detail="Nome modello non valido.")

    from llampaca.security import PathNotAllowed
    try:
        file_path = confine_path(Path(MODELS_DIR) / model_name, [Path(MODELS_DIR)])
    except PathNotAllowed:
        raise HTTPException(status_code=400, detail="Nome modello non valido.")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    metadata_path = Path(MODELS_DIR) / f"{model_name}.json"
    if metadata_path.exists():
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
                mmproj_filename = meta.get("mmproj")
                if mmproj_filename:
                    mmproj_path = Path(MODELS_DIR) / mmproj_filename
                    if mmproj_path.exists():
                        mmproj_path.unlink()
        except Exception:
            pass
        try:
            metadata_path.unlink()
        except Exception:
            pass

    file_path.unlink()
    return {"status": "ok"}

# --- MCP ---


@app.get("/api/mcp")
def get_mcp():
    from llampaca.config import MCP_CONFIG_PATH, load_mcp_config
    if MCP_CONFIG_PATH.exists():
        try:
            mtime = MCP_CONFIG_PATH.stat().st_mtime
            if mtime > getattr(agent_manager, 'mcp_config_mtime', 0):
                agent_manager.mcp_config_mtime = mtime
                import asyncio
                fut = asyncio.run_coroutine_threadsafe(agent_manager.reload_mcp_manager(), agent_manager.loop)
                fut.result(timeout=60.0)
        except Exception as e:
            logger.error(f"Error checking/reloading MCP config on get: {e}")

    mcp_config = load_mcp_config()
    servers = mcp_config.get("mcp_servers", {})
    
    result_list = []
    for name, cfg in servers.items():
        connected = False
        tools_count = 0
        if getattr(agent_manager, 'mcp_manager', None) and name in agent_manager.mcp_manager.sessions:
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
    return result_list

@app.get("/api/mcp/search")
def search_mcp(q: str = "", page: str = "1"):
    import requests
    import urllib.parse
    try:
        url = f"https://api.smithery.ai/servers?pageSize=12&page={page}"
        if q:
            url += f"&q={urllib.parse.quote(q)}"
            
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
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
            return {
                "servers": mapped_servers,
                "pagination": data.get("pagination", {"currentPage": 1, "totalPages": 1})
            }
        return {"servers": [], "pagination": {"currentPage": 1, "totalPages": 1}}
    except Exception as e:
        logger.error(f"MCP search error: {e}")
        return {"servers": [], "pagination": {"currentPage": 1, "totalPages": 1}}

@app.get("/api/mcp/config-schema")
def mcp_config_schema(name: str = ""):
    import requests
    if not name:
        raise HTTPException(status_code=400, detail="Parametro 'name' obbligatorio.")
    try:
        url = f"https://api.smithery.ai/servers/{name}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        
        config_schema = {"type": "object", "properties": {}}
        connections = data.get("connections", [])
        if connections:
            config_schema = connections[0].get("configSchema", config_schema)
        return config_schema
    except Exception as e:
        logger.error(f"Error inspecting MCP: {e}")
        return {}

class InstallMcpPayload(BaseModel):
    name: str
    command: str
    args: Optional[List[str]] = []
    env: Optional[Dict[str, str]] = {}

@app.post("/api/mcp/install")
def install_mcp(payload: InstallMcpPayload):
    if not payload.name or not payload.command:
        raise HTTPException(status_code=400, detail="Parametri 'name' e 'command' obbligatori.")
    try:
        from llampaca.config import load_mcp_config, save_mcp_config
        mcp_config = load_mcp_config()
        if "mcp_servers" not in mcp_config:
            mcp_config["mcp_servers"] = {}
        mcp_config["mcp_servers"][payload.name] = {
            "command": payload.command,
            "args": payload.args,
            "env": payload.env
        }
        save_mcp_config(mcp_config)
        
        import asyncio
        fut = asyncio.run_coroutine_threadsafe(agent_manager.reload_mcp_manager(), agent_manager.loop)
        fut.result(timeout=60.0)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/mcp/uninstall/{package_id}")
def uninstall_mcp(package_id: str):
    import urllib.parse
    name = urllib.parse.unquote(package_id)
    try:
        from llampaca.config import load_mcp_config, save_mcp_config
        mcp_config = load_mcp_config()
        if name in mcp_config.get("mcp_servers", {}):
            del mcp_config["mcp_servers"][name]
            save_mcp_config(mcp_config)
            
        import asyncio
        fut = asyncio.run_coroutine_threadsafe(agent_manager.reload_mcp_manager(), agent_manager.loop)
        fut.result(timeout=60.0)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- TOOLS ---

@app.get("/api/tools")
def get_tools():
    import inspect
    from llampaca.tools import build_default_registry

    # Prefer the live registry (includes MCP tools) over a fresh one
    reg = getattr(agent_manager, "registry", None)
    if reg is None:
        reg = build_default_registry()

    results = []
    for name, tool in reg._tools.items():
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

    # Sort: custom first, then built-in, then MCP, alphabetically
    def sort_key(t):
        if t["is_custom"]:
            return (0, t["name"])
        elif t["is_mcp"]:
            return (2, t["mcp_server"], t["name"])
        else:
            return (1, t["name"])

    results.sort(key=sort_key)
    return results

class CustomToolPayload(BaseModel):
    name: str
    code: str

@app.post("/api/tools/custom")
def post_custom_tool(payload: CustomToolPayload):
    import ast
    name = payload.name.strip()
    code = payload.code
    requires_confirmation = False  # default

    if not name:
        raise HTTPException(status_code=400, detail="Il nome dello strumento è obbligatorio.")
    if not re.match("^[a-zA-Z0-9_]+$", name):
        raise HTTPException(status_code=400, detail="Nome non valido. Usa solo lettere, numeri e underscore.")

    # Ensure the primary function in code is named `name`
    if re.search(r'^\s*def\s+[a-zA-Z0-9_]+', code, flags=re.MULTILINE):
        code = re.sub(r'^\s*def\s+[a-zA-Z0-9_]+', f'def {name}', code, count=1, flags=re.MULTILINE)

    try:
        tree = ast.parse(code)
    except SyntaxError as se:
        raise HTTPException(status_code=400, detail=f"Errore di sintassi in Python: {se.msg} alla riga {se.lineno}")

    # Strip existing requires_confirmation lines
    cleaned_code = re.sub(r'\n[a-zA-Z0-9_]+\.requires_confirmation\s*=\s*(True|False)\s*', '', code)
    tree_cleaned = ast.parse(cleaned_code)

    func_names = [
        node.name for node in tree_cleaned.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    ]
    if not func_names:
        raise HTTPException(status_code=400, detail="Il codice deve contenere almeno una funzione pubblica.")

    flag_lines = "".join(f"\n{fn}.requires_confirmation = {requires_confirmation}\n" for fn in func_names)
    code_to_write = cleaned_code.rstrip() + "\n" + flag_lines

    from llampaca.config import LLAMPACA_DIR
    custom_dir = LLAMPACA_DIR / "custom_tools"
    custom_dir.mkdir(parents=True, exist_ok=True)
    file_path = custom_dir / f"{name}.py"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code_to_write)

    # Reload registry
    import asyncio
    fut = asyncio.run_coroutine_threadsafe(agent_manager.reload_registry(), agent_manager.loop)
    fut.result(timeout=60.0)
    return {"status": "ok"}

@app.delete("/api/tools/custom/{tool_name}")
def delete_custom_tool(tool_name: str):
    import urllib.parse, ast
    name = urllib.parse.unquote(tool_name).strip()

    from llampaca.config import LLAMPACA_DIR
    custom_dir = LLAMPACA_DIR / "custom_tools"

    # 1. Direct file stem match
    file_path = custom_dir / f"{name}.py"
    if file_path.exists():
        file_path.unlink()

    # 2. Search for any .py file containing function `name`
    if custom_dir.exists():
        for py_file in list(custom_dir.glob("*.py")):
            try:
                with open(py_file, "r", encoding="utf-8") as f:
                    content = f.read()
                tree = ast.parse(content)
                funcs = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
                if name in funcs or py_file.stem == name:
                    py_file.unlink()
            except Exception:
                pass

    # Evict cached modules
    import sys as _sys
    modules_to_del = [m for m in _sys.modules if m.startswith("llampaca_custom_")]
    for m in modules_to_del:
        if name in m or f"llampaca_custom_{name}" == m:
            del _sys.modules[m]

    # Reload registry
    import asyncio
    fut = asyncio.run_coroutine_threadsafe(agent_manager.reload_registry(), agent_manager.loop)
    fut.result(timeout=60.0)
    return {"status": "ok"}

# --- WIKI ---
from llampaca import wiki

@app.get("/api/wiki")
async def get_wiki_pages():
    try:
        pages = [{"name": name, "description": desc} for name, desc in wiki.list_pages()]
        return {"pages": pages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/wiki/{page_name}")
async def get_wiki_page(page_name: str):
    import urllib.parse
    name = urllib.parse.unquote(page_name)
    try:
        content = wiki.read_page(name)
        return {"name": name, "content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Page non trovata")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class WikiPagePayload(BaseModel):
    name: str
    content: str

@app.post("/api/wiki")
async def post_wiki_page(payload: WikiPagePayload):
    try:
        wiki.write_page(payload.name, payload.content)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/wiki/{page_name}")
async def delete_wiki_page(page_name: str):
    import urllib.parse
    name = urllib.parse.unquote(page_name)
    try:
        path = wiki.page_path(name)
        if path.is_file():
            path.unlink()
            return {"status": "ok", "name": wiki.slugify(name)}
        else:
            raise HTTPException(status_code=404, detail="Page not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- SKILLS ---
from llampaca import skills

@app.get("/api/skills")
def get_skills_list():
    try:
        skills_list = skills.list_skills()
        return {"skills": skills_list, "max_chars": getattr(skills, 'MAX_SKILL_CHARS', 5000)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/skills/{skill_name}")
def get_skill(skill_name: str):
    import urllib.parse
    name = urllib.parse.unquote(skill_name)
    try:
        content = skills.read_skill(name)
        return {"slug": skills.slugify(name), "content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Skill non trovata")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class SkillPayload(BaseModel):
    name: str
    content: str

@app.post("/api/skills")
async def post_skill(payload: SkillPayload):
    try:
        skills.write_skill(payload.name, payload.content)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/skills/{skill_name}")
async def delete_skill_route(skill_name: str):
    try:
        if skills.delete_skill(skill_name):
            return {"status": "ok"}
        else:
            raise HTTPException(status_code=404, detail="Skill not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- HELPER FUNCTIONS ---
import sys as _sys

def _get_client_config_paths():
    home = Path.home()
    system = _sys.platform

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
            code_user / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",
            code_user / "globalStorage" / "rooveterinaryinc.roo-cline" / "settings" / "cline_mcp_settings.json",
            code_user / "settings.json",
            code_user / "mcp.json"
        ])
    elif system == 'win32':
        appdata = os.environ.get("APPDATA")
        if appdata:
            code_user = Path(appdata) / "Code" / "User"
            vscode_paths.extend([
                code_user / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",
                code_user / "globalStorage" / "rooveterinaryinc.roo-cline" / "settings" / "cline_mcp_settings.json",
                code_user / "settings.json",
                code_user / "mcp.json"
            ])
    else:
        code_user = home / ".config" / "Code" / "User"
        vscode_paths.extend([
            code_user / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",
            code_user / "globalStorage" / "rooveterinaryinc.roo-cline" / "settings" / "cline_mcp_settings.json",
            code_user / "settings.json",
            code_user / "mcp.json"
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
    venv_bin = Path(_sys.executable).parent / "llampaca"
    if venv_bin.exists():
        return str(venv_bin)
    import shutil
    which_cmd = shutil.which("llampaca")
    if which_cmd:
        return which_cmd
    return "llampaca"

# --- CLIENTS/MCP SETUP ---

def _is_mcp_installed(paths_list):
    for p in paths_list:
        if p and p.exists():
            try:
                import json
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "mcpServers" in data and "llampaca" in data["mcpServers"]:
                    return True
            except Exception:
                pass
    return False

def _setup_mcp_in_file(p, action):
    import json
    if not p.exists():
        if action == "remove":
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {}
    else:
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
            
    if "mcpServers" not in data or not isinstance(data.get("mcpServers"), dict):
        data["mcpServers"] = {}
        
    modified = False
    if action == "install":
        data["mcpServers"]["llampaca"] = {
            "command": _resolve_llampaca_command(),
            "args": ["mcp"]
        }
        modified = True
    elif action == "remove":
        if "llampaca" in data["mcpServers"]:
            del data["mcpServers"]["llampaca"]
            modified = True
            
    if modified:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

@app.get("/api/clients/status")
async def clients_status():
    paths = _get_client_config_paths()
    return {
        "claude_desktop": {
            "mcp_connected": _is_mcp_installed([paths.get("claude_desktop")])
        },
        "vscode": {
            "mcp_connected": _is_mcp_installed(paths.get("vscode", []))
        }
    }

class McpSetupPayload(BaseModel):
    target: str
    action: str

@app.post("/api/clients/mcp/setup")
async def clients_mcp_setup(payload: McpSetupPayload):
    paths = _get_client_config_paths()
    if payload.target == "claude_desktop":
        target_paths = [paths.get("claude_desktop")]
    elif payload.target == "vscode":
        target_paths = paths.get("vscode", [])
    else:
        raise HTTPException(status_code=400, detail="Invalid target")
        
    try:
        for p in target_paths:
            if p:
                _setup_mcp_in_file(p, payload.action)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Setup failed: {e}")

@app.get("/api/clients/snippets")
async def clients_snippets():
    config = load_config()
    port = config.get("server_port", 8080)
    model = config.get("default_model", "Nessun modello attivo")
    url = f"http://127.0.0.1:{port}/v1"
    
    snippet_continue = f"""models:
  - title: Llampaca Local
    provider: openai
    model: AUTODETECT
    apiBase: {url}"""
    
    snippet_cline = f"""{{
  "mcpServers": {{
    "llampaca": {{
      "command": "{_resolve_llampaca_command()}",
      "args": ["mcp"]
    }}
  }}
}}"""
    
    snippet_python = f"""from openai import OpenAI
client = OpenAI(base_url="{url}", api_key="not-needed")
response = client.chat.completions.create(
    model="{model}",
    messages=[{{"role": "user", "content": "Ciao!"}}]
)
print(response.choices[0].message.content)"""

    return {
        "continue": snippet_continue,
        "cline_roo": snippet_cline,
        "mcp": snippet_cline,
        "python": snippet_python,
        "endpoint_url": url,
        "active_model": model
    }

# --- MEDIA ---
def _serve_media(raw_path: str) -> FileResponse:
    """
    Serve one file, confined to the media roots (generated-images directory
    and the active workspace).

    This endpoint used to build its target as ``"/" + path`` with no checks at
    all, which turned it into an unauthenticated read primitive for the entire
    filesystem: ``GET /api/media/etc/passwd`` returned the file. Everything
    now goes through confine_path, which resolves symlinks and '..' before
    testing containment, and refuses the credential deny list.
    """
    from llampaca.security import media_roots, PathNotAllowed

    if not raw_path:
        raise HTTPException(status_code=400, detail="Missing 'path'.")

    try:
        abs_path = confine_path(raw_path, media_roots())
    except PathNotAllowed:
        # Deliberately indistinguishable from a missing file: a 403 here would
        # let a caller probe which paths exist outside the allowed roots.
        raise HTTPException(status_code=404, detail="File not found")

    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    import mimetypes
    mime_type, _ = mimetypes.guess_type(str(abs_path))
    if not mime_type:
        mime_type = 'application/octet-stream'

    return FileResponse(str(abs_path), media_type=mime_type)


@app.get("/api/media")
async def get_media_query(path: str = ""):
    """Query-string form: ``/api/media?path=<abs path>``. This is the form the
    chat frontend emits (see formatImageLinks in ChatView.js); only the path
    form below existed before, so inline image previews returned 404."""
    return _serve_media(path)


@app.get("/api/media/{path:path}")
async def get_media(path: str):
    """Path form: ``/api/media/<abs path without leading slash>``."""
    return _serve_media("/" + path if not path.startswith("/") else path)


# --- STATIC FILES AND FALLBACK ROUTE ---
from fastapi.responses import HTMLResponse
import mimetypes

# Fallback for Vue Router / SPA: catch all non-API routes and return index.html
@app.get("/{full_path:path}")
async def serve_spa_or_static(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API route not found")
        
    frontend_dir = Path(os.path.dirname(__file__)).resolve()

    # os.path.join() happily accepts '../../..' and uvicorn does not normalize
    # the request path, so joining the raw value served any file on the disk
    # (verified: '/..%2f..%2f../etc/passwd' returned the file). Confine the
    # join result to the GUI asset directory before touching it.
    file_path = None
    if full_path:
        try:
            file_path = confine_path(os.path.join(frontend_dir, full_path), [frontend_dir])
        except PermissionError:
            file_path = None  # fall through to the SPA index below
    else:
        file_path = frontend_dir / "index.html"

    if file_path is not None and file_path.is_file():
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if not mime_type:
            mime_type = 'application/octet-stream'
        return FileResponse(str(file_path), media_type=mime_type)

    # SPA fallback
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
        
    return HTMLResponse("<html><body><h1>Llampaca GUI Error</h1><p>Frontend files not found.</p></body></html>", status_code=404)



def parse_hf_input(input_str: str):
    """
    Parses a Hugging Face input string into (repo_id, filename).
    """
    input_str = input_str.strip()
    if not input_str:
        return None, None
        
    # Match URL pattern
    url_pattern = r"https?://huggingface\.co/([^/]+)/([^/]+)/(?:resolve|blob)/[^/]+/(.+)"
    match = re.match(url_pattern, input_str)
    if match:
        repo_user, repo_name, filename = match.groups()
        return f"{repo_user}/{repo_name}", filename
        
    # Match short identifier pattern
    short_pattern = r"([^/]+)/([^/]+)/(.+)"
    match = re.match(short_pattern, input_str)
    if match:
        repo_user, repo_name, filename = match.groups()
        return f"{repo_user}/{repo_name}", filename
        
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
        logger.info(f"HF Search error: {e}")
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
                # Separate mmproj files
                mmproj_files = [f for f in gguf_files if "mmproj" in f["filename"].lower()]
                main_gguf_files = [f for f in gguf_files if "mmproj" not in f["filename"].lower()]
                
                # Sort files alphabetically/by name
                main_gguf_files.sort(key=lambda x: x["filename"])
                mmproj_files.sort(key=lambda x: x["filename"])
                
                results.append({
                    "repo_id": repo_id,
                    "downloads": downloads,
                    "gguf_files": main_gguf_files,
                    "mmproj_files": mmproj_files,
                    "download_mmproj": len(mmproj_files) > 0,
                    "description": f"Repository con {len(main_gguf_files)} file GGUF."
                })
        except Exception as e:
            logger.error(f"Error reading model info for {repo_id}: {e}")
            
    return results

