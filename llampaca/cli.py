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

async def async_run_chat(model_path, port, ctx, threads, gpu, no_tools):
    config = load_config()
    
    # 2. Check and choose conversation session
    from llampaca.engine.db import (
        list_conversations,
        create_conversation,
        add_message,
        get_conversation,
        update_conversation_summary
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
                
            # Reload messages into active memory, retaining the SQLite ID
            for msg in conv_data["messages"]:
                messages.append({"role": msg["role"], "content": msg["content"], "id": msg.get("id")})
                
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

    # Retrieve summary metadata if we resumed a conversation
    summary = None
    last_summarized_id = None
    if "conv_data" in locals() and conv_data:
        summary = conv_data.get("summary")
        last_summarized_id = conv_data.get("last_summarized_message_id")

    agent = Agent(
        client=client,
        registry=registry,
        confirm=confirm_action,
        model=model_path.name,
        # Lets the agent trim old history before the prompt outgrows the
        # window, and powers the context-usage indicator after each turn.
        context_size=server.context_size,
        summary=summary,
    )
    
    if messages:
        # Load only the active (un-summarized) messages into the agent's memory
        active_messages = []
        for m in messages:
            if m.get("role") == "system":
                continue
            if last_summarized_id is not None and m.get("id") is not None and m["id"] <= last_summarized_id:
                continue
            active_messages.append(m)
        agent.messages.extend(active_messages)

    # --- RAG plumbing for large attachments ------------------------------
    # The embedding server is LAZY: EmbeddingService() only resolves the
    # configured model; nothing starts until activate_document_search()
    # runs — at the first over-budget /attach, or right below when the
    # resumed conversation already has indexed documents.
    from llampaca.engine.embedding import EmbeddingService, EmbeddingUnavailable
    from llampaca.engine.db import list_documents
    from llampaca.tools import register_document_tools

    embedding_service = EmbeddingService()
    search_tool_active = False

    async def activate_document_search() -> None:
        """
        Bring the document-search machinery up: start the embedding server
        (lazy, idempotent) and register the search_documents tool exactly
        once, refreshing the agent so prompt-mode models get the updated
        system prompt. Must run from this async context because the sync
        tool cannot start the async server itself.
        """
        nonlocal search_tool_active
        await embedding_service.ensure_started()
        if search_tool_active or registry is None:
            return
        register_document_tools(
            registry,
            embed_query=embedding_service.query_embedder(),
            conversation_id=active_conversation_id,
        )
        agent.refresh_tools()
        search_tool_active = True

    # Resuming a conversation that already has indexed documents: activate
    # the search tool now, so the model can keep answering questions about
    # them. (This is the one case where the embedding server starts at
    # session open — the indexed documents signal the intent to use it.)
    if registry:
        indexed_docs = await list_documents(active_conversation_id)
        if indexed_docs:
            mismatched = [
                d for d in indexed_docs
                if d["embedder_name"] != embedding_service.model_file
            ]
            if mismatched:
                click.echo(click.style(
                    "  [warning] Some indexed documents were built with a "
                    f"different embedding model ({mismatched[0]['embedder_name']}); "
                    "their search results will be unavailable until you "
                    "re-attach them.", fg="yellow",
                ))
            try:
                await activate_document_search()
                doc_names = ", ".join(d["filename"] for d in indexed_docs)
                click.echo(f"Indexed documents available for search: {doc_names}")
            except EmbeddingUnavailable as e:
                click.echo(click.style(
                    f"  [warning] Document search unavailable: {e}", fg="yellow"
                ))

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
    click.echo(" Type '/attach <file>' to attach a document (PDF, Word, text).")
    click.echo(" Type '/exit' or '/quit' to close the session.")
    click.echo("=" * 50 + "\n")

    # Attachment support for /attach. This import is cheap (pypdf and
    # python-docx are lazily imported inside the extractors, not here).
    from llampaca.attachments import (
        AttachmentError,
        attachment_token_budget,
        build_attachment_block,
        estimate_tokens,
        extract_text,
    )

    # Attachments staged by /attach and not yet sent. Each entry is
    # (filename, extracted_text). They are merged into the *next* user
    # message instead of being appended to the history on their own:
    # Gemma-style chat templates reject two consecutive "user" messages
    # ("Conversation roles must alternate..."), so a standalone attachment
    # message followed by the user's question would fail server-side in
    # prompt mode. One combined user turn works with every template.
    pending_attachments = []
    # Notes about documents that were INDEXED instead of injected (too
    # large for the budget). Merged into the next user message the same
    # way, but they carry only a pointer ("use search_documents"), never
    # the document text — that is the whole point of indexing.
    pending_index_notes = []

    try:
        while True:
            user_input = click.prompt("You", prompt_suffix=" > ")

            # Command handling
            if user_input.strip().lower() in ["/exit", "/quit"]:
                break

            if not user_input.strip():
                continue

            # --- /attach <path>: stage a document for the next message ---
            if user_input.strip().lower().startswith("/attach"):
                raw_arg = user_input.strip()[len("/attach"):].strip()
                if not raw_arg:
                    click.echo("Usage: /attach <path-to-file>  (PDF, .docx, or plain text)")
                    continue

                # Expand ~ and resolve relative paths against the launch
                # directory. No workspace sandbox here on purpose: the path
                # is typed by the user, not chosen by the model.
                attach_path = Path(raw_arg).expanduser()
                if not attach_path.is_absolute():
                    attach_path = Path.cwd() / attach_path

                try:
                    text = extract_text(attach_path)
                except AttachmentError as e:
                    click.echo(click.style(f"  [attach error] {e}", fg="red"))
                    continue
                except Exception as e:
                    # Defensive net: a bug in an extractor (or in one of its
                    # libraries) must cost the user one red line, never the
                    # whole session — /attach is chrome, not the chat itself.
                    click.echo(click.style(
                        f"  [attach error] Unexpected error while extracting "
                        f"'{attach_path.name}': {e}", fg="red"
                    ))
                    continue

                # The size budget decides the strategy: within it, the text
                # is injected directly (phase 1); beyond it, the document
                # gets INDEXED and the model searches it instead (RAG). The
                # check is on the *total* staged content: two attachments
                # that individually fit but jointly overflow would starve
                # the conversation just the same.
                budget = attachment_token_budget(server.context_size)
                staged_tokens = sum(estimate_tokens(t) for _, t in pending_attachments)
                new_tokens = estimate_tokens(text)
                if staged_tokens + new_tokens > budget:
                    if no_tools:
                        # Indexing is useless without the search tool: the
                        # model could never read the document back.
                        click.echo(click.style(
                            f"  [attach error] '{attach_path.name}' is too large "
                            f"(~{new_tokens} tokens vs a budget of ~{budget}) and "
                            "tools are disabled (--no-tools), so it cannot be "
                            "indexed for search either. Attach a smaller file or "
                            "relaunch without --no-tools.", fg="red",
                        ))
                        continue

                    click.echo(click.style(
                        f"  [indexing] {attach_path.name}: ~{new_tokens} tokens — "
                        f"too large for direct injection (budget ~{budget}), "
                        "indexing for search instead...", fg="cyan",
                    ))
                    try:
                        # Order matters: activate first (starts the embedding
                        # server lazily and registers the tool), then index.
                        await activate_document_search()
                        info = await embedding_service.index_document(
                            active_conversation_id, attach_path.name, text
                        )
                    except EmbeddingUnavailable as e:
                        click.echo(click.style(f"  [attach error] {e}", fg="red"))
                        continue
                    except Exception as e:
                        # Same defensive net as extraction: an indexing bug
                        # costs one red line, never the session.
                        click.echo(click.style(
                            f"  [attach error] Unexpected error while indexing "
                            f"'{attach_path.name}': {e}", fg="red",
                        ))
                        continue

                    pages_part = f", {info['pages']} pages" if info["pages"] else ""
                    # The model must learn the document exists and how to
                    # reach it; the note rides along with the next message
                    # (same merge rule as direct attachments).
                    pending_index_notes.append(
                        f"[Attached and indexed: {attach_path.name} "
                        f"({info['chunks']} searchable passages{pages_part}). "
                        "This document is NOT in your context: use the "
                        "search_documents tool to read passages from it.]"
                    )
                    click.echo(click.style(
                        f"  [indexed] {attach_path.name}: {info['chunks']} chunks"
                        f"{pages_part}. The model can now search inside it; a "
                        "note will be sent with your next message.", fg="cyan",
                    ))
                    continue

                pending_attachments.append((attach_path.name, text))
                used_percent = (staged_tokens + new_tokens) * 100 // budget
                click.echo(click.style(
                    f"  [attached] {attach_path.name} (~{new_tokens} tokens, "
                    f"attachment budget {used_percent}% used). "
                    "It will be sent together with your next message.",
                    fg="cyan",
                ))
                continue

            # The user's own words, captured BEFORE any merge below: used
            # for auto-titling, so a conversation is never titled
            # "[Attached file: ..." after a first message with attachments.
            question_text = user_input

            # Merge any staged attachments and index notes into this
            # message: document(s)/note(s) first, the user's request last,
            # as one single user turn (see pending_attachments above).
            if pending_attachments or pending_index_notes:
                parts = [
                    build_attachment_block(name, text)
                    for name, text in pending_attachments
                ]
                parts.extend(pending_index_notes)
                names = ", ".join(name for name, _ in pending_attachments)
                if pending_index_notes:
                    suffix = f"{len(pending_index_notes)} indexed document note(s)"
                    names = f"{names}, {suffix}" if names else suffix
                user_input = "\n\n".join(parts + [user_input])
                pending_attachments = []
                pending_index_notes = []
                click.echo(click.style(f"  [sending with attachments: {names}]", dim=True))

            # Add message to local context and persist to SQLite
            user_msg_id = await add_message(
                active_conversation_id, "user", user_input,
                title_snippet=question_text,
            )
            
            # Print streaming response
            click.echo("Llampaca > ", nl=False)
            response_content = ""
            try:
                async for kind, data in agent.send(user_input, message_id=user_msg_id):
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
                    elif kind == "summary_updated":
                        # Persist the updated summary to SQLite
                        await update_conversation_summary(
                            active_conversation_id,
                            data["summary"],
                            data["last_summarized_message_id"]
                        )
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
                assistant_msg_id = await add_message(active_conversation_id, "assistant", response_content)
                # Assign the database ID to the assistant's message in memory
                if agent.messages and agent.messages[-1]["role"] == "assistant":
                    agent.messages[-1]["id"] = assistant_msg_id
            
    except (KeyboardInterrupt, EOFError):
        click.echo("\nSession interrupted.")
    finally:
        # Clear active conversation status from config on exit
        config = load_config()
        config["active_conversation_id"] = ""
        save_config(config)
        
        # Shutdown servers: the chat server and, if the session ever
        # indexed or searched documents, the embedding server too
        # (stop() is a no-op when it never started).
        server.stop()
        embedding_service.stop()
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
