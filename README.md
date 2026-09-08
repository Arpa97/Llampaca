# Llampaca
<img width="1024" height="1024" alt="logo_no_frame" src="https://github.com/user-attachments/assets/86e2bb9e-2f75-4996-9a1b-946f13044620" />


> Run your own AI agent locally, for free, on your machine. No cloud. No subscriptions. You own it, and you'll be happy.

Llampaca is a `llama.cpp`-based local AI assistant written in Python. It self-hosts an inference engine, downloads precompiled binaries automatically, manages GGUF models from Hugging Face, and gives you a fully agentic, MCP-ready personal assistant — all without leaving your terminal or GUI.

To download llampaca alpha click [here] (https://github.com/Arpa97/Llampaca/releases/tag/llampaca)

---

## ✨ Features

- 🔧 **Zero setup** — downloads `llama-server` binaries automatically for your OS and architecture (macOS arm64/Intel, Linux, Windows)
- 🤖 **GPU accelerated** — uses Apple Metal (`-ngl 99`) on Apple Silicon, CUDA on NVIDIA or HIP on AMD out of the box
- ⚡ **Speculative Decoding** — accelerate text generation speeds by pairing a primary LLM with a smaller draft model (`-md` / `-ngld`)
- 💾 **SSD Offloading** — offload large model weights using mmap memory mapping when system RAM or VRAM is limited
- 🔍 **Deep Search** — trigger deep multi-query web research and synthesis directly in chat sessions
- 🚀 **FastAPI Backend** — high-performance asynchronous REST API powering headless server mode and desktop GUI
- ⏱️ **Prompt Cache Warmup** — pre-fills system prompt cache tokens (~7,600 tokens) on server startup to eliminate first-token latency
- 🔀 **Smart port management** — automatically finds a free port if the default is in use (8080 → 8081 → ...)
- 🧹 **Orphan-safe** — on startup, kills any leftover `llama-server` processes from crashed previous sessions
- 📦 **Model management** — download GGUF models for text chat, embedding/RAG, and image generation from Hugging Face with a single command, or load your own
- 🎨 **Local Image Generation** — generate images locally using GGUF Stable Diffusion models (`sdxl-turbo-q4`, `flux-schnell-q4`) via CLI or directly inside agent chat sessions with live image previews
- 💬 **Interactive chat** — streaming terminal chat session and standalone GUI desktop application with any loaded model
- 📎 **File attachments** — attach a PDF, Word or text file to the conversation with `/attach` and ask questions about it (with automatic local RAG vector indexing)
- 🧠 **Personal wiki (persistent memory)** — the assistant remembers durable facts about you across sessions, stored as plain markdown pages in `~/.llampaca/wiki/`
- ⚡ **Markdown Skills** — define reusable domain instructions, coding standards, or workflows in `~/.llampaca/skills/` that the agent can read and maintain
- 🤖 **Agentic tool use** — read/write workspace files, execute shell commands (with user confirmation), fetch web pages, install isolated Python/Node packages, and generate images via native OpenAI-compatible tool calling
- 🔌 **MCP Support** — expand your agent's capabilities with external Model Context Protocol (MCP) servers (e.g., email, database, github, slack, etc.)
- 🏗️ **Extensible** — drop custom Python functions into `~/.llampaca/custom_tools/` to register new tools effortlessly

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

### 7. Generate images locally (optional)

```bash
# Download an image generation preset (Flash or High Quality)
llampaca models download --preset sdxl-turbo-q4

# Generate an image from the CLI
llampaca generate-image "a cute llama programmer sitting in front of a retro computer, digital art"

# Or ask the agent during chat or in the GUI:
# You > "Draw a sunset over cyberpunk mountains"
```

---

## 📋 CLI Reference

| Command | Description |
|---|---|
| `llampaca init` | Download `llama-server` binaries and set up directories |
| `llampaca models list` | List local GGUF models and available presets |
| `llampaca models download` | Download a model from Hugging Face |
| `llampaca models remove <filename>` | Delete a local model |
| `llampaca generate-image <prompt>` | Generates a local image from a prompt using Stable Diffusion GGUF models (`--quality fast\|high`, `--output <path>`) |
| `llampaca run [model_name]` | Start the server and open an interactive chat session |
| `llampaca gui [model_name]` | Start the standalone desktop app with GUI dashboard |
| `llampaca serve [model_name]` | Start the full Llampaca backend (`llama-server` + HTTP API) headless, without a GUI |
| `llampaca mcp` | Run Llampaca itself as an MCP server (stdio), exposing its built-in **read-only** tools to VSCode or Claude Desktop (see note below) |
| `llampaca history list` | List all stored conversation sessions |
| `llampaca history delete <id>` | Delete a conversation session by its ID |
| `llampaca integrations list` | List all configured MCP integrations |
| `llampaca integrations browse` | Search and install servers from the Smithery.ai registry |
| `llampaca integrations add <name>` | Manually add a local or external MCP server |
| `llampaca integrations remove <name>` | Uninstall/remove an MCP server |
| `llampaca integrations setup <name> --repo <url>` | Clone a Git repository and run its setup script |
| `llampaca integrations repo list` | List configured MCP registry URLs |
| `llampaca integrations repo add <url>` | Add an MCP registry URL |
| `llampaca integrations repo remove <url>` | Remove an MCP registry URL |

### `llampaca run`, `gui` & `serve` options

| Flag | Default | Description |
|---|---|---|
| `--port` | `8080` | Starting port for `llama-server` (auto-increments if occupied) |
| `--api-port` | `8090` | Port for FastAPI REST API server (only for `llampaca serve`) |
| `--ctx` | `8192` | Context window size in tokens |
| `--threads` | auto | Number of CPU threads to use |
| `--gpu` | `-1` (auto) | GPU layers to offload (`0` = CPU only, `-1` = all layers) |
| `--no-tools` | off | Disable agent tools (plain chat mode) |
| `--no-think` | off | Disable the hidden "thinking" phase of reasoning models (e.g. Qwen3): faster responses, slightly lower quality on complex tasks |
| `--draft-model` | none | Speculative decoding: GGUF filename of smaller draft model to accelerate inference |
| `--draft-gpu` | `-1` (auto) | GPU layers to offload for the draft model |
| `--ssd-offload` | off | Enable SSD Offloading via mmap memory mapping for massive models |

---

## 🛠️ Agent Tools

During a `llampaca run` or GUI session the model can call these built-in tools:

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
| `generate_image` | Generate a new image from a prompt using local Stable Diffusion GGUF engine | No |
| `read_wiki_page` | Read a page of the persistent personal wiki (`~/.llampaca/wiki/`) | No |
| `update_wiki_page` | Create/overwrite a wiki page with a durable fact about the user | **Yes** |
| `read_skill_page` | Read instructions from a Markdown skill page (`~/.llampaca/skills/`) | No |
| `update_skill_page` | Create or edit a domain Markdown skill file | **Yes** |
| `install_package` | Install Python (venv) or Node.js (`~/.llampaca/node_modules`) packages | **Yes** |
| `search_documents` | Search inside indexed attachments (only active when the conversation has documents indexed via `/attach`) | No |

Safety model:
- **Workspace sandbox** — file tools can only touch paths inside the directory where you launched `llampaca run`; anything else (e.g. `../../etc/passwd`) is rejected
- **Explicit confirmation** — destructive tools show you the exact arguments and only run if you approve; a declined action is reported back to the model so it can adapt
- **Iteration cap** — the tool loop stops after 10 round-trips to prevent runaway behavior
- **MCP server mode** — `llampaca mcp` runs over stdio, where Llampaca has no way to show you a confirmation prompt. The confirmation-gated tools (`run_shell_command`, `write_file`, `edit_file`, `delete_path`, `update_wiki_page`, `install_package`) are therefore **not exposed** in that mode; only the read-only ones are. If you trust your MCP client to ask for approval itself, set `LLAMPACA_MCP_ALLOW_CONFIRMED_TOOLS=1` to expose them — they are then flagged as destructive so a compliant client prompts you

Tool calling works with any model, via two modes picked automatically:

- **Native** — for models whose chat template supports tools (e.g. the Qwen family): uses llama-server's OpenAI-compatible `tools` API with structured `tool_calls`. Most reliable.
- **Prompt-based** — for models without tool support in their template (e.g. Gemma): tool definitions are injected into the system prompt and the model replies with a JSON object that Llampaca parses itself.

---

## 🎨 Local Image Generation

Llampaca includes built-in support for generating images locally using Stable Diffusion GGUF models:

- **Presets**:
  - ⚡ `sdxl-turbo-q4`: Flash generation in 1-2 steps (~1.6 GB).
  - 🎨 `flux-schnell-q4`: High quality with photorealistic details in 4 steps (~3.2 GB).
- **CLI command**:
  ```bash
  llampaca generate-image "a futuristic llama workspace, digital art" --quality high
  ```
- **In-Chat & GUI Integration**:
  Ask the agent to generate or draw an image during a conversation; the model invokes `generate_image` and displays an inline preview in the GUI dashboard or clickable links in the CLI. Output images are saved in `~/Documents/LlampacaDocs/Images/`.

---

## ⚡ Skills & Custom Tools

Llampaca can be extended without writing complex plugins:

### 1. Markdown Skills (`~/.llampaca/skills/`)
Store domain instructions, prompt templates, or workflow guides as plain `.md` files. The agent indexes all available skill files in its system prompt and reads the full content on-demand via `read_skill_page` or creates new ones via `update_skill_page`.

### 2. Custom Python Tools (`~/.llampaca/custom_tools/`)
Drop any Python script with standard functions into `~/.llampaca/custom_tools/`. Llampaca automatically inspects, builds JSON schemas for, and registers these functions into the agent's tool registry at startup.

---

## 🔌 Model Context Protocol (MCP)

Llampaca supports the **Model Context Protocol (MCP)**, allowing you to connect external tools (like databases, email clients, custom CLI utilities, or remote APIs) directly into your local agent.

MCP servers are spawned dynamically in the background via stdio streams when starting a chat session. All active tools from the connected MCP servers are automatically registered in the agent's Tool Registry.

### 🔍 How to use it

1. **Browse and Install integrations** from the official Smithery.ai Registry:
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

Llampaca includes a standalone GUI desktop application built with `pywebview`, Vue.js, and FastAPI. It features a complete dashboard to interact with your agent and configure your local environment visually.

### How to start the GUI
Start the dashboard with the following command:
```bash
llampaca gui
```

You can pass execution options just like `llampaca run`:
```bash
llampaca gui [model_name] [--port port] [--ctx ctx] [--threads threads] [--gpu gpu] [--draft-model draft] [--draft-gpu draft_gpu] [--ssd-offload]
```

### Key Modules & Capabilities

#### 🚀 Async Startup & Splash Screen
* Instant splash screen while `llama-server` initializes and pre-warms prompt caches in the background.

#### 💬 Chat & Session Manager
* Stream agent outputs word-by-word with markdown formatting, including live tool-call and "thinking" indicators.
* **Deep Search toggle** — trigger deep multi-query web research directly from the chat UI header.
* **Native Context Menu & Copy/Paste** — native right-click context menu and copy-paste support inside the webview desktop app.
* List, load, and delete conversation history persisted in the local SQLite database.
* **File attachments & Image previews** — attach PDF/Word/text files directly in chat and view rendered generated images inline.
* **Stop button** — interrupt an in-progress answer at any time; the partial output is kept and the backend is cancelled cleanly.
* **Context & speed indicator** — shows context usage and token speed in real-time.

#### ⚙️ Settings & Engine Customization
* Configure primary and draft models for Speculative Decoding.
* Set GPU offloading layers independently for primary and draft models.
* Toggle SSD Offloading (mmap), reasoning thinking mode (`no-think`), context window sizes, and CPU thread counts.
* Real-time loading overlay during prompt cache warmup (~7,600 pre-filled tokens).

#### 🧠 Personal Wiki & Skills Manager
* Visual editor for persistent memory wiki pages (`~/.llampaca/wiki/`) and Markdown domain skills (`~/.llampaca/skills/`).

#### 📦 GGUF Model Catalogue
* Download LLM chat models, RAG embedding models, and Stable Diffusion image models in one click.

#### 🔌 MCP Integrations Manager
* Browse Smithery.ai, configure env variables, and monitor active MCP server connections.

---

## 🎯 Recommended Model Presets

| Preset | Size | Kind | Best For |
|---|---|---|---|
| `qwen3.5-4b-instruct` | ~4.2 GB | Chat | **Recommended** — best balance of speed and quality |
| `qwen2.5-coder-1.5b-instruct` | ~1 GB | Chat | Very fast, excellent for coding tasks |
| `llama3.2-3b-instruct` | ~2 GB | Chat | General purpose, Meta's lightweight model |
| `qwen3-embedding-0.6b` | ~640 MB | Embedding | Multilingual embedder for document search/RAG (not a chat model) |
| `sdxl-turbo-q4` | ~1.6 GB | Image | Flash image generation — ultra-fast 1-2 step generations |
| `flux-schnell-q4` | ~3.2 GB | Image | High quality image generation — 4 steps with rich photorealistic detail |

---

## 🗂️ Project Structure

```
llampaca/
├── __init__.py           # Package version
├── __main__.py           # python -m llampaca entrypoint
├── cli.py                # Click-based CLI commands
├── cli_chat.py           # Interactive terminal chat loop & slash commands (/attach, /remember, /deepsearch)
├── config.py             # Paths, defaults, and model presets
├── attachments.py        # /attach: PDF/Word/text extraction and context budgeting
├── packages.py           # Venv & Node package installer core
├── rag.py                # RAG core: chunking, vector serialization, cosine top-k
├── skills.py             # Markdown skills core: instruction pages, slugs, prompt index
├── wiki.py               # Personal wiki core: markdown pages, slugs, prompt index
├── engine/
│   ├── downloader.py     # GitHub binary + Hugging Face model downloader
│   ├── server.py         # llama-server subprocess manager (chat, draft model, embedding)
│   ├── client.py         # OpenAI-compatible streaming client (text, tool calls, embeddings)
│   ├── db.py             # SQLite conversation & message storage with schema migrations
│   ├── embedding.py      # EmbeddingService: lazy embedding-server lifecycle + indexing
│   ├── image_generator.py # Stable Diffusion (sd.cpp) engine wrapper
│   ├── mcp_client.py     # Stdio MCP client wrapper
│   └── mcp_installer.py  # Smithery.ai registry & MCP setup helpers
├── agent/
│   └── loop.py           # Agentic execution loop (UI-independent, event-based)
├── gui/
│   ├── server.py         # FastAPI web server & PyWebview wrapper
│   ├── routes.py         # FastAPI REST API endpoints (chat, models, settings, history)
│   ├── agent_manager.py  # Async manager for server lifecycle & prompt cache pre-filling
│   ├── splash.html       # Desktop GUI startup splash screen
│   └── index.html        # Vue.js desktop GUI dashboard
└── tools/
    ├── registry.py       # Tool registry: JSON schema generation from Python functions
    ├── filesystem.py     # read_file / write_file / edit_file / delete_path (sandboxed)
    ├── shell.py          # run_shell_command (confirmation-gated)
    ├── web.py            # fetch_url / web_search
    ├── documents.py      # search_documents (RAG retrieval over indexed attachments)
    ├── wiki.py           # read_wiki_page / update_wiki_page (persistent memory)
    ├── skills.py         # read_skill_page / update_skill_page
    ├── packages.py       # install_package (pip/npm isolated)
    └── image.py          # generate_image (local SDXL/FLUX GGUF)
```

### Application Data

All application data is stored in `~/.llampaca/`:

| Path | Contents |
|---|---|
| `~/.llampaca/bin/` | `llama-server` binary and shared libraries |
| `~/.llampaca/models/` | Downloaded GGUF model files (Chat, Embedding, Image) |
| `~/.llampaca/wiki/` | Personal wiki: markdown pages of persistent memory (`/remember`) |
| `~/.llampaca/skills/` | Markdown skills: domain instructions and prompt workflows |
| `~/.llampaca/custom_tools/` | Custom Python tools auto-registered at startup |
| `~/.llampaca/installed_packages.json` | Registry of installed Python and Node.js packages |
| `~/.llampaca/logs/` | Server logs and PID files per port |
| `~/.llampaca/history.db` | SQLite database storing conversation history |
| `~/.llampaca/config.json` | User configuration |
| `~/.llampaca/mcp_config.json` | MCP integrations configuration |

---

## ⚙️ Configuration

`~/.llampaca/config.json` is created automatically on first run:

```json
{
    "llama_server_path": "",
    "default_model": "Qwen3.5-4B-Q8_0.gguf",
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
- [x] Conversation history — persistent storage of sessions via SQLite
- [x] Agentic tool/skill execution loop (filesystem, shell, web tools with confirmation gating)
- [x] Web search integration (DuckDuckGo, no API key) & Deep Search
- [x] File attachments (`/attach` — PDF/Word/text) with automatic RAG (local embeddings + `search_documents` tool)
- [x] Personal wiki (`/remember` + `read_wiki_page`/`update_wiki_page`): persistent cross-session memory
- [x] Local Image Generation (`sd.cpp` / Stable Diffusion GGUF integration)
- [x] Markdown Skills system (`~/.llampaca/skills/`)
- [x] Custom Python Tools (`~/.llampaca/custom_tools/`)
- [x] Package Management (isolated Python & Node.js packages)
- [x] MCP (Model Context Protocol) integration
- [x] GUI (desktop application packaging with pywebview & Vue.js)
- [x] High-performance FastAPI REST API backend architecture
- [x] Speculative Decoding with draft models (`-md` / `-ngld`)
- [x] SSD Offloading via mmap memory mapping
- [x] Synchronous prompt cache warmup & async GUI splash screen
- [ ] One-click binary installer (no Python required)

---

## 📄 License

Apache 2.0 — see [LICENSE](LICENSE) for details.
