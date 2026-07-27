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
from llampaca import skills

from llampaca.cli_utils import format_size
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
        # The Markdown skills index (slug + short description of each
        # installed skill) also rides in the system prompt so the model
        # knows which skills exist and can pull the full instructions with
        # read_skill_page(). Without this the tool is registered but the
        # model has no way to discover the available slugs. Mirrors what the
        # GUI server already does per message.
        skills_idx = skills.render_skills_index()
        if skills_idx:
            system_prompt += "\n\n" + skills_idx

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

