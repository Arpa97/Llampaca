import os
import sys
import time
import asyncio
# pyrefly: ignore [missing-import]
import click
from pathlib import Path
from tabulate import tabulate

from llampaca.config import (
    load_config,
    save_config,
    MODELS_DIR,
    MODEL_PRESETS,
    LLAMPACA_DIR,
    ensure_dirs,
    DEFAULT_CONTEXT_SIZE,
)
from llampaca.engine.downloader import download_llama_binaries, download_hf_model
from llampaca.engine.server import LlamaServer, is_port_in_use
from llampaca.engine.client import LlamaClient
from llampaca.agent import Agent
from llampaca.agent.prompts import DEFAULT_SYSTEM_PROMPT
from llampaca.tools import build_default_registry
from llampaca import wiki
from llampaca import skills

from llampaca.logutil import setup_logging
from llampaca.cli_utils import format_size
from llampaca.cli_chat import async_run_chat
@click.group()
def main():
    """Llampaca - Run your local AI agents and assistants for free."""
    setup_logging(console=True)
    ensure_dirs()

@main.command()
def init():
    """Initialize application directories and download llama.cpp binaries."""
    click.echo("=== Initializing Llampaca ===")
    ensure_dirs()
    click.echo(f"Workspace directory: {LLAMPACA_DIR}")
    
    success = download_llama_binaries()
    if success:
        click.echo("\nInitialization completed successfully!")
        click.echo("You can now download a model using: llampaca models download")
    else:
        click.echo("\nInitialization failed. Please check the logs and try again.")
        sys.exit(1)

@main.group()
def models():
    """Manage local GGUF models."""
    pass

@models.command(name="list")
def list_models():
    """List downloaded models and available presets."""
    config = load_config()
    default_model_name = config.get("default_model", "")
    
    # List downloaded local models
    gguf_files = list(MODELS_DIR.glob("*.gguf"))
    
    click.echo("=== Local Models ===")
    if not gguf_files:
        click.echo("No local GGUF models downloaded yet. Use 'llampaca models download' to get one.\n")
    else:
        local_table = []
        for file in gguf_files:
            size_str = format_size(file.stat().st_size)
            is_default = "Yes" if file.name == default_model_name or any(
                p_name == default_model_name and preset["file"] == file.name 
                for p_name, preset in MODEL_PRESETS.items()
            ) else "No"
            local_table.append([file.name, size_str, is_default, str(file)])
            
        click.echo(tabulate(local_table, headers=["Filename", "Size", "Default", "Path"], tablefmt="simple"))
        click.echo()
        
    click.echo("=== Recommended Presets ===")
    preset_table = []
    for name, preset in MODEL_PRESETS.items():
        is_downloaded = "Installed" if (MODELS_DIR / preset["file"]).exists() else "Not Installed"
        kind_label = preset.get("kind", "chat").upper()
        preset_table.append([name, kind_label, preset["repo"], preset["file"], is_downloaded, preset["description"]])
        
    click.echo(tabulate(preset_table, headers=["Preset Name", "Kind", "HF Repo", "Filename", "Status", "Description"], tablefmt="simple"))

@models.command(name="download")
@click.argument("preset_name", required=False)
@click.option("--preset", type=click.Choice(list(MODEL_PRESETS.keys())), help="Download a recommended model preset")
@click.option("--repo", help="Hugging Face repository ID (e.g. Qwen/Qwen3.5-4B-Instruct-GGUF)")
@click.option("--file", help="GGUF filename in the repository")
def download_model(preset_name, preset, repo, file):
    """Download a GGUF model from Hugging Face."""
    chosen_preset = preset or preset_name
    if chosen_preset and chosen_preset in MODEL_PRESETS:
        preset_info = MODEL_PRESETS[chosen_preset]
        repo_id = preset_info["repo"]
        filename = preset_info["file"]
    elif repo and file:
        repo_id = repo
        filename = file
    else:
        # Prompt user with options if no parameters are passed
        click.echo("Please choose a model preset to download:")
        presets_list = list(MODEL_PRESETS.keys())
        for idx, name in enumerate(presets_list):
            kind_tag = f"[{MODEL_PRESETS[name].get('kind', 'chat').upper()}] "
            click.echo(f"[{idx + 1}] {kind_tag}{name} ({MODEL_PRESETS[name]['description']})")
        click.echo(f"[{len(presets_list) + 1}] Custom Hugging Face model")
        
        choice = click.prompt("Enter choice", type=int)
        
        if 1 <= choice <= len(presets_list):
            selected = presets_list[choice - 1]
            preset_info = MODEL_PRESETS[selected]
            repo_id = preset_info["repo"]
            filename = preset_info["file"]
        elif choice == len(presets_list) + 1:
            repo_id = click.prompt("Enter Hugging Face Repo ID (e.g. Qwen/Qwen3.5-4B-Instruct-GGUF)")
            filename = click.prompt("Enter GGUF Filename (e.g. Qwen3.5-4B-Instruct-Q4_K_M.gguf)")
        else:
            click.echo("Invalid choice.")
            sys.exit(1)
            
    try:
        download_hf_model(repo_id, filename)

        # Update the config key matching the model's role. An embedding
        # model must never become the *chat* default: `llampaca run` would
        # try to converse with a model that cannot generate text. The kind
        # is recovered from the preset table by filename, which also covers
        # downloads made via --repo/--file for a known preset file.
        from llampaca.config import get_preset_for_file
        preset_info = get_preset_for_file(filename)
        kind = (preset_info or {}).get("kind", "chat")

        config = load_config()
        if kind == "embedding":
            config["embedding_model"] = filename
            save_config(config)
            click.echo(f"Successfully downloaded '{filename}' and set it as the embedding model (for document search/RAG).")
        elif kind == "image":
            click.echo(f"Successfully downloaded image generation model '{filename}'.")
        else:
            config["default_model"] = filename
            save_config(config)
            click.echo(f"Successfully downloaded and set '{filename}' as the default model.")
    except Exception as e:
        click.echo(f"Error downloading model: {e}", err=True)
        sys.exit(1)

@models.command(name="remove")
@click.argument("filename")
def remove_model(filename):
    """Delete a local GGUF model file."""
    model_path = MODELS_DIR / filename
    if not model_path.exists():
        # Check if they specified a preset name instead
        if filename in MODEL_PRESETS:
            model_path = MODELS_DIR / MODEL_PRESETS[filename]["file"]
        else:
            click.echo(f"Model file '{filename}' not found in {MODELS_DIR}")
            sys.exit(1)
            
    if click.confirm(f"Are you sure you want to delete {model_path.name}?"):
        model_path.unlink()
        click.echo(f"Deleted {model_path.name}")

@main.command()
@click.argument("model_name", required=False)
@click.option("--port", type=int, help="Port to run llama-server on")
@click.option("--ctx", type=int, help="Context size")
@click.option("--threads", type=int, help="Number of CPU threads to use")
@click.option("--gpu", type=int, help="Number of GPU layers to offload (-1 for auto)")
@click.option("--no-tools", is_flag=True, default=False, help="Disable agent tools (plain chat mode)")
@click.option("--no-think", is_flag=True, default=False,
              help="Disable the model's hidden 'thinking' phase (reasoning models like Qwen3): faster responses, slightly lower quality on complex tasks")
@click.option("--draft-model", help="Speculative decoding: draft model file name to accelerate generation")
@click.option("--draft-gpu", type=int, help="Number of GPU layers for draft model (-1 for auto)")
@click.option("--ssd-offload", is_flag=True, default=False, help="Enable SSD Offloading via mmap for massive models")
@click.option("-w", "--workspace", help="Cartella di lavoro (workspace) per la sessione.")
def run(model_name, port, ctx, threads, gpu, no_tools, no_think, draft_model, draft_gpu, ssd_offload, workspace):
    """Launch llama-server and open an interactive agent session."""
    if workspace:
        from llampaca.tools.filesystem import set_workspace_root
        set_workspace_root(workspace)
    config = load_config()
    
    # 1. Resolve model path
    if not model_name:
        model_name = config.get("default_model", "")
        # Resolve to preset filename if default_model refers to a preset name
        if model_name in MODEL_PRESETS:
            model_name = MODEL_PRESETS[model_name]["file"]
            
    if not model_name:
        click.echo("Error: No default model configured, and no model specified.")
        click.echo("Please download a model using: llampaca models download")
        sys.exit(1)
        
    model_path = MODELS_DIR / model_name
    # Fallback to direct absolute/relative path if not found in models directory
    if not model_path.exists():
        model_path = Path(model_name)
        if not model_path.exists():
            # Check if user specified a preset name
            if model_name in MODEL_PRESETS:
                preset_file = MODEL_PRESETS[model_name]["file"]
                model_path = MODELS_DIR / preset_file
                
    if not model_path.exists():
        click.echo(f"Error: Model '{model_name}' could not be resolved to a file path.")
        click.echo(f"Looked in {MODELS_DIR} and current working directory.")
        sys.exit(1)
        
    import asyncio
    asyncio.run(async_run_chat(model_path, port, ctx, threads, gpu, no_tools, no_think, draft_model, draft_gpu, ssd_offload))

@main.command(name="serve")
@click.argument("model_name", required=False)
@click.option("--port", type=int, help="Port to run llama-server on")
@click.option("--api-port", type=int, default=8090, help="Port to run Llampaca API server on")
@click.option("--ctx", type=int, help="Context size")
@click.option("--threads", type=int, help="Number of CPU threads to use")
@click.option("--gpu", type=int, help="Number of GPU layers to offload (-1 for auto)")
@click.option("--draft-model", help="Speculative decoding: draft model file name to accelerate generation")
@click.option("--draft-gpu", type=int, help="Number of GPU layers for draft model (-1 for auto)")
@click.option("--ssd-offload", is_flag=True, default=False, help="Enable SSD Offloading via mmap for massive models")
def serve(model_name, port, api_port, ctx, threads, gpu, draft_model, draft_gpu, ssd_offload):
    """Avvia il server Llampaca completo (llama-server + API HTTP) senza interfaccia grafica."""
    config = load_config()
    
    # 1. Resolve model path
    if not model_name:
        model_name = config.get("default_model", "")
        if model_name in MODEL_PRESETS:
            model_name = MODEL_PRESETS[model_name]["file"]
            
    if not model_name:
        click.echo("Error: No default model configured, and no model specified.")
        click.echo("Please download a model using: llampaca models download")
        sys.exit(1)
        
    model_path = MODELS_DIR / model_name
    if not model_path.exists():
        model_path = Path(model_name)
        if not model_path.exists():
            if model_name in MODEL_PRESETS:
                preset_file = MODEL_PRESETS[model_name]["file"]
                model_path = MODELS_DIR / preset_file
                
    if not model_path.exists():
        click.echo(f"Error: Model '{model_name}' could not be resolved to a file path.")
        click.echo(f"Looked in {MODELS_DIR} and current working directory.")
        sys.exit(1)
        
    # Resolve parameters
    port = port or config.get("server_port", 8080)
    ctx = ctx or config.get("context_size", DEFAULT_CONTEXT_SIZE)
    threads = threads or config.get("n_threads", 4)
    gpu = gpu if gpu is not None else config.get("gpu_layers", -1)
    
    # 2. Instantiate and start LlamaServer
    from llampaca.engine.server import LlamaServer
    server = LlamaServer(model_path, port=port, context_size=ctx, n_threads=threads, gpu_layers=gpu, draft_model=draft_model, draft_model_gpu_layers=draft_gpu, ssd_offload=ssd_offload)
    
    click.echo(f"Starting llama-server on port {port}...")
    import asyncio
    if not asyncio.run(server.start()):
        click.echo("Failed to start llama-server.")
        sys.exit(1)
        
    click.echo(f"llama-server is up and running on port {port}!")
    
    # 3. Start API HTTP server
    from llampaca.gui.server import start_api_server
    try:
        start_api_server(port=api_port)
    finally:
        click.echo("Shutting down llama-server...")
        server.stop()
        click.echo("Server stopped. Goodbye!")

@main.group()
def history():
    """Gestisci lo storico delle chat."""
    pass

@history.command(name="list") #list history of chats
def list_history():
    #Get all history database
    from llampaca.engine.db import list_conversations
    import asyncio
    history = asyncio.run(list_conversations())
    #Print history
    for item in history:
        click.echo(f"ID: {item['id']}")
        click.echo(f"Title: {item['title']}")
        click.echo(f"Model: {item['model_name']}")
        click.echo(f"Created At: {item['created_at']}")
        click.echo(f"Updated At: {item['updated_at']}")
        click.echo("")

@history.command(name="delete")
@click.argument("conversation_id")
@click.option("-y", "--yes", is_flag=True, help="Ignora la conferma e cancella direttamente.")
def delete_history(conversation_id, yes):
    """Cancella una specifica sessione di chat tramite il suo ID (UUID)."""
    from llampaca.engine.db import get_conversation, delete_conversation
    import asyncio
    
    # Verifica l'esistenza della conversazione prima di tentare la rimozione
    conv = asyncio.run(get_conversation(conversation_id))
    if not conv:
        click.echo(f"Errore: Nessuna conversazione trovata con l'ID '{conversation_id}'.")
        sys.exit(1)
        
    # Chiedi conferma all'utente a meno che non sia stato specificato il flag -y
    if not yes:
        if not click.confirm(f"Sei sicuro di voler eliminare la conversazione '{conv['title']}'?"):
            click.echo("Operazione annullata.")
            return
            
    # Rimuovi la conversazione (il database gestirà in cascata i messaggi correlati)
    asyncio.run(delete_conversation(conversation_id))
    click.echo(f"Conversazione eliminata con successo: '{conv['title']}' (ID: {conversation_id})")

@main.command(name="mcp")
def run_mcp_server():
    """Avvia Llampaca come MCP Server per integrarlo con VSCode o Claude Desktop."""
    from llampaca.engine.mcp_server import main as start_mcp
    start_mcp()

@main.command(name="gui")
@click.argument("model_name", required=False)
@click.option("--port", type=int, help="Port to run llama-server on")
@click.option("--ctx", type=int, help="Context size")
@click.option("--threads", type=int, help="Number of CPU threads to use")
@click.option("--gpu", type=int, help="Number of GPU layers to offload (-1 for auto)")
@click.option("--draft-model", help="Speculative decoding: draft model file name to accelerate generation")
@click.option("--draft-gpu", type=int, help="Number of GPU layers for draft model (-1 for auto)")
@click.option("--ssd-offload", is_flag=True, default=False, help="Enable SSD Offloading via mmap for massive models")
@click.option("-w", "--workspace", help="Cartella di lavoro (workspace) predefinita per la sessione.")
def run_gui(model_name, port, ctx, threads, gpu, draft_model, draft_gpu, ssd_offload, workspace):
    """Avvia la dashboard grafica interattiva di Llampaca ed il server dei modelli."""
    if workspace:
        from llampaca.tools.filesystem import set_workspace_root
        set_workspace_root(workspace)
    import sys
    import subprocess
    
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
        click.echo("L'interfaccia grafica richiede la libreria 'pywebview'.")
        if click.confirm("Desideri installarla automaticamente ora tramite pip?", default=True):
            try:
                click.echo("Installazione in corso...")
                subprocess.run([sys.executable, "-m", "pip", "install", "pywebview"], check=True)
                click.echo("Installazione completata!")
            except Exception as e:
                click.echo(f"Errore durante l'installazione automatica: {e}")
                click.echo("Prova ad installarla manualmente eseguendo: pip install pywebview")
                sys.exit(1)
        else:
            click.echo("Impossibile avviare la GUI senza 'pywebview'.")
            sys.exit(1)

    # 1. Resolve model path
    config = load_config()
    if not model_name:
        model_name = config.get("default_model", "")
        if model_name in MODEL_PRESETS:
            model_name = MODEL_PRESETS[model_name]["file"]
            
    if not model_name:
        click.echo("Error: No default model configured, and no model specified.")
        click.echo("Please download a model using: llampaca models download")
        sys.exit(1)
        
    model_path = MODELS_DIR / model_name
    if not model_path.exists():
        model_path = Path(model_name)
        if not model_path.exists():
            if model_name in MODEL_PRESETS:
                preset_file = MODEL_PRESETS[model_name]["file"]
                model_path = MODELS_DIR / preset_file
                
    if not model_path.exists():
        click.echo(f"Error: Model '{model_name}' could not be resolved to a file path.")
        click.echo(f"Looked in {MODELS_DIR} and current working directory.")
        sys.exit(1)
        
    # Resolve parameters
    port = port or config.get("server_port", 8080)
    ctx = ctx or config.get("context_size", DEFAULT_CONTEXT_SIZE)
    threads = threads or config.get("n_threads", 4)
    gpu = gpu if gpu is not None else config.get("gpu_layers", -1)

    # 2. Start GUI and load model asynchronously
    click.echo(f"Starting Llampaca GUI...")
    click.echo(f"Model will initialize in the background (port {port})...")
    
    from llampaca.gui.agent_manager import agent_manager
    agent_manager.is_restarting = True  # Tell the frontend that we are loading!
    
    def async_startup():
        agent_manager.ready_event.wait()
        from llampaca.gui.restart_bridge import restart_via_agent_manager
        try:
            # We use restart_via_agent_manager to do a clean initialization using the GUI's lifecycle
            success = restart_via_agent_manager(
                agent_manager,
                port=port,
                context_size=ctx,
                n_threads=threads,
                gpu_layers=gpu,
                model_name=model_name,
                draft_model=draft_model,
                draft_model_gpu_layers=draft_gpu,
                ssd_offload=ssd_offload,
                speculative_mode=config.get("speculative_mode", "none")
            )
            if success:
                click.echo(f"llama-server is up and running on port {port}!")
            else:
                click.echo("Failed to start llama-server.")
        except Exception as e:
            click.echo(f"Error during async startup: {e}")
            
    from llampaca.gui.server import start_gui_window
    try:
        start_gui_window(on_ready=async_startup)
    finally:
        click.echo("Shutting down llama-server...")
        from llampaca.engine.state import get_chat_server
        active = get_chat_server()
        if active:
            active.stop()
        click.echo("Goodbye!")

@main.group(name="integrations")
def integrations():
    """Gestisci le integrazioni esterne MCP (Model Context Protocol)."""
    pass

@integrations.command(name="list")
def integrations_list():
    """Elenca le integrazioni MCP esterne configurate."""
    from llampaca.config import load_mcp_config, load_config
    mcp_config = load_mcp_config()
    config = load_config()
    
    servers = mcp_config.get("mcp_servers", {})
    legacy_servers = config.get("mcp_servers", {})
    
    if not servers and not legacy_servers:
        click.echo("Nessuna integrazione MCP configurata.")
        return
        
    if servers:
        click.echo("=== Integrazioni MCP (mcp_config.json) ===")
        for name, cfg in servers.items():
            click.echo(f"  Nome: {name}")
            click.echo(f"    Comando: {cfg.get('command')}")
            click.echo(f"    Argomenti: {cfg.get('args', [])}")
            if cfg.get("env"):
                click.echo(f"    Env: {list(cfg.get('env').keys())}")
            click.echo("")
            
    if legacy_servers:
        click.echo("=== Integrazioni MCP Legacy (config.json) ===")
        for name, cfg in legacy_servers.items():
            click.echo(f"  Nome: {name}")
            click.echo(f"    Comando: {cfg.get('command')}")
            click.echo(f"    Argomenti: {cfg.get('args', [])}")
            if cfg.get("env"):
                click.echo(f"    Env: {list(cfg.get('env').keys())}")
            click.echo("")

@integrations.command(name="browse")
@click.option("--repo", help="Specifica l'URL del repository da navigare.")
def integrations_browse(repo):
    """Sfoglia i server MCP disponibili nei repository ed installali."""
    from llampaca.config import load_mcp_config, save_mcp_config
    import requests
    
    mcp_config = load_mcp_config()
    registries = mcp_config.get("mcp_registries", [])
    
    if not registries:
        click.echo("Errore: nessun repository MCP configurato in mcp_config.json.")
        return
        
    selected_repo = repo
    if not selected_repo:
        if len(registries) == 1:
            selected_repo = registries[0]
        else:
            click.echo("=== Seleziona il Repository MCP ===")
            for idx, r in enumerate(registries, 1):
                click.echo(f"[{idx}] {r}")
            choice = click.prompt("Scegli un repository", type=int)
            if choice < 1 or choice > len(registries):
                click.echo("Scelta non valida.")
                return
            selected_repo = registries[choice - 1]
            
    # Initial search keyword prompt
    click.echo("\n=== Sfoglia Integrazioni MCP ===")
    search_query = click.prompt("Inserisci una parola chiave per cercare (premi Invio per mostrare tutti)", default="", show_default=False).strip()
    if not search_query:
        search_query = None
        
    after_cursor = None
    cursors_history = []  # Stack for back-navigation: list of (after_cursor, search_query)
    
    while True:
        # Build URL
        url = f"{selected_repo}?limit=10"
        if search_query:
            url += f"&query={search_query}"
        if after_cursor:
            url += f"&after={after_cursor}"
            
        click.echo(f"\nConnessione a {selected_repo}...")
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            click.echo(f"Errore durante la connessione al repository: {e}")
            return
            
        servers = data.get("servers", [])
        page_info = data.get("pageInfo", {})
        has_next = page_info.get("hasNextPage", False)
        next_cursor = page_info.get("endCursor")
        
        if not servers:
            click.echo("\nNessun server MCP trovato.")
            if search_query:
                if click.confirm("Vuoi azzerare la ricerca e mostrare tutti?", default=True):
                    search_query = None
                    after_cursor = None
                    cursors_history = []
                    continue
            return
            
        click.echo(f"\n=== Server MCP Disponibili (Ricerca: {search_query or 'Nessuna'}) ===")
        for idx, s in enumerate(servers, 1):
            click.echo(f"[{idx}] {s.get('name')} (da {s.get('namespace', 'sconosciuto')})")
            desc = s.get('description', '')
            if len(desc) > 80:
                desc = desc[:77] + "..."
            click.echo(f"    Descrizione: {desc}")
            repo_url = s.get("repository", {}).get("url")
            if repo_url:
                click.echo(f"    Link: {repo_url}")
        click.echo("-" * 50)
        
        # Build action options
        options = []
        action_map = {}
        
        # Selection options
        for idx in range(1, len(servers) + 1):
            action_map[str(idx)] = ("select", idx - 1)
            
        # Navigation options
        if has_next and next_cursor:
            options.append("[N] Prossima Pagina")
            action_map["n"] = ("next", next_cursor)
            
        if cursors_history:
            options.append("[P] Pagina Precedente")
            action_map["p"] = ("prev", None)
            
        options.append("[S] Nuova Ricerca")
        action_map["s"] = ("search", None)
        
        options.append("[Q] Annulla ed Esci")
        action_map["q"] = ("cancel", None)
        
        click.echo("Opzioni: " + ", ".join(options))
        choice = click.prompt("Scegli un'opzione o inserisci il numero del server", type=str).strip().lower()
        
        if choice not in action_map:
            click.echo("Scelta non valida. Riprova.")
            continue
            
        action, val = action_map[choice]
        
        if action == "cancel":
            click.echo("Operazione annullata.")
            return
            
        elif action == "search":
            new_search = click.prompt("Inserisci la nuova parola chiave da cercare (premi Invio per mostrare tutti)", default="", show_default=False).strip()
            search_query = new_search if new_search else None
            after_cursor = None
            cursors_history = []
            continue
            
        elif action == "next":
            cursors_history.append((after_cursor, search_query))
            after_cursor = val
            continue
            
        elif action == "prev":
            prev_cursor, prev_search = cursors_history.pop()
            after_cursor = prev_cursor
            search_query = prev_search
            continue
            
        elif action == "select":
            server = servers[val]
            name = server.get("slug") or server.get("name").lower().replace(" ", "-")
            description = server.get("description", "")
            repo_url = server.get("repository", {}).get("url")
            schema = server.get("environmentVariablesJsonSchema", {}) or {}
            
            if repo_url:
                if click.confirm(f"\nQuesto server ha un repository Git ({repo_url}).\nVuoi clonarlo ed effettuarne la build automaticamente in locale?", default=True):
                    from llampaca.engine.mcp_installer import run_generic_git_setup
                    run_generic_git_setup(name, repo_url, schema)
                    return

            click.echo(f"\nInstallazione di: {server.get('name')}")
            click.echo(f"Descrizione: {description}")
            if repo_url:
                click.echo(f"GitHub: {repo_url}")
                
            # Propose default npx command
            default_cmd = "npx"
            default_args = ["-y", name]
            
            use_default = click.confirm(f"Usa il comando di esecuzione consigliato: {default_cmd} {' '.join(default_args)}?", default=True)
            if use_default:
                cmd = default_cmd
                args = default_args
            else:
                cmd = click.prompt("Inserisci il comando da eseguire (es. python3, node)", type=str)
                args_str = click.prompt("Inserisci gli argomenti separati da spazi", type=str, default="")
                args = args_str.split() if args_str else []
                
            # Configure environment variables
            env = {}
            schema = server.get("environmentVariablesJsonSchema", {})
            props = schema.get("properties", {})
            required = schema.get("required", [])
            
            if props:
                click.echo("\nConfigurazione Variabili d'Ambiente:")
                for var_name, var_info in props.items():
                    desc = var_info.get("description", "")
                    is_req = var_name in required
                    req_str = " (Obbligatorio)" if is_req else " (Opzionale)"
                    prompt_str = f"  {var_name}{req_str}"
                    if desc:
                        prompt_str += f"\n    Desc: {desc}\n  Valore"
                    val = click.prompt(prompt_str, default="", show_default=False)
                    if val.strip():
                        env[var_name] = val.strip()
                    elif is_req:
                        click.echo(f"Errore: {var_name} è obbligatorio.")
                        return
                        
            # Save to mcp_config.json
            mcp_config["mcp_servers"][name] = {
                "command": cmd,
                "args": args,
                "env": env
            }
            save_mcp_config(mcp_config)
            click.echo(f"\nIntegrazione '{name}' installata con successo in mcp_config.json!")
            return

@integrations.command(name="add")
@click.argument("name")
def integrations_add(name):
    """Aggiungi manualmente un'integrazione MCP."""
    from llampaca.config import load_mcp_config, save_mcp_config
    mcp_config = load_mcp_config()
    
    cmd = click.prompt("Inserisci il comando da eseguire (es. npx, python3)", type=str)
    args_str = click.prompt("Inserisci gli argomenti separati da spazi", type=str, default="")
    args = args_str.split() if args_str else []
    
    env = {}
    while click.confirm("Vuoi aggiungere una variabile d'ambiente?", default=False):
        var_name = click.prompt("Nome variabile (es. API_KEY)", type=str)
        var_val = click.prompt(f"Valore per {var_name}", type=str)
        env[var_name] = var_val
        
    mcp_config["mcp_servers"][name] = {
        "command": cmd,
        "args": args,
        "env": env
    }
    save_mcp_config(mcp_config)
    click.echo(f"Integrazione '{name}' aggiunta con successo in mcp_config.json.")

@integrations.command(name="remove")
@click.argument("name")
def integrations_remove(name):
    """Rimuovi un'integrazione MCP specificando il suo nome."""
    from llampaca.config import load_mcp_config, save_mcp_config
    mcp_config = load_mcp_config()
    
    if name in mcp_config.get("mcp_servers", {}):
        del mcp_config["mcp_servers"][name]
        save_mcp_config(mcp_config)
        click.echo(f"Integrazione '{name}' rimossa con successo.")
    else:
        click.echo(f"Errore: nessuna integrazione trovata con il nome '{name}' in mcp_config.json.")

@integrations.command(name="setup")
@click.argument("name")
@click.option("--repo", required=True, help="L'URL del repository Git da clonare ed installare.")
def integrations_setup(name, repo):
    """Esegui la configurazione guidata generica per un repository Git MCP."""
    from llampaca.engine.mcp_installer import run_generic_git_setup
    run_generic_git_setup(name, repo, {})

@integrations.group(name="repo")
def repo_group():
    """Gestisci i repository (registries) delle integrazioni MCP."""
    pass

@repo_group.command(name="list")
def repo_list():
    """Elenca i repository MCP configurati."""
    from llampaca.config import load_mcp_config
    mcp_config = load_mcp_config()
    registries = mcp_config.get("mcp_registries", [])
    
    if not registries:
        click.echo("Nessun repository MCP configurato.")
    else:
        click.echo("=== Repository MCP Configurati ===")
        for idx, r in enumerate(registries, 1):
            click.echo(f" [{idx}] {r}")

@repo_group.command(name="add")
@click.argument("url")
def repo_add(url):
    """Aggiungi un URL di un nuovo repository MCP."""
    from llampaca.config import load_mcp_config, save_mcp_config
    mcp_config = load_mcp_config()
    registries = mcp_config.setdefault("mcp_registries", [])
    
    if url in registries:
        click.echo(f"Il repository '{url}' è già configurato.")
    else:
        registries.append(url)
        save_mcp_config(mcp_config)
        click.echo(f"Repository '{url}' aggiunto con successo.")

@repo_group.command(name="remove")
@click.argument("url")
def repo_remove(url):
    """Rimuovi un repository MCP esistente tramite il suo URL."""
    from llampaca.config import load_mcp_config, save_mcp_config
    mcp_config = load_mcp_config()
    registries = mcp_config.get("mcp_registries", [])
    
    if url in registries:
        registries.remove(url)
        save_mcp_config(mcp_config)
        click.echo(f"Repository '{url}' rimosso con successo.")
    else:
        click.echo(f"Errore: repository '{url}' non trovato in mcp_config.json.")

@main.command(name="generate-image")
@click.argument("prompt")
@click.option("--output", help="Cartella o percorso di destinazione personalizzato dell'immagine")
@click.option("--quality", type=click.Choice(["fast", "high"]), default="fast", help="Preset qualità/velocità: 'fast' (flash) o 'high' (alta qualità)")
def generate_image_cmd(prompt, output, quality):
    """Genera un'immagine locale da un prompt usando stable-diffusion.cpp (sd.cpp)."""
    from llampaca.tools.image import generate_image
    click.echo(f"Generazione immagine in corso per il prompt: '{prompt}'...")
    res = generate_image(prompt=prompt, output_directory=output, quality=quality)
    click.echo(res)




@main.group()
def config():
    """Manage application configuration."""
    pass

@config.command(name="set-kv-cache")
@click.option("--k", default="q8_0", help="Key cache quant: f16, q8_0, q5_0, q4_0")
@click.option("--v", default="q8_0", help="Value cache quant: f16, q8_0, q5_0, q4_0")
def set_kv_cache(k, v):
    """Set the Key-Value cache quantization."""
    cfg = load_config()
    cfg["kv_cache_quant_k"] = k
    cfg["kv_cache_quant_v"] = v
    save_config(cfg)
    click.echo(f"KV cache set to K={k}, V={v}. Restart the server to apply.")


if __name__ == "__main__":
    main()
