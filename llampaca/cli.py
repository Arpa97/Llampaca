import os
import sys
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

@main.command()
@click.argument("model_name", required=False)
@click.option("--port", type=int, help="Port to run llama-server on")
@click.option("--ctx", type=int, help="Context size")
@click.option("--threads", type=int, help="Number of CPU threads to use")
@click.option("--gpu", type=int, help="Number of GPU layers to offload (-1 for auto)")
def run(model_name, port, ctx, threads, gpu):
    """Launch llama-server and open an interactive chat session."""
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
        
    # 2. Start server
    server_port = port or config.get("server_port", 8080)
    
    server = LlamaServer(
        model_path=model_path,
        port=server_port,
        context_size=ctx,
        n_threads=threads,
        gpu_layers=gpu
    )
    
    if not server.start():
        sys.exit(1)
        
    # 3. Connect client and start interactive loop
    client = LlamaClient(port=server.port)
    
    click.echo("\n" + "=" * 50)
    click.echo(f" Interactive Chat Session with {model_path.name}")
    click.echo(" Type '/exit' or '/quit' to close the session.")
    click.echo("=" * 50 + "\n")
    
    messages = [
        {"role": "system", "content": "You are Llampaca, a helpful, friendly local AI personal assistant."}
    ]
    
    try:
        while True:
            user_input = click.prompt("You", prompt_suffix=" > ")
            
            # Command handling
            if user_input.strip().lower() in ["/exit", "/quit"]:
                break
                
            if not user_input.strip():
                continue
                
            messages.append({"role": "user", "content": user_input})
            
            # Print streaming response
            click.echo("Llampaca > ", nl=False)
            response_content = ""
            for chunk in client.chat_stream(messages, model=model_path.name):
                click.echo(chunk, nl=False)
                response_content += chunk
            click.echo() # Newline at the end
            
            messages.append({"role": "assistant", "content": response_content})
            
    except (KeyboardInterrupt, EOFError):
        click.echo("\nSession interrupted.")
    finally:
        # 4. Cleanup
        server.stop()
        click.echo("Goodbye!")

if __name__ == "__main__":
    main()
