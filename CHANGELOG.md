# Changelog

All notable changes to the Llampaca project will be documented in this file.
This project adheres to Semantic Versioning and complies with development logging guidelines.

## [2026-07-16]

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
