# Changelog

All notable changes to the Llampaca project will be documented in this file.
This project adheres to Semantic Versioning and complies with development logging guidelines.

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
