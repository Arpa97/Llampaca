
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
    update_conversation_summary
)
from llampaca.config import load_config, save_config, MODELS_DIR, MODEL_PRESETS, DEFAULT_CONTEXT_SIZE
from llampaca.gui.agent_manager import agent_manager, run_async, is_server_running
from llampaca.gui.restart_bridge import restart_via_agent_manager

logger = logging.getLogger(__name__)

app = FastAPI(title="Llampaca GUI API")

# --- CONVERSATIONS ---
@app.get("/api/conversations")
async def get_conversations():
    convs = run_async(list_conversations())
    return convs

@app.get("/api/conversations/{conv_id}")
async def get_conversation_by_id(conv_id: str):
    conv = run_async(get_conversation(conv_id))
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv

class CreateConvPayload(BaseModel):
    title: Optional[str] = None
    system_prompt: Optional[str] = None

@app.post("/api/conversations")
async def create_new_conversation(payload: CreateConvPayload):
    config = load_config()
    default_model = config.get("default_model", "qwen3.5-4b-instruct")
    title = payload.title if payload.title else 'New Conversation'
    
    conv_id = run_async(create_conversation(
        model_name=default_model,
        title=title
    ))
    
    conv = run_async(get_conversation(conv_id))
    return conv

@app.delete("/api/conversations/{conv_id}")
async def delete_conversation_route(conv_id: str):
    run_async(delete_conversation(conv_id))
    return {"status": "ok"}

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

        if not is_server_running(port=server_port):
            warn_msg = (
                f"Il server dei modelli (llama-server) non è attivo sulla porta {server_port}. "
                "Avvialo nel tuo terminale con 'llampaca run' per parlare con l'agente."
            )
            assistant_msg_id = run_async(add_message(conv_id, "assistant", warn_msg))
            yield _format_sse("text", warn_msg)
            yield _format_sse("done", {'assistant_message_id': assistant_msg_id})
            return

        q = queue.Queue()
        agent_manager.process_message(conv_id, content, user_msg_id, conv_data, config, q)
        
        import asyncio
        try:
            while True:
                try:
                    msg = q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue
                    
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
class ConfirmPayload(BaseModel):
    message_id: str
    action: str

@app.post("/api/confirm")
async def confirm_action(payload: ConfirmPayload):
    agent_manager.resolve_pending(payload.message_id, "confirm", payload.action)
    return {"status": "ok"}

@app.post("/api/cancel")
async def cancel_action(payload: ConfirmPayload):
    agent_manager.resolve_pending(payload.message_id, "cancel", payload.action)
    return {"status": "ok"}

# --- OPEN FILE ---
class OpenFilePayload(BaseModel):
    path: str

@app.post("/api/open-file")
async def open_file(payload: OpenFilePayload):
    import subprocess, sys
    path = payload.path
    if not os.path.exists(path):
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
    server_port: int
    context_size: int
    threads: int
    gpu_layers: int

@app.post("/api/settings")
async def post_settings(payload: SettingsPayload, background_tasks: BackgroundTasks):
    config = load_config()
    old_port = config.get("server_port")
    
    config["server_port"] = payload.server_port
    config["context_size"] = payload.context_size
    config["threads"] = payload.threads
    config["gpu_layers"] = payload.gpu_layers
    save_config(config)

    def restart_task():
        restart_via_agent_manager(
            port=payload.server_port,
            context_size=payload.context_size,
            n_threads=payload.threads,
            gpu_layers=payload.gpu_layers
        )

    background_tasks.add_task(restart_task)
    return {"status": "ok", "message": "Settings saved. Restarting server in background..."}

# --- MODELS ---


@app.get("/api/models")
def get_models():
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
        
        def restart_task():
            restart_via_agent_manager(
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

    file_path = Path(MODELS_DIR) / model_name
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
    venv_bin = Path(_sys.executable).parent / "llampaca"
    if venv_bin.exists():
        return str(venv_bin)
    import shutil
    which_cmd = shutil.which("llampaca")
    if which_cmd:
        return which_cmd
    return "llampaca"

# --- CLIENTS/MCP SETUP ---
@app.get("/api/clients/status")
async def clients_status():

    paths = _get_client_config_paths()
    res = []
    for client, cp in paths.items():
        res.append({
            "client": client,
            "installed": cp is not None and os.path.exists(cp),
            "config_path": str(cp) if cp else None
        })
    return {"clients": res}

class McpSetupPayload(BaseModel):
    client: str

@app.post("/api/clients/mcp/setup")
async def clients_mcp_setup(payload: McpSetupPayload):
    import subprocess
    cmd = _resolve_llampaca_command(['mcp', 'setup', payload.client])
    try:
        subprocess.run(cmd, check=True)
        return {"status": "ok"}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=f"Setup failed: {e}")

@app.get("/api/clients/snippets")
async def clients_snippets():
    import subprocess
    cmd = _resolve_llampaca_command(['mcp', 'setup', 'snippet'])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        return {"snippet": res.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- MEDIA ---
@app.get("/api/media/{path:path}")
async def get_media(path: str):
    abs_path = "/" + path
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    import mimetypes
    mime_type, _ = mimetypes.guess_type(abs_path)
    if not mime_type:
        mime_type = 'application/octet-stream'
        
    return FileResponse(abs_path, media_type=mime_type)


# --- STATIC FILES AND FALLBACK ROUTE ---
from fastapi.responses import HTMLResponse
import mimetypes

# Fallback for Vue Router / SPA: catch all non-API routes and return index.html
@app.get("/{full_path:path}")
async def serve_spa_or_static(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API route not found")
        
    frontend_dir = os.path.dirname(__file__)
    file_path = os.path.join(frontend_dir, full_path) if full_path else os.path.join(frontend_dir, "index.html")

    if os.path.exists(file_path) and not os.path.isdir(file_path):
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = 'application/octet-stream'
        return FileResponse(file_path, media_type=mime_type)
        
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

