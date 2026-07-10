# 🦙 Llampaca

> Run your own AI agent locally, for free, on your machine. No cloud. No subscriptions. You own it, and you'll be happy.

Llampaca is a `llama.cpp`-based local AI assistant written in Python. It self-hosts an inference engine, downloads precompiled binaries automatically, manages GGUF models from Hugging Face, and gives you a fully agentic, MCP-ready personal assistant — all without leaving your terminal.

---

## ✨ Features

- 🔧 **Zero setup** — downloads `llama-server` binaries automatically for your OS and architecture (macOS arm64/Intel, Linux, Windows)
- 🤖 **GPU accelerated** — uses Apple Metal (`-ngl 99`) on Apple Silicon, CUDA on NVIDIA out of the box
- 🔀 **Smart port management** — automatically finds a free port if the default is in use (8080 → 8081 → ...)
- 🧹 **Orphan-safe** — on startup, kills any leftover `llama-server` processes from crashed previous sessions
- 📦 **Model management** — download GGUF models from Hugging Face with a single command, or load your own
- 💬 **Interactive chat** — streaming terminal chat session with any loaded model
- 🏗️ **Agent-ready** — scaffolding for agentic workflows, tool/skill binding, and MCP integration (coming soon)

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

### `llampaca run` options

| Flag | Default | Description |
|---|---|---|
| `--port` | `8080` | Starting port (auto-increments if occupied) |
| `--ctx` | `4096` | Context window size in tokens |
| `--threads` | auto | Number of CPU threads |
| `--gpu` | `-1` (auto) | GPU layers to offload (`0` = CPU only, `-1` = all layers) |

---

## 🎯 Recommended Model Presets

| Preset | Size | Best For |
|---|---|---|
| `qwen3.5-4b-instruct` | ~2.5 GB | **Recommended** — best balance of speed and quality |
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
│   └── client.py         # OpenAI-compatible streaming client
└── agent/
    └── base.py           # Agent scaffold (tool binding, MCP — coming soon)
```

### Application Data

All application data is stored in `~/.llampaca/`:

| Path | Contents |
|---|---|
| `~/.llampaca/bin/` | `llama-server` binary and shared libraries |
| `~/.llampaca/models/` | Downloaded GGUF model files |
| `~/.llampaca/logs/` | Server logs and PID files per port |
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
- [ ] Agentic tool/skill execution loop
- [ ] MCP (Model Context Protocol) integration
- [ ] DeepSearch / web search integration
- [ ] GUI (desktop application packaging)
- [ ] One-click installer (no Python required)

---

## 📄 License

Apache 2.0 — see [LICENSE](LICENSE) for details.
