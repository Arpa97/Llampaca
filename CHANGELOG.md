# Changelog

All notable changes to the Llampaca project will be documented in this file.
This project adheres to Semantic Versioning and complies with development logging guidelines.

## [2026-07-20]

### Fixed — GUI: a sent message with attachments now shows which files it carried

- **`llampaca/gui/controllers/chat_controller.js`** — `sendMessage` captures the
  attached (non-errored) filenames before clearing the chips and prepends a
  "📎 name" line per file to the optimistic user bubble. Before, the sent turn
  showed only the typed text, so nothing in the transcript indicated the model
  was answering on the basis of an attached document. This mirrors how a
  reloaded conversation already renders (the backend persists the full
  document blocks, which `parseMarkdown` collapses to the same "📎 name" chips).

### Added — GUI: "Profilo" tab (view/edit wiki pages) and `/remember` in the chat

- **`llampaca/gui/server.py`** — wiki REST endpoints over the same
  `~/.llampaca/wiki/` pages the model and the CLI use:
  - `GET /api/wiki` (list of name+description), `GET /api/wiki/<name>` (full
    content), `POST /api/wiki` (create/overwrite, enforcing `write_page`'s
    non-empty + size-cap rules → 400 on violation), `DELETE /api/wiki/<name>`
    (deleting is a user action, which this is).
  - `/remember <fact>` now works in the GUI chat: `handle_post_message`
    applies the same transform as the CLI (the fact is forwarded to the model
    with the "store faithfully via update_wiki_page" instruction), so the
    write still goes through the GUI's Allow/Decline confirmation.
- **`llampaca/gui/models/wiki_model.js`**, **`controllers/wiki_controller.js`**,
  **`components/WikiView.js`** — new "Profilo" tab: a two-pane page list +
  markdown editor to read, create, edit, and delete memories, with a live
  character count against the page cap. Edits are the same files the assistant
  reads, so they take effect in the next message (the wiki index is rebuilt
  per turn).
- **`llampaca/gui/app.js`**, **`index.html`** — registered the tab (nav item +
  view) and added its styles.
  - *Why:* the wiki was terminal-only; the GUI now exposes it as an editable
    personal profile, and the `/remember` shortcut has GUI parity.

### Changed — single source of truth for the default context window (CLI, GUI, Agent all aligned to 8192)

- **`llampaca/config.py`** — new `DEFAULT_CONTEXT_SIZE = 8192` constant, used
  to seed `DEFAULT_CONFIG["context_size"]`. Added `EMBEDDING_CONTEXT_SIZE = 4096`
  for the (deliberately independent, chunk-sized) embedding server.
- Replaced every hard-coded chat-context fallback with the constant, so the
  default can no longer drift between entry points (they had diverged to
  `32768`, `8192` and `4096`):
  - **`llampaca/agent/loop.py`** — `Agent(context_size=...)` default.
  - **`llampaca/cli.py`** — `run`/`serve`/`gui` `--ctx` fallbacks (were 32768).
  - **`llampaca/engine/server.py`** — chat-mode fallback and
    `restart_active_server` (were 8192 / 32768); the embedding-mode fallback
    now uses `EMBEDDING_CONTEXT_SIZE`.
  - **`llampaca/gui/server.py`** — all four GUI fallbacks (were 4096/32768),
    including the per-message agent and the attachment budget — this is the
    part that aligns the GUI to 8192 as requested.
  - *Why:* the GUI was running at 4096 while the CLI used 8192; the user asked
    for one place that sets the default for everyone. Change
    `DEFAULT_CONTEXT_SIZE` in `config.py` and every path picks it up. (An
    existing `~/.llampaca/config.json` still wins for that user, since
    `load_config` only fills MISSING keys — the constant governs new configs
    and every in-code fallback.)

### Added — GUI: file attachments (📎 button + drag-and-drop), the GUI equivalent of the CLI's `/attach`

- **`llampaca/gui/server.py`** — New upload endpoint and staging pipeline,
  reusing the CLI's attachment logic end to end:
  - `POST /api/conversations/<id>/attach` (`handle_post_attach`): receives the
    raw file bytes (filename in the `X-Attachment-Filename` header), writes a
    temp file preserving the suffix, and calls `attachments.extract_text`.
  - `AgentManager.stage_attachment` applies the CLI's budget rule: within
    `attachment_token_budget` the extracted text is staged for direct
    injection; beyond it the document is indexed via a (lazy) `EmbeddingService`
    and only a search pointer is staged. Staged items are kept per conversation
    under a lock (`pending_attachments` / `pending_index_notes`).
  - `handle_post_message` merges the staged items into the user turn *before*
    persisting (via `build_attachment_block`), so the document travels with the
    message in the DB and survives resume — the same "document + question as one
    user turn" shape the CLI uses (Gemma-style templates reject two consecutive
    user messages).
  - `AgentManager.activate_document_search` (re)binds the `search_documents`
    tool to the CURRENT conversation each message, since the GUI rebuilds the
    agent per turn from one shared registry; the embedding server is stopped on
    shutdown.
  - Auto-titling now strips attachment blocks (`_strip_attachment_blocks`) so a
    first message with a file is not titled after the document's first line.
  - *Why:* the GUI registered the attachment machinery's dependencies but had
    no way to actually attach a file; the terminal `/attach` was the only path.
- **`llampaca/gui/models/chat_model.js`** — `uploadAttachment(convId, file)`.
- **`llampaca/gui/controllers/chat_controller.js`** — attachment state (chips
  with uploading/done/error status), `attachFiles` (button + drop share it),
  drag-and-drop handlers, on-demand conversation creation extracted to
  `ensureConversation`, and a per-conversation guard so creating a conversation
  on drop does not wipe the chips just added.
- **`llampaca/gui/components/ChatView.js`** — wired the (previously inert) 📎
  button to a hidden file input, drag-and-drop over the messages panel with an
  overlay, staged-file chips above the input, and collapsing of persisted
  attachment blocks to a compact "📎 filename" line on reload.
- **`llampaca/gui/index.html`** — styles for the chips and the drop overlay.
- Note: verified by import/compile and the offline test suite (102 passing);
  the upload + RAG path needs a live GUI + running servers to exercise
  end-to-end.

### Fixed — GUI: wiki index missing from the system prompt (model invented page names)

- **`llampaca/gui/server.py`** — `_process_message_coro` now builds the
  agent's `system_prompt` as `DEFAULT_SYSTEM_PROMPT + render_index()`, the
  same way the CLI does at session start. The GUI already registered the
  wiki tools (via `build_default_registry`) but never injected the wiki
  index, so the model had no idea which pages actually exist and
  hallucinated names like "user-identity"/"user-preferences" — while the
  terminal (which injects the index) worked correctly against the same
  `~/.llampaca/wiki`.
  - *Why:* the wiki is only useful if the model knows its contents without
    a blind tool call; the index is that awareness. `render_index()` reads
    the real wiki dir, so GUI and terminal now share one memory view.

### Merged — `dev-umb` (RAG/embeddings + personal wiki) into `GUI_DEV` (GUI + MCP)

- Merged branch `dev-umb` into the new `gui-umb` branch (base `GUI_DEV`),
  reconciling eight conflicting files. Notable resolution decisions:
  - **`llampaca/config.py`** — `context_size` kept at **8192** (dev-umb's
    value, dated 07-18) over GUI_DEV's 32768 (07-17): it is both the more
    recent change and the one with an explicit rationale for ~4B models.
    Combined `mcp_servers` (GUI_DEV) with the embedding/RAG settings
    (dev-umb) in `DEFAULT_CONFIG`, and kept both the `qwen3-embedding-0.6b`
    preset (dev-umb) and the `size_gb` field on the llama3.2 preset
    (GUI_DEV).
  - **`llampaca/engine/server.py`** — kept dev-umb's superset `__init__`
    body that handles both chat and embedding modes.
  - **`llampaca/cli.py`** — kept GUI_DEV's `mcp_manager.stop()` shutdown
    alongside dev-umb's embedding-server shutdown comment.
- **`tests/test_search_tool.py`**, **`tests/test_wiki.py`** — updated the
  tool tests to `await registry.execute(...)`: GUI_DEV made
  `ToolRegistry.execute` a coroutine (for async MCP tools) while dev-umb's
  tests still called it synchronously. `TestWikiTools` now also mixes in
  `IsolatedAsyncioTestCase`.
  - *Why:* the merge combines dev-umb's sync-era tool tests with GUI_DEV's
    async registry; without this the tests receive un-awaited coroutines.
- **`tests/test_mcp_server.py`** — the registry-vs-server tool mapping check
  now compares functions by `__qualname__` instead of object identity.
  - *Why:* dev-umb's wiki tools are closures built per `build_default_registry()`
    call, so two independently built registries yield distinct-but-equivalent
    function objects; identity comparison broke, qualified-name comparison is
    the correct invariant.
- Full suite: 102 tests passing in the conda env.

### Added — Settings persistence, local models management, and runtime LLM server restarts

- **`llampaca/engine/server.py`** — Added global `_active_server` tracking and an async `restart_active_server` method.
  - *Why:* Enables dynamically restarting the underlying `llama-server` process with new configurations (model file, context size, CPU threads, GPU layers) without requiring manual CLI intervention.

- **`llampaca/gui/server.py`** — Implemented API endpoints for settings (`GET`/`POST` `/api/settings`) and local GGUF models (`GET` `/api/models`, `POST` `/api/models/default`, `DELETE` `/api/models/<name>`).
  - *Why:* Acts as a bridge between the frontend GUI actions and the backend server configuration files / subprocess manager.

- **`llampaca/gui/models/settings_model.js`** & **`llampaca/gui/controllers/settings_controller.js`** — Replaced settings mocks with async API requests.
  - *Why:* Wires the Settings tab input elements to load and save real global configuration values.

- **`llampaca/gui/models/gguf_model.js`** & **`llampaca/gui/controllers/models_controller.js`** — Replaced GGUF models mocks with async API requests.
  - *Why:* Wires the Models tab layout to display actual downloaded GGUF files and dynamically trigger model switching at runtime.

### Changed — system prompt: web_search anche quando il modello è incerto

- **`llampaca/agent/loop.py`** — `DEFAULT_SYSTEM_PROMPT`: il trigger per
  `web_search` ora include anche i casi in cui il modello non sa o non è
  sicuro di un fatto ("or anything you are unsure of or do not know"),
  non solo eventi correnti / fatti cambiati dopo il training.
- **Motivo:** i modelli piccoli tendono ad allucinare fatti statici che non
  conoscono invece di cercarli. La frase è stata riscritta (non aggiunta) per
  non allungare il prompt — importante soprattutto in modalità prompt-based
  (Gemma), dove le istruzioni tool vivono già dentro il system prompt.

## [2026-07-18]

### Changed — default context window doubled: 4096 → 8192 tokens

- **`llampaca/config.py`** — `DEFAULT_CONFIG["context_size"]` 4096 → 8192.
- **`llampaca/engine/server.py`** — chat-mode fallback (used when
  config.json lacks the key) aligned to 8192. The EMBEDDING server's
  context stays 4096 on purpose: it is sized for one ~500-token chunk,
  not for conversation.
- **`llampaca/agent/loop.py`** — `Agent(context_size=...)` signature
  default aligned to 8192 (the CLI always passes the resolved value; this
  only affects programmatic users).
- **`README.md`** — `--ctx` table default and config example updated.
- Also updated the user's existing `~/.llampaca/config.json` (which
  pinned the old 4096 — `load_config()` only fills MISSING keys, so the
  code-side default alone would never have taken effect for them).
- *Why:* requested by the user. More room for the agentic loop (system
  prompt + wiki index + tool results + history) before trimming kicks in.
  Cost: the KV cache doubles; with q8_0 cache quantization and ~4B models
  this stays well within the machine's budget. Derived values scale
  automatically: attachment budget (~35% of ctx) and the per-tool-result
  cap (ctx-proportional) both grow with the window.

### Added — personal wiki: persistent cross-session memory (`/remember`)

- **`llampaca/config.py`** — new `WIKI_DIR` (`~/.llampaca/wiki/`), created by
  `ensure_dirs()`. The wiki lives in app data, NOT in the launch workspace:
  it is the user's memory, shared by every project.
- **`llampaca/wiki.py`** (new) — pure file logic: `slugify()` (page names
  normalized to `[a-z0-9-]`, so model-provided names can never traverse
  paths), page CRUD with a size cap (~1500 est. tokens — a page must always
  be whole-readable inside `--ctx 4096`), and `render_index()` (the wiki
  section of the system prompt, capped at 30 listed pages, explicit "the
  wiki is empty" message so the model knows it can create the first page).
  Deliberately NO embeddings/RAG: a personal wiki is a few dozen titled
  markdown pages, and a title index plus deterministic whole-page reads is
  more reliable for small local models than fuzzy retrieval. Pages are
  human-editable markdown on purpose (inspect/correct/delete with any
  editor).
- **`llampaca/tools/wiki.py`** (new) — `read_wiki_page` (no confirmation;
  a missing page lists the existing names so the model self-corrects) and
  `update_wiki_page` (**confirmation-gated** like every other write: a
  trigger-happy model costs one keystroke, never a polluted wiki). The
  "only durable facts, never secrets, overwrite means whole page" policy
  lives in the tool docstring — next to the capability it governs. No
  delete tool: removing memories is a user action on the files.
- **`llampaca/tools/__init__.py`** — wiki tools added to
  `build_default_registry()`: unlike `search_documents` (per-conversation,
  registered on demand) the wiki is global and always available.
- **`llampaca/cli.py`** — (1) the wiki index is appended to the system
  prompt at session start (static for the session by design: rebuilding on
  every write would invalidate llama-server's prefix cache; a page written
  mid-session is confirmed by its tool result); skipped under `--no-tools`.
  (2) New `/remember <fact>` chat command: the fact is forwarded to the
  model as a normal turn so IT picks/creates the right page, and the write
  still passes through the confirmation prompt. The instruction demands the
  fact be copied FAITHFULLY: e2e verification showed Gemma-3-4B
  (prompt-based tool mode) inventing page content / copying the stale index
  description instead of the new fact — Qwen3.5-4B (native mode) stores it
  verbatim; with weaker models the confirmation prompt (which shows the
  exact content) is the safety net. (3) Session header advertises
  `/remember`.
- **`tests/test_wiki.py`** (new) — 20 offline tests: slugify/traversal,
  page CRUD + size caps, index rendering (empty/full/truncated), tool
  registration, confirmation flags, and error-as-string contract. All
  tests run against a tmp wiki dir; full suite: 96 passed.
- *Why:* the assistant had per-conversation memory (history.db) and
  per-document knowledge (RAG attachments) but nothing that crossed
  sessions; the wiki gives small local models durable personalization at
  zero new dependencies (TODO: "llm wiki o rag, second brain per
  personalizzazione utente").

### Added — generation speed (tokens/second) in the turn footer

- **`llampaca/engine/client.py`** — `chat_stream_events()` yields a new
  `("stats", dict)` event, at most once per request right before the final
  `("message", ...)`: llama-server attaches a `timings` object (llama.cpp
  extension) to the final stream chunk — the one with `finish_reason` and
  an empty delta — carrying server-measured counters (`predicted_n`,
  `predicted_ms`, `prompt_n`, `prompt_ms`, ...). Captured before the
  delta guards (which skip that chunk) and not emitted at all when the
  server sends no timings (older builds).
- **`llampaca/agent/loop.py`** — `send()` forwards `("stats", dict)`
  per model request; the UI owns aggregation (a turn with tool
  round-trips is several requests).
- **`llampaca/cli.py`** — the footer now reads
  `[context: ~N% free | took N.Ns | N tok @ N.N tok/s]`: generated tokens
  summed across the turn's requests, speed computed as
  `Σpredicted_n / Σpredicted_ms` — generation-time only, so tool
  execution and prompt processing never dilute the reported speed. The
  speed segment is omitted when no stats arrived.
  - *Why:* requested by the user after the turn-duration display: the
    duration alone can't distinguish "model is slow" from "model wrote a
    lot" — tokens/second is the comparable metric across models and
    settings (it is what the earlier Roo Code vs terminal investigation
    had to dig out of raw server responses by hand).
- **`tests/test_stream_events.py`** — 2 new client tests (stats emitted
  from the final chunk's timings and ordered right before "message"; no
  stats event when the server sends no timings) and the agent-forwarding
  test now covers `stats` too. Suite: 76 tests passing in both
  environments. Verified end-to-end (conda binary, Qwen3-4B, one-tool
  question): footer showed `[context: ~56% free | took 30.5s | 657 tok @
  27.2 tok/s]`.

### Added — live thinking display, early tool-call announcement, turn duration in the CLI

- **`llampaca/engine/client.py`** — `chat_stream_events()` yields two new
  event kinds: `("reasoning", str)` for chunks of the model's thinking
  (llama-server's `reasoning_content` delta extension, read via `getattr`
  because the OpenAI SDK doesn't declare it), and `("tool_name", str)`
  emitted the moment a streamed tool-call fragment first carries the
  function name — long before its JSON arguments finish streaming.
  Reasoning is display-only: it is *not* accumulated into the final
  `("message", ...)` dict, so it can never re-enter the conversation
  history or the database.
- **`llampaca/agent/loop.py`** — `send()` forwards them as
  `("reasoning", str)` and `("tool_start", {"name": ...})` events (both
  documented in the docstring). Reasoning chunks bypass the prompt-mode
  JSON buffering (a tool call never hides in reasoning) and count as
  produced output for the native-tools rejection heuristic: a template
  that rejects the `tools` parameter fails before generating anything, so
  once thinking has streamed, a later error is a runtime failure, not a
  rejection.
- **`llampaca/cli.py`** — the chat loop renders reasoning dim under a
  `[thinking]` header (one block per thinking phase; reasoning models
  think again before each tool round-trip), shows
  `[tool] calling <name>...` as soon as the name is known (the full
  `[tool] name({args})` line still follows once arguments are complete),
  and the turn footer now reads `[context: ~N% free | took N.Ns]` — the
  wall-clock duration of the whole turn (thinking + generation + tool
  executions), measured with `time.monotonic()`.
  - *Why:* measured on Qwen3-4B, a trivial one-tool question generated
    12+ seconds of tokens that were 100% invisible (thinking + tool-call
    JSON): the terminal sat silent while Roo Code, on the *same*
    llama-server, streamed its reasoning live and felt fast. This makes
    the same wait visible — same speed, completely different perception —
    and the duration in the footer makes regressions measurable at a
    glance.
- **`tests/test_stream_events.py`** — new tests: reasoning is streamed
  but kept out of the final message; `tool_name` fires exactly once and
  before the arguments complete (reassembly still intact); the agent
  forwards `reasoning`/`tool_start`. Suite: 74 tests passing in both
  environments (system `python3 -m pytest`, conda `python -m unittest`).
  Verified end-to-end with a real `llampaca run` session (conda binary,
  Qwen3-4B): thinking streams dim, `[tool] calling read_file...` appears
  immediately, footer shows `[context: ~57% free | took 32.8s]`.

### Added — `--no-think` flag for `llampaca run` (disable reasoning models' thinking phase)

- **`llampaca/engine/server.py`** — `LlamaServer` accepts a new
  `no_think: bool` parameter (chat mode only). When set, `_build_command()`
  appends `--reasoning off` to the llama-server command line, which renders
  the chat template with thinking disabled (e.g. Qwen3's
  `enable_thinking=false`) so the model answers directly instead of
  producing a hidden `<think>` block first.
- **`llampaca/cli.py`** — new `--no-think` CLI option on `run`, threaded
  through `async_run_chat()` to the `LlamaServer` constructor.
  - *Why:* investigation of "Llampaca feels slower than Roo Code on the
    same llama-server" showed the wait before any visible output is the
    model's thinking phase: measured on Qwen3-4B, a trivial one-tool
    question spent 1.1s on prompt processing and **12.2s generating 351
    tokens that were 100% invisible** (reasoning + tool-call JSON —
    `LlamaClient` only surfaces `delta.content`, and the tool_call event
    is emitted at end of stream). `--no-think` removes that phase at the
    source for models whose template supports the toggle.
  - *Known limitation (verified empirically):* the flag only works when
    the GGUF's embedded chat template implements the `enable_thinking`
    toggle. The two local third-party conversions tested
    (`Qwen3-4B-Q4_K_M.gguf` from an AWQ re-conversion, and
    `Benasd/Qwen3.5-4B-Q8_0.gguf`) ship templates *without* it (zero
    `enable_thinking` occurrences in the GGUF metadata): with them the
    model keeps thinking and the reasoning text leaks into the visible
    `content` instead of `reasoning_content` — same latency, worse output.
    Official `Qwen/Qwen3-*-GGUF` builds (or non-thinking Instruct-2507
    variants) are required for the flag to have effect.

### Changed — conversation summarization moved off the turn's critical path (deferred, background)

- **`llampaca/agent/loop.py`** — `_trim_history()` is now synchronous and
  trim-only: it drops the oldest turns (same watermark/hysteresis logic as
  before) and *queues* the removed user/assistant turns instead of
  summarizing them inline. New `Agent.summarize_pending()` drains the queue
  with one non-streaming LLM call and returns
  `{"summary", "last_summarized_message_id"}` for persistence; new
  `Agent.has_pending_summary` property tells the UI whether a flush is
  needed. The `("summary_updated", ...)` event is no longer yielded by
  `send()` (the CLI persists the summary itself after the flush).
  - *Why (performance):* the old flow ran a full LLM generation (the
    summary) *before* the user's request whenever trimming fired — which
    happens every time the history climbs from the 60% to the 80%
    watermark, i.e. periodically for the whole life of a long session.
    Two costs on every occurrence: (1) the answer could not even start
    streaming until an entire extra generation finished (seconds on local
    hardware); (2) the summary prompt replaced llama-server's cached
    prompt prefix right before the main request, forcing a full prompt
    re-process. Deferring the summary to after the turn removes both: the
    generation now runs while the user reads/types (server idle time),
    and the main request goes out immediately after the trim.
  - On failure, the queued turns are kept and retried at the next flush
    (previously they were silently dropped from the summary); the messages
    themselves are always safe in SQLite regardless.
- **`llampaca/cli.py`** — After each turn, if the agent has pending
  summary turns, the flush runs as a background `asyncio` task
  (`flush_summary()`), persisting via `update_conversation_summary`. The
  next turn awaits any still-running flush before contacting the server
  (llama-server would serialize the requests anyway, so this costs nothing
  extra and keeps turns strictly ordered). On session exit an in-flight
  flush is awaited before the server stops; no *new* summarization is
  started at exit because un-summarized messages are still in the database
  and are simply reloaded on resume.
- **`tests/test_summary.py`** — `test_trim_history_called` updated to the
  new split: `_trim_history()` must trim and queue *without* calling the
  LLM; `summarize_pending()` must then generate the summary, return the
  persistence payload, and leave the queue empty.

### Changed — database schema checks run once per process instead of on every connection

- **`llampaca/engine/db.py`** — New module-level `_migrated_paths` set:
  the schema-creation and migration block (`PRAGMA table_info` +
  `ALTER TABLE` checks + 2 `CREATE TABLE IF NOT EXISTS` + 2
  `CREATE INDEX IF NOT EXISTS` + commit) now runs only the first time this
  process touches a given database file, instead of inside *every*
  `get_db_connection()` call.
  - *Why (performance):* the connection manager opens a fresh connection
    for each operation, and a chat turn performs several (persist user
    message, persist assistant message, summary update...). Each one was
    re-paying the full idempotent migration ritual. The schema cannot
    change while the process runs, so verifying it once per database
    removes that fixed cost from every message write.
  - Safety: the path is marked as verified only after the whole block
    commits successfully (a failure leaves it unmarked and the next
    connection retries), and a database file that disappears mid-process
    (e.g. deleted by a test) is detected via the existing `db_exists`
    check and re-initialized. Per-connection pragmas that SQLite requires
    per connection (`foreign_keys`, busy timeout, WAL) are untouched.

### Changed — embedding server runs CPU-only by default (independent of the chat model's GPU setting)

- **`llampaca/config.py`** — New `embedding_gpu_layers` config key, default
  `0`.
- **`llampaca/engine/embedding.py`** — `EmbeddingService` reads
  `embedding_gpu_layers` and passes it explicitly to `LlamaServer(...,
  gpu_layers=...)`, instead of letting the embedding server silently
  inherit the chat model's `gpu_layers` (it previously fell through to the
  same config default because nothing was passed).
  - *Why:* User-reported heat/slowdown while the RAG path was active. Root
    cause: both llama-server processes (chat model AND the 0.6B embedder)
    were requesting full Metal offload (`-ngl 99`) simultaneously — two
    GPU-accelerated inference processes competing for the same unified
    memory and thermal budget on an 8 GB Mac. The embedder is small enough
    that CPU is plenty fast for embedding a handful of short chunks, so
    running it off Metal frees the GPU/thermal budget entirely for the
    chat model, where offload actually matters for interactive generation
    speed. `embedding_gpu_layers` can still be raised (e.g. `-1` for auto)
    on a machine with GPU/RAM to spare.
- **`tests/test_embedding.py`** — 2 new tests: `embedding_gpu_layers`
  defaults to 0 in `DEFAULT_CONFIG`; `LlamaServer(embedding=True,
  gpu_layers=0)._build_command()` emits no `-ngl` flag at all (not `-ngl
  0`) — llama-server then never touches Metal/CUDA for that process.
  `EmbeddingService.gpu_layers` also covered directly.
- Verified live: with an oversized attachment, the chat server's logged
  command still carries `-ngl 99` while the embedding server's now omits
  `-ngl` entirely — indexing and search_documents still worked correctly
  (question answered right: "potatura degli ulivi ... tra febbraio e
  marzo").

### Fixed — embedding server crashed on real (larger) documents

- **`llampaca/engine/server.py`** — Embedding mode now passes `--parallel 1`
  and its default context is 4096 (was 2048).
  - *Why:* Found via a real crash reproduced by the user: attaching a
    10-page/~9100-token PDF (20 chunks) crashed the embedding server with
    `failed to find a memory slot for batch of size 1906` followed by a
    fatal `GGML_ASSERT` abort — surfacing to the CLI as an opaque
    "Connection error" during indexing. Root cause, found in the server's
    own log (`llama-server-embed-*.log`): `--parallel` defaults to `-1`
    (auto), which picked 4 slots, splitting `-c` between them
    (`n_ctx_slot = context_size / n_slots` = 2048/4 = 512 tokens per
    slot) — too small for chunks whose REAL token count (observed up to
    630) ran over the chars/4 estimate used to size them at ~500. A single
    slot gives every request the full context; the pipeline embeds one
    batch at a time per session anyway, so no concurrency is lost. The
    earlier milestone 1-4 live checks never hit this because their test
    documents were small enough (6-12 chunks) to fit under a 512-token
    slot even split 4 ways — this bug needed a bigger, more realistic
    document to surface.
  - *Verified:* recreated the exact crashing document (10 pages, ~9114
    tokens, 20 chunks) and re-ran the same `/attach` + question twice:
    indexing now succeeds both times, and a question that explicitly
    referenced "il documento allegato" correctly triggered
    `search_documents` and answered right, citing page 6 ("ogni 200
    chilometri"). A first, more loosely-phrased question made the 4B
    model reach for `web_search` instead — expected small-model phrasing
    sensitivity, not a regression from this fix.
- **`tests/test_embedding.py`** — Updated the two assertions tied to the
  old defaults (context 2048 → 4096) and added a check that `--parallel 1`
  is present in the embedding command line.

### Fixed — conversations no longer titled "[Attached file: ..." (phase 2, milestone 5)

- **`llampaca/engine/db.py`**, **`llampaca/cli.py`** — `add_message()`
  takes an optional `title_snippet`; the CLI passes the user's own words,
  captured before attachment blocks / index notes are merged into the
  message, so auto-titling names the conversation after the question.
  - *Why:* a first message carrying an attachment was titled from the
    merged content — "[Attached and indexed: manuale_grande..." instead of
    the actual question. Verified live: the test session is now titled
    "Secondo il documento, ogni quanti chi...".

### Verified — RAG end-to-end with a real multi-page PDF (phase 2, milestone 5)

- A generated 8-page PDF (~17k extractable chars, distinct topic per page)
  attached in a real session: indexed as "12 chunks, 8 pages", the model
  called search_documents, the result cited the right page
  (`[manuale_grande.pdf, p.6 | relevance ...]`) and the answer was correct
  ("la catena va lubrificata ogni 200 chilometri"). This exercises PDF
  page attribution through the whole pipeline, not just in unit tests.

- **`.claude/skills/verify/SKILL.md`** — Documents the RAG verification
  flow: the piped-session example, the exact line sequence to look for
  ([indexing] → embed-server start → [indexed] → [tool] search_documents →
  two shutdown lines), the embed-server log name, and the note that the
  embedding server starting at session open on resume is intended.

### Added — /attach now indexes oversized documents for search (phase 2, milestone 4)

- **`llampaca/engine/embedding.py`** (new) — `EmbeddingService`: per-session
  owner of the embedding llama-server lifecycle and of the indexing
  pipeline (text → chunks → embeddings → documents/chunks tables).
  - *What:* Construction is cheap (resolves the configured embedding model
    + its pooling/prefix metadata, no server). `ensure_started()` is the
    LAZY start — nothing runs until the first over-budget /attach or a
    resume of a conversation with indexed documents. `index_document()`
    chunks, embeds (document prefix applied), stores, and returns the
    numbers for the CLI's feedback lines. `query_embedder()` hands the
    search tool its sync embed callable (only after start: the sync tool
    cannot start the async server itself). All user-facing failures raise
    `EmbeddingUnavailable` with the fix in the message (e.g. the exact
    `models download` command).

- **`llampaca/cli.py`** — The over-budget `/attach` branch now indexes
  instead of rejecting.
  - *What:* `[indexing]` feedback → lazy server start → `index_document`
    → a NOTE (never the text) staged for the next user message ("this
    document is NOT in your context: use search_documents") → the tool
    registered once per session + `agent.refresh_tools()`. On session
    resume, conversations with indexed documents reactivate the tool at
    open (with a warning listing documents built by a different embedder).
    With `--no-tools`, oversized attachments are still rejected — indexing
    would be useless if the model can never search. Both servers stopped
    in the shutdown path.

- **`llampaca/rag.py`** — New `count_pages()` (page count from the source
  text's markers). Found by a failing test: the previous max-over-chunks
  logic undercounted whenever trailing pages merged into an
  earlier-starting chunk.

- **`tests/test_embedding.py`** — 4 new EmbeddingService tests: missing
  model → actionable EmbeddingUnavailable, query_embedder before start →
  RuntimeError, full index_document roundtrip against a faked embedding
  client (prefix reaches the embedder, metadata recorded, float32 BLOBs
  stored), stop() idempotent without start.

- Verified live end-to-end on the fragile path (gemma-3-4b, prompt-based
  tools): a 26 KB text attached with /attach → indexed into 20 chunks with
  the embedding server starting lazily mid-session → the model called
  search_documents and answered correctly ("le spese si ripartiscono in
  base ai millesimi, articolo 12"); resuming the same conversation
  reactivated the index without re-attaching and answered a new question
  correctly. Both servers shut down at exit in both runs.

### Added — search_documents tool and dynamic tool activation (phase 2, milestone 3)

- **`llampaca/tools/documents.py`** (new) — The only model-facing piece of
  the RAG pipeline.
  - *What:* `register_document_tools(registry, embed_query,
    conversation_id, db_path)` registers `search_documents(query, top_k=4)`:
    embeds the query (sync HTTP via the `make_query_embedder(port,
    query_prefix)` factory, which owns the model's asymmetric query
    prefix), reads the conversation's chunks fresh from the database on
    every call (documents attached mid-session are immediately
    searchable), ranks with rag.top_k, and returns provenance-labeled
    blocks (`[file, p.N | relevance 0.75]`). top_k clamped to 1-8. All
    failure modes return model-readable error strings: no documents
    indexed, embedding server unreachable, index/embedder dimension
    mismatch ("re-attach to re-index").
  - *Why (sync, and a sync db read):* the tool registry executes tools as
    plain sync functions inside the agent's running event loop, where
    aiosqlite cannot be awaited — hence the new read-only
    `get_conversation_chunks_sync()` in **`llampaca/engine/db.py`**
    (stdlib sqlite3, safe next to the async writers thanks to WAL).
  - *Why (not in build_default_registry):* small models call tools more
    reliably the fewer they see; the tool is registered dynamically only
    when a conversation actually has indexed documents.

- **`llampaca/agent/loop.py`** — Dynamic tool activation.
  - *What:* New `Agent.refresh_tools()`: re-syncs `tools_enabled` and, in
    prompt-based mode, rebuilds `messages[0]` from the stored tool-free
    base prompt via the new single source of truth
    `_system_prompt_content()`. `_switch_to_prompt_mode()` now rebuilds
    through the same path instead of appending (`+=`), making both
    operations idempotent — no duplicated tool sections no matter how
    often they run. Native mode needs nothing: definitions are read from
    the registry on every request.
  - *Compatibility:* the summarization feature injects the conversation
    summary into a transient per-request copy (`messages_to_send`), never
    into `messages[0]`, so the rebuild composes with it safely (verified).

- **`llampaca/config.py`** — `CHARS_PER_TOKEN` moved here (the
  dependency-free leaf module) with a re-export from `agent/loop.py`.
  - *Why:* `tools/__init__` → `documents` → `rag` → `agent.loop` →
    `tools.registry` was a circular import chain, caught by the test
    suite; the shared heuristic belongs below all of them.

- **`tests/test_search_tool.py`** (new) — 11 tests: schema generation,
  formatted hits with provenance, top_k clamping, all three tool error
  modes, and refresh_tools in both modes (adds new tool in prompt mode,
  leaves the prompt alone in native mode, idempotence of refresh and of
  the mode switch, tools_enabled resync).

- Verified live: search_documents executed through the registry (JSON
  arguments, like the agent does) against a real embedding server ranks
  the page-3 condominium chunk first (relevance 0.70) for "come si
  dividono le spese dell'ascensore?".

### Added — RAG core: chunking, vector storage and search (phase 2, milestone 2)

- **`llampaca/rag.py`** (new) — Pure-logic RAG core, no I/O.
  - *What:* `chunk_text()` splits extracted document text into ~500-token
    chunks with ~15% overlap, packing whole paragraphs (a hit that starts
    mid-sentence is much harder for the chat model to use) and hard-
    splitting only degenerate paragraphs longer than a whole chunk. The
    `--- Page N ---` markers produced by the PDF extractor are consumed
    and become per-chunk page attribution. `serialize_vector()` /
    `deserialize_vector()` store embeddings as little-endian float32
    BLOBs. `top_k()` ranks chunk rows by cosine similarity in numpy
    (proper normalization, zero-vector guards, and rows with a mismatched
    embedding dimension are skipped rather than crashing or mis-ranking).
  - *Why (brute force, no vector index):* a document is ~100 chunks;
    exhaustive cosine over a few hundred 1024-dim float32 vectors is a
    sub-millisecond matrix product. sqlite-vec/FAISS would add a native
    dependency to speed up something already instant; the one-BLOB-per-
    chunk format does not preclude adopting one later.

- **`llampaca/engine/db.py`** — New `documents` and `chunks` tables in
  history.db, created idempotently (CREATE TABLE IF NOT EXISTS on every
  connection, doubling as migration for existing databases), with CRUD:
  `add_document`, `add_chunks` (single transaction: a document is indexed
  entirely or not at all), `list_documents`, `get_conversation_chunks`
  (join with filenames — the exact `top_k` input), `delete_document`.
  - *What (schema):* documents belong to a conversation with ON DELETE
    CASCADE (deleting a chat deletes its index — the reason the tables
    live in history.db); chunks belong to a document, also CASCADE.
    `embedder_name`/`embedding_dim` are recorded so the pipeline detects
    embedder changes and re-indexes instead of comparing incompatible
    vectors.

- **`pyproject.toml`** — Added `numpy>=1.26.0` (installed in both Python
  environments, per the conda-env lesson of 2026-07-16).

- **`tests/test_rag.py`** (new) — 17 tests: chunker (bounds, overlap
  actually repeating the previous tail, page attribution across chunks,
  marker consumption, giant-paragraph hard split, empty input),
  serialization roundtrip, top_k (ranking, magnitude invariance, k>n,
  zero-vector and dimension-mismatch edges), and the document tables
  (roundtrip usable directly by top_k, conversation scoping, both CASCADE
  paths).

- Verified live end-to-end (milestones 1+2 composed): a synthetic 6-page
  Italian document → 12 chunks → real embeddings → stored and reloaded
  through the db layer → the query "chi paga le spese dell'ascensore del
  condominio?" ranks both page-3 (condominium) chunks first (cosine 0.754
  / 0.721) far above the distractors (0.270).

### Added — Embedding infrastructure for RAG (phase 2, milestone 1)

- **`llampaca/config.py`** — New preset `qwen3-embedding-0.6b`
  (Qwen/Qwen3-Embedding-0.6B-GGUF, Q8_0, ~640 MB) with a new `kind:
  "embedding"` preset field, plus per-model retrieval metadata: `pooling`
  ("last" for Qwen3-Embedding) and the asymmetric `query_prefix` /
  `document_prefix` instruction strings. New config keys `embedding_model`
  and `embedding_port` (8180). New helper `get_preset_for_file()` for
  filename→preset reverse lookup.
  - *Why:* Chosen embedder for the RAG pipeline (best multilingual quality
    in its size class — documents will often be Italian). Pooling and
    prefixes live next to the model they belong to because getting either
    wrong degrades retrieval silently, with no error anywhere.

- **`llampaca/engine/server.py`** — `LlamaServer(embedding=True,
  pooling=...)` runs llama-server as an embedding server; command-line
  construction factored into `_build_command()`.
  - *What:* Embedding mode uses `--embedding --pooling <mode>
    --ubatch-size == -c` (2048 default) on its own port range (8180+), with
    its own `llama-server-embed-<port>` pid/log files (still matching the
    orphan-cleanup glob). None of the chat flags (--jinja, --cache-reuse,
    -fa, KV quantization) are passed. `cleanup_orphans()` is skipped in
    embedding mode.
  - *Why (skip cleanup):* the embedding server starts lazily while the chat
    server is already running — running cleanup there would kill the chat
    server mid-session. The chat server, always first, keeps doing cleanup
    for both roles.

- **`llampaca/engine/client.py`** — New `LlamaClient.embed(texts,
  batch_size=32)` on the OpenAI-compatible /v1/embeddings endpoint:
  batched, order-preserving (results re-sorted by index defensively).
  Prefixes are the caller's job — they are model-specific and asymmetric.

- **`llampaca/cli.py`** — `models download` now routes the downloaded file
  to the right config key by preset kind: embedding models set
  `embedding_model`, everything else sets `default_model` as before.
  - *Why:* Downloading the embedder must not hijack the *chat* default —
    `llampaca run` would try to converse with a model that cannot generate.

- **`tests/test_embedding.py`** (new) — 9 tests: preset completeness,
  chat-vs-embedding command lines, embedding-mode defaults (port range,
  context, pid naming), configurable pooling, and `embed()` batching/order
  guarantees against a fake endpoint.

- Verified live on this machine: server starts on 8180, vectors are
  1024-dim, and an Italian query ("quando scade il contratto di affitto?")
  ranks the lease-contract sentence far above two distractors (cosine 0.755
  vs 0.271/0.111). Chat session re-verified unaffected after the
  `_build_command()` refactor.

## [2026-07-17]

### Added — External Model Context Protocol (MCP) Client support

- **`llampaca/config.py`** — Added `load_mcp_config()` and `save_mcp_config()` to support `mcp_config.json` configuration file, isolation, and default values.
  - *Why:* Keeps user-modifiable MCP settings (configured servers and repository sources) isolated from the core settings in `config.json`, enabling easy factory resets.

- **`llampaca/tools/registry.py`** — Made `ToolRegistry.execute` asynchronous and updated it to support both standard synchronous tools and asynchronous coroutines or awaitable tool wrappers.
  - *Why:* Allows the registry to execute asynchronous tools, which is necessary when invoking external MCP server operations via async sessions.

- **`llampaca/agent/loop.py`** — Updated the tool execution step inside the agentic loop to await the now-asynchronous `self.registry.execute`.
  - *Why:* Adapts the agent core loop to handle async tool executions.

- **`llampaca/engine/mcp_client.py`** — Created a new client manager class `McpClientManager` to start external MCP servers using standard stdio JSON-RPC transport, fetch their tools, format various server payload responses, and dynamically register tools under a prefixed namespace (e.g. `gmail__send_email`). Included dangerous keywords heuristics and config overrides for tool confirmation rules.
  - *Why:* Encapsulates external MCP server lifetime management and dynamic tool routing safely.

- **`llampaca/cli.py`** — Integrated `McpClientManager` startup and shutdown lifecycle hooks into the `async_run_chat` command. Added the new `llampaca integrations` Click command group (supporting list, browse via Glama API, add, remove, and repo management).
  - *Why:* Connects the user's configured MCP servers when starting the CLI session and provides a user-friendly terminal interface to discover and manage MCP integrations.

- **`tests/test_mcp_client.py`** — Added unit tests verifying `load_mcp_config`, `save_mcp_config`, integrations subcommands, repository changes, config overrides, default dangerous heuristics, result formatting, dynamic wrapping, and manager subprocess controls.
  - *Why:* Guarantees the robustness of the external MCP integration, unified config loading, and CLI commands, preventing regressions.

- **`README.md`** — Documented Model Context Protocol (MCP) server integration, CLI `llampaca integrations` command suite, configuration path (`~/.llampaca/mcp_config.json`), and checked off MCP in the roadmap section.
  - *Why:* Provides clear instructions for users on how to search, add, remove, and manage external MCP server tools in Llampaca.

## [2026-07-16]

### Added — Model Context Protocol (MCP) Server support for VSCode integration

- **`pyproject.toml`** — Added `mcp>=1.0.0` dependency.
  - *Why:* Enables using the official Anthropic MCP SDK in python to implement a stdio-based MCP server.

- **`llampaca/engine/mcp_server.py`** — Implemented MCP server initialization using `FastMCP` that dynamically maps all registered tools from Llampaca's registry.
  - *Why:* Exposes the filesystem, web search, and shell tools of Llampaca to external AI clients.

- **`llampaca/cli.py`** — Added `llampaca mcp` Click command to start the MCP server, and `llampaca serve` command to run the local LLM server in the foreground without opening a chat session.
  - *Why:* Gives a clean and dedicated way to start either the tool server or the LLM server separately, which is ideal for external VSCode integration.

- **`tests/test_mcp_server.py`** — Created new unit tests verifying the MCP server creation and correct mapping of all registry tools.
  - *Why:* Ensures functionality and protects against mapping regressions.

### Added — Transparent context window summarization with SQLite persistence

- **`llampaca/engine/db.py`** — Added `summary` and `last_summarized_message_id` columns to `conversations` table with auto-migration. Added `update_conversation_summary` function and returned `id` for messages in `get_conversation`.
  - *Why:* Storing the summary and message pointers in SQLite ensures that the full conversation history remains in the database (for GUI display) while allowing the Agent to reload only the active messages.

- **`llampaca/agent/loop.py`** — Updated `Agent` constructor to support `summary`. Made `_trim_history` asynchronous and integrated non-streaming LLM summarization. Added `MIN_ACTIVE_WINDOW = 6` safety window constraint. Yielded `summary_updated` event instead of showing warning messages.
  - *Why:* Keeps the LLM context usage stable in long chats transparently without displaying warnings or deleting actual messages from history.

- **`llampaca/cli.py`** — Updated chat initialization to load summary and filter active history. Associated DB message IDs to in-memory messages. Handled `summary_updated` event to write back to SQLite.
  - *Why:* Connects the database persistence with the Agent context trimming logic seamlessly.

- **`llampaca/engine/server.py`** — Added `-fa on` (Flash Attention) and `-ctk q8_0` / `-ctv q8_0` (Key-Value cache quantization) to `llama-server` start options.
  - *Why:* Dramatically optimizes memory usage and speed when the context window fills up, preventing VRAM swapping and slow quadratically scaled attention computations.

- **`tests/test_summary.py`** — Created new unit tests verifying the migration, context trimming, LLM call mock, and safety active window.
  - *Why:* Validates the functionality and prevents regressions.

### Changed — verify skill now documents the two Python environments

- **`.claude/skills/verify/SKILL.md`** — Documents that the user's real
  `llampaca` entry point is the conda env
  (`~/miniconda3/envs/llampaca`, editable install), not system `python3`;
  new dependencies must be installed in BOTH, and end-to-end verification
  must use the conda binary. Example commands updated accordingly (plus:
  the leading `1` line for the session-selection menu, and an `/attach`
  example).
  - *Why:* The skill only mentioned system `python3`; verification against
    it passed while the user's real environment lacked the new `pypdf`
    dependency — the direct cause of the `/attach` crash fixed below.

### Fixed — `/attach` crashed the whole session when `pypdf` was missing

- **`llampaca/attachments.py`** — The lazy `pypdf` / `python-docx` imports
  are now wrapped: a missing package raises `AttachmentError` with the
  exact fix ("pip install pypdf") instead of `ModuleNotFoundError`.
  - *Why:* The new dependencies had been installed in one Python
    environment while the user's `llampaca` entry point ran from another
    (a conda env created before this feature): `/attach file.pdf` killed
    the entire chat session with a traceback. The text-file path worked,
    masking the problem, because plain text needs no extractor library.

- **`llampaca/cli.py`** — The `/attach` handler now also catches generic
  `Exception` (one red `[attach error]` line, session keeps running), not
  just `AttachmentError`.
  - *Why:* Same incident, second lesson — no extractor bug should ever be
    able to take down the conversation; `/attach` is chrome, not the chat.

## [2026-07-15]

### Added — `/attach`: attach a PDF/Word/text file to the chat (phase 1)

- **`llampaca/attachments.py`** (new) — Text extraction and budgeting for
  user attachments.
  - *What:* `extract_text()` dispatches on extension: PDF via `pypdf` (with
    `--- Page N ---` markers, empty-password decryption attempt, and a
    dedicated "no text layer (scanned document?)" error), `.docx` via
    `python-docx` (paragraphs + tables as pipe-separated rows), and a
    whitelist of plain-text extensions read verbatim. All failures raise
    `AttachmentError` with a user-facing, actionable message (e.g. legacy
    `.doc` → "re-save as .docx"). `attachment_token_budget()` caps an
    attachment at 35% of the context window (`ATTACH_MAX_CONTEXT_FRACTION`),
    using the same chars/4 estimate as the agent loop;
    `build_attachment_block()` frames the text with begin/end markers naming
    the file. Extractor libraries are imported lazily so the CLI never pays
    for them until a file is actually attached.
  - *Why:* First step of the attachment roadmap (direct injection for small
    files; RAG for large ones is phase 2). The hard 35% budget exists
    because silently truncating a document is worse than refusing it — the
    model would answer confidently from half a contract. Oversized files
    are rejected with the budget numbers and the `--ctx` suggestion until
    phase 2 lands.

- **`llampaca/cli.py`** — New `/attach <path>` command in the interactive
  session.
  - *What:* `/attach` extracts the file (path `~`-expanded, resolved against
    the launch directory, deliberately NOT workspace-sandboxed: the path is
    typed by the user, not chosen by the model) and *stages* it; the staged
    block is merged into the user's **next** message (documents first,
    request last), with the total of all staged attachments held under the
    same 35% budget. Feedback lines show estimated tokens and budget usage;
    errors are shown in red and never kill the session. The session header
    now advertises the command.
  - *Why (merge instead of a separate history message):* Gemma-style chat
    templates hard-reject consecutive `user` messages ("Conversation roles
    must alternate..."), verified empirically on llama.cpp b10001 — a
    standalone attachment message followed by the user's question would 400
    on every prompt-mode model. One combined user turn works with every
    template.
  - *Why (the "content included below, do NOT use tools" hint in the
    block):* verified in a live session that without it the 4B models hunt
    for the attached file with `read_file`/`search_text` in the workspace
    instead of reading the text already in their prompt.

- **`pyproject.toml`** — Added `pypdf>=4.0.0` and `python-docx>=1.1.0` to
  the dependencies.

- **`tests/test_attachments.py`** (new) — 15 tests: extraction round-trips
  for text/PDF/docx (the PDF fixture is assembled programmatically with a
  byte-exact xref so the test is hermetic), every error path (missing file,
  directory, unsupported/legacy extension, corrupt PDF/docx, empty file),
  budget math, and the attachment-block framing.

## [2026-07-14]

### Added — `read_file` can read a range of lines

- **`llampaca/tools/filesystem.py`** — `read_file` takes two new *optional*
  parameters, `start_line` (1-indexed) and `max_lines`.
  - *What:* Omitting both keeps the previous behaviour byte-for-byte (read
    from the start, truncate at `MAX_READ_CHARS`), so a model that ignores
    them sees no change. With them, the model reads a window of the file:
    the result is prefixed with `[lines A-B of N in 'path']` so it can map
    the text back to line numbers, and whenever more lines remain the footer
    names the exact `start_line` to resume from. Truncation now also cuts on
    a line boundary (instead of mid-line) and carries the same resume hint.
    Out-of-range starts return an explicit error rather than empty output;
    the size cap and the workspace sandbox apply to ranged reads too. No
    change to the tool registration: the JSON schema is generated from the
    type hints, so `path` stays the only required parameter.
  - *Why:* A big file was previously all-or-nothing: `read_file` truncated at
    the cap and the model had no way to reach the rest, so a line number
    reported by `search_text` (`file:line`) was unusable — it could not read
    around it. That is the core coding loop on a small context window: search
    → read just that region → `edit_file`. Reading whole files instead of the
    relevant window is also what makes the context fill up in the first place.

### Added — context-budget management in the agent loop

- **`llampaca/agent/loop.py`** — The agent now trims its own history before
  every request instead of letting the prompt outgrow the context window.
  - *What:* New `Agent(context_size=...)` parameter, a chars/4 token estimator
    (`_estimate_tokens`), and `_trim_history()` with two watermarks: trimming
    starts above 80% of the window and cuts down to 60%, always keeping the
    system prompt (messages[0]) and the latest message pinned. In native tool
    mode, dropping an assistant message that carried `tool_calls` also drops
    its now-orphaned `tool` result messages, which the server would otherwise
    reject. A `warning` event tells the UI how many messages were dropped.
    `context_usage()` exposes (estimated tokens, context size) for display.
  - *Why:* The history grew unboundedly; on overflow llama-server truncates
    the prompt from the front — the system prompt first, which in prompt-based
    tool mode carries the tool instructions themselves, silently breaking the
    session. The hysteresis (80→60) exists for speed: each trim changes the
    prompt prefix and invalidates llama-server's prompt cache, so trimming in
    one big cut every N turns beats trimming a little on every turn.

### Changed — tool-result cap now scales with the context window

- **`llampaca/tools/registry.py`**, **`llampaca/tools/__init__.py`** — The cap
  on a single tool result is a per-registry setting instead of a fixed 8000
  characters; `build_default_registry(max_result_chars=...)` forwards it.
  - *What:* `ToolRegistry(max_result_chars=...)`, default unchanged (8000).
  - *Why:* 8000 chars ≈ 2000 tokens — half the default 4096 window, so one
    `read_file` could swamp the whole context; with a bigger `--ctx` the fixed
    cap was instead needlessly strict.

- **`llampaca/cli.py`** — Wires the resolved context size into both pieces:
  `build_default_registry(max_result_chars=server.context_size)` (numerically
  ≈ 25% of the window in tokens) and `Agent(context_size=server.context_size)`.
  Uses `server.context_size` — the value actually passed to llama-server —
  rather than the raw `--ctx` flag, which may be None.

### Added — context-usage indicator in the chat UI

- **`llampaca/cli.py`** — After each turn the CLI prints a dim
  `[context: ~NN% free]` line based on `Agent.context_usage()`.
  - *Why:* The user asked to see how much context remains during a session;
    the estimate is heuristic (chars/4), hence the "~".

### Changed — prompt-cache friendliness (tool-calling speed)

- **`llampaca/engine/server.py`** — llama-server is launched with
  `--cache-reuse 256`, enabling KV-cache chunk reuse via context shifting when
  a prompt only partially matches the cached prefix.
  - *Why:* History trimming changes the prompt prefix; without cache reuse
    every trim would force reprocessing the entire prompt (the slowest phase
    on local hardware).
- **`llampaca/engine/client.py`** — `chat_stream_events` now sends
  `extra_body={"cache_prompt": true}` (a llama-server extension) explicitly.
  - *Why:* The agent loop re-sends the whole history on every tool
    round-trip; prefix caching makes each round-trip pay only for the new
    tokens. Recent builds default to true — passing it explicitly protects
    against older binaries and documents the dependency.

### Decided — flash attention flag intentionally NOT added

- **`llampaca/engine/server.py`** — Comment documenting why `-fa` is not
  passed at launch, despite being considered for prefill speed.
  - *Why:* Verified against the installed binary: recent llama.cpp builds
    default `--flash-attn` to `auto` (enabled wherever the backend supports
    it), so the flag would add nothing — while older builds used a bare
    boolean `-fa`, so the new `-fa auto` syntax would make them fail to
    start. Flash attention is exact (same output, faster algorithm), and the
    `auto` default already provides it on every capable binary.

## [2026-07-12]

### Changed — steer the model to compute exact answers via the shell

- **`llampaca/agent/loop.py`** — `DEFAULT_SYSTEM_PROMPT` now tells the model it
  cannot see individual characters or do reliable mental arithmetic, and that
  it must use `run_shell_command` for anything requiring exactness (counting
  characters/words/lines, reversing or sorting text, arithmetic).
  - *What:* A behavioural nudge only. No new tool: `run_shell_command` already
    existed in `llampaca/tools/shell.py`, already gated behind
    `requires_confirmation=True`.
  - *Why:* LLMs tokenize text, so questions like "count the o's in this
    sentence" are answered by guessing and are usually wrong. The prompt
    previously said the model *could* run shell commands but never suggested
    using them for this class of task, so it never did.

### Changed — tool confirmation now shows the code verbatim

- **`llampaca/agent/loop.py`** — New `Agent._format_confirmation()`; the
  confirmation prompt no longer dumps the raw JSON arguments.
  - *What:* Decodes the arguments JSON and prints each one unescaped, with
    multi-line values (shell scripts, file contents) rendered as an indented
    block. Falls back to the raw JSON if it cannot be parsed, so the user is
    never shown less than what will actually run. Output is plain text, so it
    stays UI-agnostic and the confirm callback signature is unchanged.
  - *Why:* Shell commands reached the user JSON-escaped and collapsed onto one
    line (`{"command": "echo \"x\" | grep -o o | wc -l"}`), which is exactly
    the wrong format for a prompt whose entire purpose is letting the user vet
    code before it executes on their machine.

### Fixed — prompt-based tools broken by DB conversation restore

- **`llampaca/cli.py`** — Conversation restore no longer replaces the agent's
  system prompt.
  - *What:* `async_run_chat` used to do `agent.messages = messages`, where
    `messages` always starts with the generic system message stored in the
    database ("You are Llampaca, a helpful local AI personal assistant.").
    That overwrote the system prompt `Agent.__init__` had just built — which
    carries the current date and, in prompt-based mode, the tool definitions
    themselves. Now only the actual conversation turns (user/assistant) are
    appended after the agent-built system message.
  - *Why:* On models whose chat template has no tool support (e.g. Gemma),
    tools work by injecting their definitions into the system prompt. The
    overwrite silently stripped them, so Gemma never saw any tool and
    prompt-based tool calling stopped working after the async/DB features
    were merged. Native-mode models (e.g. Qwen) were unaffected because
    their tools travel in the API `tools` parameter. This was a semantic
    conflict between two features developed in parallel (DB persistence on
    main, prompt-based tool calling on umberto): git merged them cleanly
    because the code paths never touch the same lines.

## [2026-07-10]

### Added — delete_path tool (confirmation-gated deletion)

- **`llampaca/tools/filesystem.py`** — New `delete_path(path)` tool.
  - *What:* Permanently deletes a file or an entire directory (recursively)
    inside the workspace. Always requires user confirmation. On top of the
    sandbox it refuses two catastrophic targets outright: the workspace root
    itself and `.git` (or anything inside it). Directory deletions report how
    many contained items were removed.
  - *Why:* Users asked the agent to clean up files/folders; without a
    dedicated tool the model fell back on `rm -rf` via `run_shell_command`,
    which has no sandbox and no `.git` guard. A purpose-built tool keeps
    deletion inside the workspace with explicit, irreversible-action warnings.

### Fixed — edit_file unusable from prompt-based mode (Gemma)

- **`llampaca/agent/loop.py`** — `_extract_tool_call` now parses candidate
  JSON with `strict=False`.
  - *What:* Accepts literal control characters (newlines, tabs) inside JSON
    string values, which spec-compliant parsing rejects.
  - *Why:* Prompt-mode models like Gemma routinely emit multi-line tool
    arguments with raw newlines instead of `\n` escapes — which is exactly the
    common case for `edit_file`/`write_file` content. Those calls were being
    silently discarded as "not a tool call", so edits never executed.
    Reproduced with a unit case (multi-line `old_text`) and verified fixed
    end-to-end with Gemma editing a Python file.

### Added — Prompt-based tool calling with automatic mode detection

- **`llampaca/agent/loop.py`** — The agent now supports two tool-calling modes
  and picks one automatically per model.
  - *What:* NATIVE mode (unchanged) uses the OpenAI `tools` parameter and the
    server's structured `tool_calls` — used when the model's chat template
    mentions tools (e.g. the Qwen family). PROMPT-BASED mode — for models like
    Gemma whose template has no tool support — injects the tool definitions
    into the system prompt, asks the model to reply with a single JSON object
    to call a tool, parses that JSON out of the reply text
    (`_extract_tool_call`, tolerant of ```json fences and surrounding text,
    strict about naming a registered tool), and feeds results back as
    user-role messages (these templates reject the "tool" role). The mode is
    detected at startup by inspecting the server's chat template; if a native
    request is still rejected at runtime, the agent switches to prompt mode on
    the fly instead of disabling tools. Streaming is preserved: prompt-mode
    output is buffered only while it still looks like JSON, so prose streams
    live and raw tool-call JSON is never shown to the user.
  - *Why:* Passing `tools` to a model whose template can't render them means
    the model never sees the definitions and *hallucinates* tool results
    (observed with Gemma 3 4B inventing file contents). This closes the gap:
    Qwen-style models keep the most reliable native path, everything else
    still gets working tools.
- **`llampaca/engine/client.py`** — New `get_chat_template()` (reads
  llama-server's `/props` endpoint) used for the mode detection.
- **`llampaca/cli.py`** — The session header now shows the detected mode:
  `Tools enabled (native): ...` / `Tools enabled (prompt-based): ...`.

### Added

- **Database Storage (`llampaca/engine/db.py`)**: Integrated SQLite-based persistent storage for conversation history. Added tables `conversations` (UUID identifier, title, model, timestamps) and `messages` (role, content, creation timestamp) with `ON DELETE CASCADE` constraints. Uses WAL (Write-Ahead Logging) and a busy timeout to support concurrent writes by multiple agents.
- **CRUD Operations**: Implemented connection managers and functions to create, query, list, update, and delete conversations and messages.
- **Auto-Titling Logic**: Automatically sets the conversation title using a truncated snippet of the first user message when a conversation is initialized with the default title.
- **Unit Tests (`tests/test_db.py`)**: Added test coverage verifying the schema integrity, auto-titling thresholds, message cascade deletions, and timestamp-based sorting.
- **edit_file tool; hardened web_search**:
  - **`llampaca/tools/filesystem.py`** — New `edit_file(path, old_text, new_text)` tool (confirmation-gated, like `write_file`). Replaces an exact snippet inside a file, leaving the rest untouched.
  - **`llampaca/tools/web.py`** — `web_search` now tries two DuckDuckGo endpoints ("lite", then "html") with per-endpoint parsers, decodes redirect wrappers, and filters out sponsored results.
- **File search tools and date-aware system prompt**:
  - **`llampaca/tools/filesystem.py`** — New `find_files(pattern)` and `search_text(text, path)` tools, plus a shared `_iter_searchable_files()` walker that prunes VCS/cache/dependency directories and hidden entries.
  - **`llampaca/agent/loop.py`** — The system prompt now ends with today's date to help local models with temporal search.
- **Web search tool (Roadmap item: "DeepSearch / web search integration")**:
  - **`llampaca/tools/web.py`** — New `web_search(query)` tool using DuckDuckGo's no-JavaScript lite endpoint.
  - **`llampaca/agent/loop.py`** — Updated `DEFAULT_SYSTEM_PROMPT` to suggest `web_search` for current/possibly-changed facts.
- **Agent session crashed on a mid-turn server error handling**:
  - **`llampaca/agent/loop.py`** — Reworked error handling in `Agent.send()` to report errors via `("error", ...)` events while keeping the session alive.
  - **`llampaca/cli.py`** — Renders `("error", ...)` in red and wraps turns in a try/except safety net.
- **Agentic tool-execution loop**:
  - **`llampaca/tools/registry.py`** — New `ToolRegistry`/`Tool` classes to auto-generate JSON schemas from type hints and docstrings.
  - **`llampaca/tools/filesystem.py`** — Sandboxed `read_file`, `write_file`, `list_directory` tools.
  - **`llampaca/tools/shell.py`** — Gated `run_shell_command` execution.
  - **`llampaca/tools/web.py`** — HTML-to-text `fetch_url`.
  - **`llampaca/tools/__init__.py`** — `build_default_registry()` helper.
  - **`llampaca/agent/loop.py`** — New `Agent` class driving the agentic loop.
- **Developer tooling**:
  - **`.claude/skills/verify/SKILL.md`** — Recipe for building/launching/driving Llampaca end-to-end to verify changes.

### Changed

- **Configuration (`llampaca/config.py`)**: Added `DB_PATH` parameter pointing to `~/.llampaca/history.db`.
- **Documentation (`README.md`)**: Updated the layout table, database specification section, and CLI reference.
- **Server Database Integration (`llampaca/engine/server.py` & `db.py`)**: Defined `self.db` in `LlamaServer` and added `db_path` alias.
- **CLI Commands (`llampaca/cli.py`)**: Updated `history list` and `delete` commands to run asynchronously using `asyncio.run()`.
- **Interactive Chat Database Integration (`llampaca/cli.py`)**: Integrated conversation selection at start, saving user prompts and assistant final responses in real-time.
- **`llampaca/engine/client.py`** — Added `chat_stream_events()` for structured streaming and tool call reassembly.
- **`llampaca/engine/server.py`** — Pass `--jinja` when launching llama-server to support tool calling.
- **`llampaca/cli.py`** — `run` command now drives the `Agent` and renders its events, with a `--no-tools` flag for plain chat mode.

### Removed

- **`llampaca/agent/base.py`** — Deleted the empty `LocalAgent` scaffold.
