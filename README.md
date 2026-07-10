# Llampaca
<img width="2000" height="2000" alt="Llampaca" src="https://github.com/user-attachments/assets/c4454143-d766-49e2-902c-93e1b8e72956" />

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
- 🤖 **Agentic tool use** — the model can read/write workspace files, run shell commands (with your confirmation) and fetch web pages, via native OpenAI-compatible tool calling
- 🏗️ **Extensible** — register any Python function as a tool; MCP integration coming soon

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

---

## 📋 CLI Reference

| Command | Description |
|---|---|
| `llampaca init` | Download `llama-server` binaries and set up directories |
| `llampaca models list` | List local GGUF models and available presets |
| `llampaca models download` | Download a model from Hugging Face |
| `llampaca models remove <filename>` | Delete a local model |
| `llampaca run [model_name]` | Start the server and open an interactive chat session |
| `llampaca history list` | List all stored conversation sessions |
| `llampaca history delete <id>` | Delete a conversation session by its ID |

### `llampaca run` options

| Flag | Default | Description |
|---|---|---|
| `--port` | `8080` | Starting port (auto-increments if occupied) |
| `--ctx` | `4096` | Context window size in tokens |
| `--threads` | auto | Number of CPU threads |
| `--gpu` | `-1` (auto) | GPU layers to offload (`0` = CPU only, `-1` = all layers) |
| `--no-tools` | off | Disable agent tools (plain chat mode) |

---

## 🛠️ Agent Tools

During a `llampaca run` session the model can call these built-in tools:

| Tool | Description | Asks confirmation? |
|---|---|---|
| `read_file` | Read a text file from the workspace | No |
| `list_directory` | List files in a workspace directory | No |
| `find_files` | Find files by glob pattern (e.g. `*.py`) across the workspace | No |
| `search_text` | Search text inside workspace files (grep-like, `file:line` results) | No |
| `write_file` | Write/overwrite a file in the workspace | **Yes** |
| `edit_file` | Replace an exact text snippet inside a file (surgical edit) | **Yes** |
| `run_shell_command` | Run a shell command on your machine | **Yes** |
| `web_search` | Search the web (DuckDuckGo, no API key) and return top results | No |
| `fetch_url` | Download a web page as plain text | No |

Safety model:
- **Workspace sandbox** — file tools can only touch paths inside the directory where you launched `llampaca run`; anything else (e.g. `../../etc/passwd`) is rejected
- **Explicit confirmation** — destructive tools show you the exact arguments and only run if you approve; a declined action is reported back to the model so it can adapt
- **Iteration cap** — the tool loop stops after 10 round-trips to prevent runaway behavior

Tool calling uses llama-server's native OpenAI-compatible `tools` API (enabled via `--jinja`). If the loaded model's chat template doesn't support tools, Llampaca warns you and falls back to plain chat automatically.

---

## 🎯 Recommended Model Presets

| Preset | Size | Best For |
|---|---|---|
| `qwen3.5-4b-instruct` | ~4.2 GB | **Recommended** — best balance of speed and quality |
| `qwen2.5-coder-1.5b-instruct` | ~1 GB | Very fast, excellent for coding tasks |
| `llama3.2-3b-instruct` | ~2 GB | General purpose, Meta's lightweight model |

---

## 🗂️ Project Structure

```
llampaca/
├── __init__.py           # Package version
├── __main__.py           # python -m llampaca entrypoint
├── cli.py                # Click-based CLI commands
├── config.py             # Paths, defaults, and model presets
├── engine/
│   ├── downloader.py     # GitHub binary + Hugging Face model downloader
│   ├── server.py         # llama-server subprocess manager (PID tracking, port scanning)
│   └── client.py         # OpenAI-compatible streaming client (text + tool call streaming)
├── agent/
│   └── loop.py           # Agentic execution loop (UI-independent, event-based)
└── tools/
    ├── registry.py       # Tool registry: JSON schema generation from Python functions
    ├── filesystem.py     # read_file / write_file / list_directory (sandboxed)
    ├── shell.py          # run_shell_command (confirmation-gated)
    └── web.py            # fetch_url (HTML → plain text)
```

### Application Data

All application data is stored in `~/.llampaca/`:

| Path | Contents |
|---|---|
| `~/.llampaca/bin/` | `llama-server` binary and shared libraries |
| `~/.llampaca/models/` | Downloaded GGUF model files |
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
    "context_size": 4096,
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
- [ ] MCP (Model Context Protocol) integration
- [ ] GUI (desktop application packaging)
- [ ] One-click installer (no Python required)

---

## 📄 License

Apache 2.0 — see [LICENSE](LICENSE) for details.
