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
from llampaca.agent.loop import DEFAULT_SYSTEM_PROMPT
from llampaca.tools import build_default_registry
from llampaca import wiki

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

async def async_run_chat(model_path, port, ctx, threads, gpu, no_tools, no_think=False):
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
        gpu_layers=gpu,
        no_think=no_think
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

    mcp_manager = None
    if registry is not None:
        from llampaca.config import load_mcp_config
        mcp_config = load_mcp_config()
        mcp_servers_config = mcp_config.get("mcp_servers", {})
        legacy_mcp = config.get("mcp_servers", {})
        if legacy_mcp:
            mcp_servers_config = {**legacy_mcp, **mcp_servers_config}

        if mcp_servers_config:
            from llampaca.engine.mcp_client import McpClientManager
            mcp_manager = McpClientManager(mcp_servers_config)
            await mcp_manager.start(registry)

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

    # The wiki index (the model's persistent memory: page names +
    # descriptions) rides in the system prompt so the model knows what it
    # remembers without a tool call. Static for the session by design: a
    # page written mid-session is confirmed by its tool result, and
    # rebuilding the prompt on every write would invalidate llama-server's
    # prefix cache (same trade-off as refresh_tools). Skipped with
    # --no-tools: the index describes tools the model would not have.
    system_prompt = None
    if registry is not None:
        system_prompt = DEFAULT_SYSTEM_PROMPT + "\n\n" + wiki.render_index()

    agent = Agent(
        client=client,
        registry=registry,
        confirm=confirm_action,
        system_prompt=system_prompt,
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
    if registry:
        click.echo(" Type '/remember <fact>' to store a fact in the wiki "
                   f"({wiki.WIKI_DIR}).")
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

    # Deferred summarization (see Agent.summarize_pending): when a turn
    # trims old history, the summary of the dropped turns is generated in
    # the BACKGROUND after the answer — while the user is typing — instead
    # of blocking the turn with an extra LLM generation before it.
    summary_task = None

    async def flush_summary() -> None:
        """Generate the pending summary and persist it to the database."""
        data = await agent.summarize_pending()
        if data:
            await update_conversation_summary(
                active_conversation_id,
                data["summary"],
                data["last_summarized_message_id"],
            )

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

            # --- /remember <fact>: store a durable fact in the wiki ------
            # Not written to disk directly: the fact is forwarded to the
            # model as a normal turn, so IT picks (or creates) the right
            # wiki page, and the write still passes through the standard
            # update_wiki_page confirmation prompt. Deterministic trigger,
            # model-side organization.
            if user_input.strip().lower().startswith("/remember"):
                fact = user_input.strip()[len("/remember"):].strip()
                if not fact:
                    click.echo("Usage: /remember <fact to store in the wiki>")
                    continue
                if no_tools:
                    click.echo(click.style(
                        "  [remember error] Tools are disabled (--no-tools), "
                        "so the model cannot write to the wiki. Relaunch "
                        "without --no-tools.", fg="red",
                    ))
                    continue
                # The fact comes FIRST so the auto-title snippet below picks
                # it up instead of the bracketed instruction.
                user_input = (
                    f"{fact}\n\n"
                    "[The user asked to remember the fact above permanently. "
                    "Store it in your wiki with update_wiki_page, copying the "
                    "fact FAITHFULLY — the page content must state exactly "
                    "the fact above, never something invented. Add it to the "
                    "existing page it fits best (read the page first and "
                    "keep its still-valid content), or create a new page if "
                    "none fits. Then confirm in one short line where you "
                    "stored it.]"
                )
                click.echo(click.style(
                    "  [remember] asking the model to store this in the wiki "
                    "(you will be asked to confirm the write)...", fg="cyan",
                ))

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

            # A summary flush from the previous turn may still be running;
            # let it finish before talking to the server again. llama-server
            # would serialize the two requests anyway, so this costs nothing
            # extra — it just keeps turns strictly ordered.
            if summary_task is not None:
                try:
                    await summary_task
                except Exception:
                    pass  # a failed flush keeps its turns queued for retry
                summary_task = None

            # Add message to local context and persist to SQLite
            user_msg_id = await add_message(
                active_conversation_id, "user", user_input,
                title_snippet=question_text,
            )

            # Print streaming response
            click.echo("Llampaca > ", nl=False)
            response_content = ""
            # Whether the stream is currently inside a thinking block: used
            # to (a) print the "[thinking]" header exactly once per block
            # and (b) close the dim block with a newline when the model
            # moves on to the answer or to a tool call. A turn can contain
            # several blocks (reasoning models think again before each tool
            # round-trip), so the flag is reset on every transition.
            in_reasoning = False
            # Wall-clock duration of the whole turn — model thinking +
            # generation + tool executions — i.e. the wait the user actually
            # experiences. monotonic() because it can't jump if the system
            # clock changes mid-turn.
            turn_started = time.monotonic()
            # Generation counters for the whole turn, summed across the
            # per-request "stats" events (a turn with tool round-trips is
            # several model requests). tokens/ms ratio — not tokens/wall-
            # clock — so tool execution time never dilutes the reported
            # generation speed.
            gen_tokens = 0
            gen_ms = 0.0
            try:
                async for kind, data in agent.send(user_input, message_id=user_msg_id):
                    # Any non-reasoning event ends the current dim thinking
                    # block: close it visually before rendering the event.
                    if in_reasoning and kind != "reasoning":
                        click.echo()
                        in_reasoning = False
                    if kind == "text":
                        click.echo(data, nl=False)
                        response_content += data
                    elif kind == "reasoning":
                        # The model's thinking, streamed dim so it reads as
                        # process, not as the answer. Purely visual: it is
                        # never part of response_content, so it never
                        # reaches the database or the conversation history.
                        if not in_reasoning:
                            click.echo(click.style("\n  [thinking] ", dim=True), nl=False)
                            in_reasoning = True
                        click.echo(click.style(data, dim=True), nl=False)
                    elif kind == "tool_start":
                        # The tool's name is known but its arguments are
                        # still streaming: show *something* immediately —
                        # the full call line follows in "tool_call" once
                        # the arguments are complete.
                        click.echo(click.style(
                            f"\n  [tool] calling {data['name']}...", fg="cyan", dim=True
                        ), nl=False)
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
                    elif kind == "stats":
                        # Silent accumulation: rendered once in the footer.
                        gen_tokens += data.get("predicted_n") or 0
                        gen_ms += data.get("predicted_ms") or 0.0
                    elif kind == "warning":
                        click.echo(click.style(f"\n  [warning] {data}", fg="yellow"))
                    elif kind == "error":
                        # Recoverable turn failure: show it in red but keep the session open
                        click.echo(click.style(f"\n  [error] {data}", fg="red"))
            except Exception as e:
                click.echo(click.style(f"\n  [error] Unexpected error: {e}", fg="red"))
            if in_reasoning:
                # Stream ended while still thinking (e.g. an error cut it
                # short): close the dim block so the chrome below starts
                # on its own line.
                click.echo()
            click.echo() # Newline at the end
            turn_seconds = time.monotonic() - turn_started

            # Turn footer: how much of the context the history now occupies,
            # how long the whole response took, and the generation speed
            # measured by the server itself (summed across the turn's
            # requests). The context estimate is heuristic (chars/4), hence
            # the "~"; the speed part is omitted when the server sent no
            # timings (older llama.cpp builds). Rendered dim so it reads as
            # chrome, not as part of the model's answer.
            used_tokens, total_tokens = agent.context_usage()
            used_percent = min(100, (used_tokens * 100 // total_tokens)) if total_tokens else 0
            speed = ""
            if gen_tokens and gen_ms > 0:
                speed = f" | {gen_tokens} tok @ {gen_tokens / (gen_ms / 1000):.1f} tok/s"
            click.echo(click.style(
                f"  [context: ~{used_percent}% used | took {turn_seconds:.1f}s{speed}]", dim=True
            ))

            if response_content:
                assistant_msg_id = await add_message(active_conversation_id, "assistant", response_content)
                # Assign the database ID to the assistant's message in memory
                if agent.messages and agent.messages[-1]["role"] == "assistant":
                    agent.messages[-1]["id"] = assistant_msg_id

            # If this turn trimmed history, fold the dropped turns into the
            # summary now, in the background: the generation runs while the
            # user reads the answer and types the next message.
            if agent.has_pending_summary:
                summary_task = asyncio.create_task(flush_summary())

    except (KeyboardInterrupt, EOFError):
        click.echo("\nSession interrupted.")
    finally:
        # Let an in-flight summary flush finish before killing the server,
        # so a summary already being generated is persisted rather than
        # lost. No NEW summarization is started here: un-summarized turns
        # are still in the database and will simply be reloaded (and
        # re-trimmed) on resume, so nothing is ever lost by skipping it.
        if summary_task is not None and not summary_task.done():
            try:
                await summary_task
            except Exception:
                pass

        # Clear active conversation status from config on exit
        config = load_config()
        config["active_conversation_id"] = ""
        save_config(config)

        if mcp_manager:
            await mcp_manager.stop()

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
@click.option("--no-think", is_flag=True, default=False,
              help="Disable the model's hidden 'thinking' phase (reasoning models like Qwen3): faster responses, slightly lower quality on complex tasks")
def run(model_name, port, ctx, threads, gpu, no_tools, no_think):
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
    asyncio.run(async_run_chat(model_path, port, ctx, threads, gpu, no_tools, no_think))

@main.command(name="serve")
@click.argument("model_name", required=False)
@click.option("--port", type=int, help="Port to run llama-server on")
@click.option("--api-port", type=int, default=8090, help="Port to run Llampaca API server on")
@click.option("--ctx", type=int, help="Context size")
@click.option("--threads", type=int, help="Number of CPU threads to use")
@click.option("--gpu", type=int, help="Number of GPU layers to offload (-1 for auto)")
def serve(model_name, port, api_port, ctx, threads, gpu):
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
    server = LlamaServer(model_path, port=port, context_size=ctx, n_threads=threads, gpu_layers=gpu)
    
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
def run_gui(model_name, port, ctx, threads, gpu):
    """Avvia la dashboard grafica interattiva di Llampaca ed il server dei modelli."""
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

    # 2. Instantiate and start LlamaServer
    from llampaca.engine.server import LlamaServer
    server = LlamaServer(model_path, port=port, context_size=ctx, n_threads=threads, gpu_layers=gpu)
    
    click.echo(f"Starting llama-server on port {port}...")
    import asyncio
    if not asyncio.run(server.start()):
        click.echo("Failed to start llama-server.")
        sys.exit(1)
        
    click.echo(f"llama-server is up and running on port {port}!")
            
    from llampaca.gui.server import start_gui_window
    try:
        start_gui_window()
    finally:
        click.echo("Shutting down llama-server...")
        from llampaca.engine.server import get_active_server
        active = get_active_server()
        if active:
            active.stop()
        else:
            server.stop()
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

if __name__ == "__main__":
    main()
