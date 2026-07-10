# Changelog

All notable changes to Llampaca are documented in this file.
Each entry includes the date, a brief description of the change, and the reason behind it.

## 2026-07-10

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

### Added — edit_file tool; hardened web_search

- **`llampaca/tools/filesystem.py`** — New `edit_file(path, old_text, new_text)`
  tool (confirmation-gated, like `write_file`).
  - *What:* Replaces an exact snippet inside a file, leaving the rest
    untouched. The snippet must match exactly once: zero matches or ambiguous
    matches return an instructive error instead of editing the wrong place.
  - *Why:* `write_file` only overwrites whole files, so small local models had
    to reproduce the entire content to change one line — slow and error-prone
    (risk of silently dropping parts of the file). Targeted replacement is the
    safer editing primitive.
- **`llampaca/tools/web.py`** — `web_search` now tries two DuckDuckGo
  endpoints ("lite", then "html") with per-endpoint parsers, decodes the
  html endpoint's `uddg=` redirect wrappers to real URLs, and filters out
  sponsored results (`y.js`/`ad_domain` ad redirects).
  - *Why:* A single regex over one endpoint's markup was fragile (a query
    could return an empty/challenge page that the other endpoint answers) and
    the html endpoint put an advertisement with a huge tracking URL as the
    first result, polluting the model's context.

### Added — File search tools and date-aware system prompt

- **`llampaca/tools/filesystem.py`** — New `find_files(pattern)` and
  `search_text(text, path)` tools, plus a shared `_iter_searchable_files()`
  walker that prunes VCS/cache/dependency directories (`.git`, `__pycache__`,
  `node_modules`, virtualenvs, …) and hidden entries.
  - *What:* `find_files` locates files by glob pattern anywhere in the
    workspace; `search_text` greps file contents (case-insensitive substring)
    returning `file:line: content` matches. Both are sandboxed to the
    workspace, skip binaries and >2 MB files, and cap result counts to protect
    the small context window of local models.
  - *Why:* Searching the codebase is the most-used capability when working on
    a project; without these tools the model had to fall back on
    `run_shell_command` (confirmation friction on every grep) or read files
    one by one.
- **`llampaca/agent/loop.py`** — The system prompt now ends with today's date.
  - *Why:* Local models have old training cutoffs and no clock: they guessed
    the date and e.g. built web searches around the wrong year. Injecting the
    real date at session start fixes that for free.

### Added — Web search tool (Roadmap item: "DeepSearch / web search integration")

- **`llampaca/tools/web.py`** — New `web_search(query)` tool.
  - *What:* Runs a web search and returns the top results (title, URL, snippet)
    so the model can find relevant pages instead of guessing URLs. Uses
    DuckDuckGo's no-JavaScript "lite" endpoint via HTTP POST (a GET returns an
    anti-bot challenge), parsing results with the standard library only.
  - *Why:* With only `fetch_url`, the model had no way to *find* pages: it
    either guessed (usually wrong) URLs, or tried to scrape Google and got its
    consent/login wall instead of results. A real search backend that needs no
    API key or login fits Llampaca's free/local ethos.
- **`llampaca/agent/loop.py`** — Updated `DEFAULT_SYSTEM_PROMPT` to tell the
  model to use `web_search` for current/possibly-changed facts instead of
  answering from memory.
  - *Why:* Small local models otherwise answer from stale training data; an
    explicit hint nudges them to search and cite.

### Fixed — Agent session crashed on a mid-turn server error

- **`llampaca/agent/loop.py`** — Reworked error handling in `Agent.send()`.
  - *What:* Previously ANY streaming exception was assumed to mean "the model
    rejected the tools parameter", so tools were disabled and the turn retried;
    if the retry also failed the exception was re-raised, crashing the whole
    CLI with a traceback. Now:
    - The tools-unsupported fallback only triggers on a genuine first-contact
      rejection (tools sent, never yet confirmed working, and no text streamed
      yet), tracked via the new `_tools_confirmed_working` flag.
    - Any other failure (server compute/GPU-memory error, dropped connection,
      etc.) is reported via a new `("error", ...)` event and the turn ends
      gracefully — the session stays alive.
    - New `_describe_error()` helper recognizes llama-server "Compute error"
      (an out-of-GPU-memory symptom on Apple Silicon) and returns actionable
      guidance (fewer `--gpu` layers, smaller `--ctx`, lighter quant).
  - *Why:* A real user session died with an `openai.APIError: Compute error.`
    traceback. Root cause was a Metal GPU out-of-memory in llama-server (an
    environment/resource issue), but the loop misdiagnosed it as a tools
    problem and, worse, let it crash the app. A single failed turn must never
    kill the REPL, and tools that already work must not be silently disabled.
- **`llampaca/cli.py`** — The `run` REPL now renders the `("error", ...)` event
  (in red) and wraps each turn in a try/except safety net so no unexpected
  exception can crash the session.
  - *Why:* Defense in depth — keep the interactive session alive no matter what
    a single turn throws.

### Added — Agentic tool-execution loop (Roadmap item: "Agentic tool/skill execution loop")

- **`llampaca/tools/registry.py`** — New `ToolRegistry`/`Tool` classes.
  - *What:* Registers plain Python functions as agent tools and auto-generates
    their OpenAI-compatible JSON Schema from type hints + docstrings. Executes
    tools by name from model-produced JSON arguments, converting every failure
    (unknown tool, malformed JSON, runtime exception) into an error string.
  - *Why:* Tool authors should just write a well-documented Python function and
    get the model-facing schema for free; a broken tool call must never crash
    the agent loop — the model needs to read the error and adapt.
- **`llampaca/tools/filesystem.py`** — `read_file`, `write_file`, `list_directory`.
  - *What:* Filesystem tools sandboxed to the workspace (the directory where
    `llampaca run` was launched). Paths escaping the sandbox are rejected.
    `write_file` requires user confirmation.
  - *Why:* Give the agent real file access while preventing it from touching
    anything outside the user's working directory.
- **`llampaca/tools/shell.py`** — `run_shell_command`.
  - *What:* Runs a shell command (60s timeout), returning exit code + stdout +
    stderr. Always requires user confirmation.
  - *Why:* Arbitrary command execution is powerful but dangerous, so it is
    always gated behind an explicit user approval.
- **`llampaca/tools/web.py`** — `fetch_url`.
  - *What:* Downloads a URL and converts HTML to plain text using only the
    standard library (no BeautifulSoup dependency).
  - *Why:* Let the agent read documentation/articles without adding heavy
    dependencies; a full DeepSearch integration remains a separate roadmap item.
- **`llampaca/tools/__init__.py`** — `build_default_registry()` helper.
  - *What:* Returns a registry pre-loaded with all built-in tools.
  - *Why:* Single entry point for the CLI (and future GUI) to get the standard
    tool set.
- **`llampaca/agent/loop.py`** — New `Agent` class implementing the agentic loop.
  - *What:* Sends the conversation + tool definitions to the model, executes any
    requested tool calls (asking for confirmation on dangerous ones), feeds the
    results back, and repeats until a final answer or a 10-iteration cap. It is
    UI-independent: it yields events (`text`, `tool_call`, `tool_result`,
    `warning`) and asks for confirmation via an injected callback.
  - *Why:* This is the core that turns Llampaca from a chatbot into an agent.
    Keeping it UI-independent means the planned GUI/HTTP API can reuse the exact
    same core without a rewrite.

### Changed

- **`llampaca/engine/client.py`** — Added `chat_stream_events()`.
  - *What:* Structured streaming that supports the OpenAI `tools` parameter and
    reassembles tool calls arriving split across many stream chunks. The old
    text-only `chat_stream()` is kept for plain chat mode.
  - *Why:* The agent loop needs tool-call data from the stream, which the
    previous text-only client discarded.
- **`llampaca/engine/server.py`** — Pass `--jinja` when launching llama-server.
  - *Why:* The jinja chat template is required for OpenAI-compatible tool
    calling. It is the default on recent builds but is passed explicitly to
    support older binaries.
- **`llampaca/cli.py`** — `run` command now drives the `Agent` and renders its
  events; added a `--no-tools` flag for plain chat mode.
  - *Why:* Move conversation logic out of the CLI into the reusable agent core;
    let users opt out of tools when they just want to chat.
- **`README.md`** — Documented agent tools, the `--no-tools` flag, the safety
  model, the updated project structure, and ticked the roadmap item.
  - *Why:* Keep user-facing docs in sync with the new capability.

### Removed

- **`llampaca/agent/base.py`** — Deleted the empty `LocalAgent` scaffold.
  - *Why:* Superseded by the fully implemented `Agent` in `agent/loop.py`.

### Added — Developer tooling

- **`.claude/skills/verify/SKILL.md`** — Recipe for building/launching/driving
  Llampaca end-to-end to verify changes against the real llama-server.
  - *Why:* Captures the cold-start verification steps so future sessions don't
    have to rediscover them.
