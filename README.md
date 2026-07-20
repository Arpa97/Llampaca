# Llampaca
<img width="1024" height="1024" alt="logo_no_frame" src="https://github.com/user-attachments/assets/86e2bb9e-2f75-4996-9a1b-946f13044620" />


> Run your own AI agent locally, for free, on your machine. No cloud. No subscriptions. You own it, and you'll be happy.

Llampaca is a `llama.cpp`-based local AI assistant written in Python. It self-hosts an inference engine, downloads precompiled binaries automatically, manages GGUF models from Hugging Face, and gives you a fully agentic, MCP-ready personal assistant — all without leaving your terminal.

---

## ✨ Features

- 🔧 **Zero setup** — downloads `llama-server` binaries automatically for your OS and architecture (macOS arm64/Intel, Linux, Windows)
- 🤖 **GPU accelerated** — uses Apple Metal (`-ngl 99`) on Apple Silicon, CUDA on NVIDIA out or HIP on AMD out zof the box
- 🔀 **Smart port management** — automatically finds a free port if the default is in use (8080 → 8081 → ...)
- 🧹 **Orphan-safe** — on startup, kills any leftover `llama-server` processes from crashed previous sessions
- 📦 **Model management** — download GGUF models from Hugging Face with a single command, or load your own
- 💬 **Interactive chat** — streaming terminal chat session with any loaded model
- 📎 **File attachments** — attach a PDF, Word or text file to the conversation with `/attach` and ask questions about it
- 🧠 **Personal wiki (persistent memory)** — the assistant remembers durable facts about you across sessions, stored as plain markdown pages you can read and edit yourself
- 🤖 **Agentic tool use** — the model can read/write workspace files, run shell commands (with your confirmation) and fetch web pages, via native OpenAI-compatible tool calling
- 🔌 **MCP Support** — expand your agent's capabilities with external Model Context Protocol (MCP) servers (e.g., email, database, github, slack, etc.)
- 🏗️ **Extensible** — register any Python function as a tool; write custom logic easily

---

## 🚀 Quick Start

### 1. Set up the virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -e .
```

### 2. Initialize — download the inference engine

```bash
llampaca init
```

This will:
- Auto-detect your OS and CPU architecture
- Fetch the latest `llama-server` release from [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp/releases)
- Install the binary and all required shared libraries into `~/.llampaca/bin/`

### 3. Download a model

```bash
# Use a curated preset (interactive menu)
llampaca models download

# Or specify a preset directly
llampaca models download --preset qwen3.5-4b-instruct

# Or download any GGUF from Hugging Face
llampaca models download --repo Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF --file qwen2.5-coder-1.5b-instruct-q4_k_m.gguf
```

### 4. Chat

```bash
llampaca run
```

Type `/exit` or `/quit` to end the session. The server shuts down cleanly and all resources are released.

### 5. Attach a document (optional)

Inside a chat session you can attach a file and ask questions about it:

```
You > /attach ~/Documents/contract.pdf
  [attached] contract.pdf (~1200 tokens, attachment budget 84% used). It will be sent together with your next message.
You > What is the termination notice period?
```

- Supported formats: **PDF** (text layer, no OCR), **Word** (`.docx`), and plain-text files (`.txt`, `.md`, `.csv`, source code, ...)
- **Small files** (up to ~35% of the context window): the extracted text is injected into your next message, so the model reads the document directly — no tools involved
- **Large files**: the document is automatically **indexed for search** (RAG): it is chunked, embedded with a local embedding model, and stored in SQLite; the model then reads it through the `search_documents` tool, retrieving only the passages relevant to each question. Requires the embedding model (`llampaca models download --preset qwen3-embedding-0.6b`, ~640 MB) — a second, lightweight `llama-server` instance starts automatically the first time it is needed
- Indexed documents **survive the session**: resume the conversation and they are searchable again without re-attaching. Deleting the conversation deletes its index.

### 6. Teach it about you (optional)

Llampaca has a **personal wiki**: plain markdown pages in `~/.llampaca/wiki/` that act as persistent memory across conversations.

```
You > /remember I prefer answers in Italian and I use conda, not venv
  [remember] asking the model to store this in the wiki (you will be asked to confirm the write)...
```

- `/remember <fact>` forwards the fact to the model, which picks (or creates) the right page via the `update_wiki_page` tool — the write always asks for your confirmation first
- The index of page names rides in the system prompt, so in every session the model knows what it remembers and reads the relevant page with `read_wiki_page` when needed
- Pages are **plain markdown files**: inspect, correct, or delete them with any editor — no hidden state
- No embeddings involved: a titled page index plus whole-page reads is more reliable than fuzzy retrieval for small local models

---

## 📋 CLI Reference

| Command | Description |
|---|---|
| `llampaca init` | Download `llama-server` binaries and set up directories |
| `llampaca models list` | List local GGUF models and available presets |
| `llampaca models download` | Download a model from Hugging Face |
| `llampaca models remove <filename>` | Delete a local model |
| `llampaca run [model_name]` | Start the server and open an interactive chat session |
| `llampaca gui [model_name]` | Start the standalone desktop app with GUI dashboard |
| `llampaca history list` | List all stored conversation sessions |
| `llampaca history delete <id>` | Delete a conversation session by its ID |
| `llampaca integrations list` | List all configured MCP integrations |
| `llampaca integrations browse` | Search and install servers from the Glama registry |
| `llampaca integrations add <name>` | Manually add a local or external MCP server |
| `llampaca integrations remove <name>` | Uninstall/remove an MCP server |
| `llampaca integrations setup <name> --repo <url>` | Clone a Git repository and run its setup script |
| `llampaca integrations repo list` | List configured MCP registry URLs |
| `llampaca integrations repo add <url>` | Add an MCP registry URL |
| `llampaca integrations repo remove <url>` | Remove an MCP registry URL |

### `llampaca run` options

| Flag | Default | Description |
|---|---|---|
| `--port` | `8080` | Starting port (auto-increments if occupied) |
| `--ctx` | `8192` | Context window size in tokens |
| `--threads` | auto | Number of CPU threads |
| `--gpu` | `-1` (auto) | GPU layers to offload (`0` = CPU only, `-1` = all layers) |
| `--no-tools` | off | Disable agent tools (plain chat mode) |
| `--no-think` | off | Disable the hidden "thinking" phase of reasoning models (e.g. Qwen3): faster responses, slightly lower quality on complex tasks. Only effective when the model's chat template supports the toggle (official Qwen GGUFs do) |

---

## 🛠️ Agent Tools

During a `llampaca run` session the model can call these built-in tools:

| Tool | Description | Asks confirmation? |
|---|---|---|
| `read_file` | Read a text file from the workspace (optionally just a range of lines) | No |
| `list_directory` | List files in a workspace directory | No |
| `find_files` | Find files by glob pattern (e.g. `*.py`) across the workspace | No |
| `search_text` | Search text inside workspace files (grep-like, `file:line` results) | No |
| `write_file` | Write/overwrite a file in the workspace | **Yes** |
| `edit_file` | Replace an exact text snippet inside a file (surgical edit) | **Yes** |
| `delete_path` | Permanently delete a file or directory (refuses workspace root and `.git`) | **Yes** |
| `run_shell_command` | Run a shell command on your machine | **Yes** |
| `web_search` | Search the web (DuckDuckGo, no API key) and return top results | No |
| `fetch_url` | Download a web page as plain text | No |
| `search_documents` | Search inside indexed attachments (only active when the conversation has documents indexed via `/attach`) | No |
| `read_wiki_page` | Read a page of the persistent personal wiki (`~/.llampaca/wiki/`) | No |
| `update_wiki_page` | Create/overwrite a wiki page with a durable fact about the user | **Yes** |

Safety model:
- **Workspace sandbox** — file tools can only touch paths inside the directory where you launched `llampaca run`; anything else (e.g. `../../etc/passwd`) is rejected
- **Explicit confirmation** — destructive tools show you the exact arguments and only run if you approve; a declined action is reported back to the model so it can adapt
- **Iteration cap** — the tool loop stops after 10 round-trips to prevent runaway behavior

Tool calling works with any model, via two modes picked automatically:

- **Native** — for models whose chat template supports tools (e.g. the Qwen family): uses llama-server's OpenAI-compatible `tools` API with structured `tool_calls`. Most reliable.
- **Prompt-based** — for models without tool support in their template (e.g. Gemma): tool definitions are injected into the system prompt and the model replies with a JSON object that Llampaca parses itself.

The active mode is shown in the session header (`Tools enabled (native): ...`).

---

## 🔌 Model Context Protocol (MCP)

Llampaca supports the **Model Context Protocol (MCP)**, allowing you to connect external tools (like databases, email clients, custom CLI utilities, or remote APIs) directly into your local agent.

MCP servers are spawned dynamically in the background via stdio streams when starting a chat session. All active tools from the connected MCP servers are automatically registered in the agent's Tool Registry.

### 🔍 How to use it

1. **Browse and Install integrations** from the official Glama Registry:
   ```bash
   llampaca integrations browse
   ```
   This interactive prompt allows you to search for integrations (e.g., `gmail`, `sqlite`, `postgres`), clone their repository, build them, and configure them.

2. **Manually Add an integration**:
   If you have a local node or python script that runs as an MCP server, you can register it:
   ```bash
   llampaca integrations add my-custom-server
   ```
   Llampaca will prompt you for the command to execute (e.g., `npx`, `python3`, `node`), arguments (e.g., `-y`, `@modelcontextprotocol/server-sqlite`), and any environment variables (e.g., API keys, database URLs).

3. **Check Configured integrations**:
   ```bash
   llampaca integrations list
   ```

4. **Run the Chat**:
   Once registered, start chat as normal. The agent will discover all tools and ask for confirmation before calling any write/edit operations:
   ```bash
   llampaca run
   ```

All configurations are saved cleanly in `~/.llampaca/mcp_config.json`.

---

## 🖥️ GUI Desktop Dashboard

Llampaca includes a standalone GUI desktop application built with `pywebview` and Vue.js. It features a complete dashboard to interact with your agent and configure your local environment visually.

### How to start the GUI
Start the dashboard with the following command:
```bash
llampaca gui
```

You can pass execution options just like `llampaca run`:
```bash
llampaca gui [model_name] [--port port] [--ctx ctx] [--threads threads] [--gpu gpu]
```

### Key Modules

#### 💬 Chat & Session Manager
* Stream agent outputs word-by-word with markdown formatting.
* List, load, and delete conversation history persisted in the local SQLite database.

#### 📦 GGUF Model Catalogue
* **Recommended Presets**: View pre-configured presets showing exact file sizes in GB. Download them in one click.
* **Hugging Face Search**: Search Hugging Face dynamically for popular GGUF repositories, select a specific file from a dropdown showing its exact size in GB, and download it.
* **Background Downloads**: Track download progress in real-time with progress bars. The catalog automatically reloads and unlocks the model once complete.
* **Dynamic Modals**: Switch active models at runtime with automatic `llama-server` restarts.

#### 🔌 MCP Integrations Manager
* **Live Connection States**: View connected/disconnected statuses for configured MCP servers along with the exact count of registered tools.
* **Glama MCP Registry**: Browse and search integrations in real-time using Glama APIs.
* **Variables Configuration Overlay Modal**: Fill out environment variables and credential requirements (like API keys) through dynamic graphical forms mapping schemas directly.
* **Runtime Hot-Reloading**: Automatically connect, spawn, and load MCP tools without having to restart the LLM inference server.

#### ⚙️ Settings Panel
* Update inference configuration settings (port, context size, CPU threads, and GPU layer offloading) at runtime.

---

## 🎯 Recommended Model Presets

| Preset | Size | Best For |
|---|---|---|
| `qwen3.5-4b-instruct` | ~4.2 GB | **Recommended** — best balance of speed and quality |
| `qwen2.5-coder-1.5b-instruct` | ~1 GB | Very fast, excellent for coding tasks |
| `llama3.2-3b-instruct` | ~2 GB | General purpose, Meta's lightweight model |
| `qwen3-embedding-0.6b` | ~640 MB | Multilingual embedder for document search/RAG (not a chat model) |

---

## 🗂️ Project Structure

```
llampaca/
├── __init__.py           # Package version
├── __main__.py           # python -m llampaca entrypoint
├── cli.py                # Click-based CLI commands
├── config.py             # Paths, defaults, and model presets
├── attachments.py        # /attach: PDF/Word/text extraction and context budgeting
├── rag.py                # RAG core: chunking, vector serialization, cosine top-k
├── wiki.py               # Personal wiki core: markdown pages, slugs, prompt index
├── engine/
│   ├── downloader.py     # GitHub binary + Hugging Face model downloader
│   ├── server.py         # llama-server subprocess manager (chat + embedding modes)
│   ├── client.py         # OpenAI-compatible streaming client (text, tool calls, embeddings)
│   └── embedding.py      # EmbeddingService: lazy embedding-server lifecycle + indexing
├── agent/
│   └── loop.py           # Agentic execution loop (UI-independent, event-based)
└── tools/
    ├── registry.py       # Tool registry: JSON schema generation from Python functions
    ├── filesystem.py     # read_file / write_file / list_directory (sandboxed)
    ├── shell.py          # run_shell_command (confirmation-gated)
    ├── web.py            # fetch_url (HTML → plain text)
    ├── documents.py      # search_documents (RAG retrieval over indexed attachments)
    └── wiki.py           # read_wiki_page / update_wiki_page (persistent memory)
```

### Application Data

All application data is stored in `~/.llampaca/`:

| Path | Contents |
|---|---|
| `~/.llampaca/bin/` | `llama-server` binary and shared libraries |
| `~/.llampaca/models/` | Downloaded GGUF model files |
| `~/.llampaca/wiki/` | Personal wiki: markdown pages of persistent memory (`/remember`) |
| `~/.llampaca/logs/` | Server logs and PID files per port |
| `~/.llampaca/history.db` | SQLite database storing conversation history |
| `~/.llampaca/config.json` | User configuration |

---

## ⚙️ Configuration

`~/.llampaca/config.json` is created automatically on first run:

```json
{
    "llama_server_path": "",
    "default_model": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
    "server_port": 8080,
    "context_size": 8192,
    "n_threads": 6,
    "gpu_layers": -1
}
```

- Set `"llama_server_path"` to use a custom binary instead of the one downloaded by `llampaca init`
- Set `"gpu_layers": 0` to run fully on CPU

---

## 🗄️ Database & Schema

Llampaca stores all your local conversation histories in a local SQLite database at `~/.llampaca/history.db`.

### Tables

#### 1. `conversations`
Stores conversation sessions.
- `id` (TEXT, PRIMARY KEY): Unique string UUID.
- `title` (TEXT): Title of the conversation. Automatically set using a truncated snippet of the first user message if left default.
- `model_name` (TEXT): Name of the GGUF model file used.
- `created_at` (TIMESTAMP): Date and time created.
- `updated_at` (TIMESTAMP): Date and time updated.

#### 2. `messages`
Stores messages for each conversation.
- `id` (INTEGER, PRIMARY KEY AUTOINCREMENT): Message identifier.
- `conversation_id` (TEXT): Foreign key referencing `conversations(id)` with `ON DELETE CASCADE`.
- `role` (TEXT): Role (`system`, `user`, `assistant`, `tool`).
- `content` (TEXT): The message text.
- `created_at` (TIMESTAMP): Date and time created.

---

## 🛡️ Process Safety

Llampaca automatically:
- **Scans for orphaned processes** at startup by reading `.pid` files in `~/.llampaca/logs/`
- **Kills stale processes** left over from previous crashed sessions before starting a new one
- **Removes PID files** on clean shutdown via `stop()`
- **Gracefully terminates** the server on `/exit`, `/quit`, or `Ctrl+C`

---

## 🗺️ Roadmap

- [x] Automatic `llama-server` binary download (all platforms)
- [x] Model download from Hugging Face with progress bars
- [x] Interactive terminal chat with streaming
- [x] Automatic port conflict resolution
- [x] Orphaned process cleanup
- [x] Conversation history — persistent storage of sessions via SQLite, with full CRUD API exposed by `llama-server` (create/list/load/delete conversations)
- [x] Agentic tool/skill execution loop (filesystem, shell, web tools with confirmation gating)
- [x] Web search integration (DuckDuckGo, no API key) — deeper "DeepSearch" (multi-step research) still to come
- [x] File attachments (`/attach` — PDF/Word/text): direct injection for small files, automatic RAG (local embeddings + `search_documents` tool) for documents larger than the context budget
- [x] Personal wiki (`/remember` + `read_wiki_page`/`update_wiki_page`): persistent cross-session memory as plain markdown pages, with the page index injected in the system prompt
- [x] MCP (Model Context Protocol) integration
- [x] GUI (desktop application packaging)
- [ ] One-click installer (no Python required)

---

## 📄 License

Apache 2.0 — see [LICENSE](LICENSE) for details.
