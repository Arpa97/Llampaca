import os
import sys
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
    ensure_dirs
)
from llampaca.engine.downloader import download_llama_binaries, download_hf_model
from llampaca.engine.server import LlamaServer, is_port_in_use
from llampaca.engine.client import LlamaClient
from llampaca.agent import Agent
from llampaca.tools import build_default_registry

def format_size(bytes_size: int) -> str:
    """Format bytes into human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} PB"

@click.group()
def main():
    """Llampaca - Run your local AI agents and assistants for free."""
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
        preset_table.append([name, preset["repo"], preset["file"], is_downloaded, preset["description"]])
        
    click.echo(tabulate(preset_table, headers=["Preset Name", "HF Repo", "Filename", "Status", "Description"], tablefmt="simple"))

@models.command(name="download")
@click.option("--preset", type=click.Choice(list(MODEL_PRESETS.keys())), help="Download a recommended model preset")
@click.option("--repo", help="Hugging Face repository ID (e.g. Qwen/Qwen3.5-4B-Instruct-GGUF)")
@click.option("--file", help="GGUF filename in the repository")
def download_model(preset, repo, file):
    """Download a GGUF model from Hugging Face."""
    if preset:
        preset_info = MODEL_PRESETS[preset]
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
            click.echo(f"[{idx + 1}] {name} ({MODEL_PRESETS[name]['description']})")
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
        
        # Set downloaded model as default
        config = load_config()
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

async def async_run_chat(model_path, port, ctx, threads, gpu, no_tools):
    config = load_config()
    
    # 2. Check and choose conversation session
    from llampaca.engine.db import (
        list_conversations,
        create_conversation,
        add_message,
        get_conversation
    )
    
    # Retrieve existing conversations to see if we can offer a resume option
    existing_conversations = await list_conversations()
    active_conversation_id = None
    messages = []
    
    if not existing_conversations:
        # No history found - start a brand new conversation session
        active_conversation_id = await create_conversation(model_name=model_path.name)
        system_content = "You are Llampaca, a helpful local AI personal assistant."
        await add_message(active_conversation_id, "system", system_content)
        messages.append({"role": "system", "content": system_content})
        click.echo("No previous chat history found. Started a new conversation session.")
    else:
        # Present selection menu
        click.echo("=== Select Conversation Session ===")
        click.echo("[1] Start a new conversation")
        
        # Display the most recent 9 conversations
        display_limit = 9
        recent_convs = existing_conversations[:display_limit]
        for idx, conv in enumerate(recent_convs):
            click.echo(f"[{idx + 2}] Resume: \"{conv['title']}\" (Model: {conv['model_name']}, Updated: {conv['updated_at']})")
            
        choice = click.prompt("Choose option (default: 1)", default=1, type=int)
        
        if choice == 1:
            # User wants to start a new conversation
            active_conversation_id = await create_conversation(model_name=model_path.name)
            system_content = "You are Llampaca, a helpful local AI personal assistant."
            await add_message(active_conversation_id, "system", system_content)
            messages.append({"role": "system", "content": system_content})
            click.echo("Started a new conversation session.")
        elif 2 <= choice <= len(recent_convs) + 1:
            # User wants to resume a previous conversation
            selected_conv = recent_convs[choice - 2]
            active_conversation_id = selected_conv["id"]
            
            # Load metadata and messages from the DB
            conv_data = await get_conversation(active_conversation_id)
            
            # If the stored model file exists, use it instead of the default/parameter model
            stored_model_path = MODELS_DIR / conv_data["model_name"]
            if stored_model_path.exists():
                model_path = stored_model_path
            else:
                # If model file is missing, warn the user and proceed with the current active model
                click.echo(f"Warning: Stored model '{conv_data['model_name']}' not found in {MODELS_DIR}.")
                click.echo(f"Falling back to current model '{model_path.name}'.")
                
            # Reload messages into active memory
            for msg in conv_data["messages"]:
                messages.append({"role": msg["role"], "content": msg["content"]})
                
            click.echo(f"\nResumed conversation: \"{conv_data['title']}\"")
            click.echo("--- Recent History ---")
            # Print the last 6 messages to provide immediate chat context
            recent_msgs = conv_data["messages"][-6:]
            for msg in recent_msgs:
                if msg["role"] == "user":
                    click.echo(f"You > {msg['content']}")
                elif msg["role"] == "assistant":
                    click.echo(f"Llampaca > {msg['content']}")
            click.echo("----------------------")
        else:
            # Fallback for invalid options
            click.echo("Invalid choice. Starting a new conversation.")
            active_conversation_id = await create_conversation(model_name=model_path.name)
            system_content = "You are Llampaca, a helpful local AI personal assistant."
            await add_message(active_conversation_id, "system", system_content)
            messages.append({"role": "system", "content": system_content})

    # Save active_conversation_id to application config to make it accessible to other components
    config = load_config()
    config["active_conversation_id"] = active_conversation_id
    save_config(config)

    # 3. Start server
    server_port = port or config.get("server_port", 8080)
    
    server = LlamaServer(
        model_path=model_path,
        port=server_port,
        context_size=ctx,
        n_threads=threads,
        gpu_layers=gpu
    )
    
    if not await server.start():
        # Clean up active session on startup failure
        config = load_config()
        config["active_conversation_id"] = ""
        save_config(config)
        sys.exit(1)
        
    # 4. Connect client and build the agent
    client = LlamaClient(port=server.port)

    # Tools are enabled by default; --no-tools falls back to plain chat.
    # The registry sandbox root is the directory the user launched us from.
    #
    # The cap on a single tool result scales with the context window instead
    # of being a fixed number: numerically, ctx-in-tokens == chars works out
    # to ~25% of the window (1 token ≈ 4 chars), so one big read_file can
    # never swamp the whole context, whatever --ctx the user picked.
    # server.context_size (not the raw --ctx flag, which may be None) is the
    # resolved value actually passed to llama-server.
    registry = None if no_tools else build_default_registry(
        max_result_chars=server.context_size
    )

    # The agent core asks for confirmation through this callback, and we render its events
    def confirm_action(prompt_text: str) -> bool:
        click.echo()  # break out of any partial output line
        return click.confirm(click.style(f"  {prompt_text}", fg="yellow") + "\n  Allow?")

    agent = Agent(
        client=client,
        registry=registry,
        confirm=confirm_action,
        model=model_path.name,
        # Lets the agent trim old history before the prompt outgrows the
        # window, and powers the context-usage indicator after each turn.
        context_size=server.context_size,
    )
    
    if messages:
        # Keep the system prompt the Agent built in __init__: it carries the
        # current date and — in prompt-based mode (e.g. Gemma) — the tool
        # definitions themselves. Replacing it with the generic system message
        # stored in the DB would strip those instructions and silently break
        # prompt-mode tool calling, so only the actual conversation turns
        # (user/assistant) are restored from the database.
        agent.messages.extend(m for m in messages if m.get("role") != "system")

    click.echo("\n" + "=" * 50)
    click.echo(f" Interactive Agent Session with {model_path.name}")
    if registry:
        # "native" = the model's chat template handles tools structurally;
        # "prompt-based" = definitions injected in the system prompt (models
        # like Gemma whose template has no tool support)
        mode = "native" if agent.native_tools else "prompt-based"
        click.echo(f" Tools enabled ({mode}): {', '.join(registry.names())}")
        click.echo(f" Workspace: {Path.cwd()}")
    else:
        click.echo(" Tools disabled (plain chat mode)")
    click.echo(" Type '/exit' or '/quit' to close the session.")
    click.echo("=" * 50 + "\n")
    
    try:
        while True:
            user_input = click.prompt("You", prompt_suffix=" > ")
            
            # Command handling
            if user_input.strip().lower() in ["/exit", "/quit"]:
                break
                
            if not user_input.strip():
                continue
                
            # Add message to local context and persist to SQLite
            await add_message(active_conversation_id, "user", user_input)
            
            # Print streaming response
            click.echo("Llampaca > ", nl=False)
            response_content = ""
            try:
                async for kind, data in agent.send(user_input):
                    if kind == "text":
                        click.echo(data, nl=False)
                        response_content += data
                    elif kind == "tool_call":
                        click.echo(click.style(
                            f"\n  [tool] {data['name']}({data['arguments']})", fg="cyan"
                        ))
                    elif kind == "tool_result":
                        # Show a one-line preview; the full result goes to the model
                        preview = data["result"].replace("\n", " ")
                        if len(preview) > 120:
                            preview = preview[:120] + "..."
                        click.echo(click.style(f"  [result] {preview}", fg="green"))
                    elif kind == "warning":
                        click.echo(click.style(f"\n  [warning] {data}", fg="yellow"))
                    elif kind == "error":
                        # Recoverable turn failure: show it in red but keep the session open
                        click.echo(click.style(f"\n  [error] {data}", fg="red"))
            except Exception as e:
                click.echo(click.style(f"\n  [error] Unexpected error: {e}", fg="red"))
            click.echo() # Newline at the end

            # Context-usage indicator: how much of the model's window is
            # still free after this turn. The estimate is heuristic
            # (chars/4), hence the "~". Rendered dim so it reads as chrome,
            # not as part of the model's answer.
            used_tokens, total_tokens = agent.context_usage()
            free_percent = max(0, 100 - (used_tokens * 100 // total_tokens))
            click.echo(click.style(
                f"  [context: ~{free_percent}% free]", dim=True
            ))

            if response_content:
                await add_message(active_conversation_id, "assistant", response_content)
            
    except (KeyboardInterrupt, EOFError):
        click.echo("\nSession interrupted.")
    finally:
        # Clear active conversation status from config on exit
        config = load_config()
        config["active_conversation_id"] = ""
        save_config(config)
        
        # Shutdown server
        server.stop()
        click.echo("Goodbye!")

@main.command()
@click.argument("model_name", required=False)
@click.option("--port", type=int, help="Port to run llama-server on")
@click.option("--ctx", type=int, help="Context size")
@click.option("--threads", type=int, help="Number of CPU threads to use")
@click.option("--gpu", type=int, help="Number of GPU layers to offload (-1 for auto)")
@click.option("--no-tools", is_flag=True, default=False, help="Disable agent tools (plain chat mode)")
def run(model_name, port, ctx, threads, gpu, no_tools):
    """Launch llama-server and open an interactive agent session."""
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
    asyncio.run(async_run_chat(model_path, port, ctx, threads, gpu, no_tools))

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

if __name__ == "__main__":
    main()
