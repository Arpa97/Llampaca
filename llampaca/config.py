import os
import json
from pathlib import Path

# Rough tokens-per-character ratio used for budget estimates everywhere in
# the codebase (agent history trimming, attachment budgets, RAG chunk
# sizing). Exact token counts would require a round-trip to the server's
# /tokenize endpoint; chars/4 is standard, cheap, and accurate enough for
# threshold decisions. Lives here — the dependency-free leaf module — so
# low-level modules (rag, attachments) can share it with the agent without
# import cycles.
CHARS_PER_TOKEN = 4

# Single source of truth for the default CHAT context window (in tokens).
# Change it HERE and every entry point picks it up: DEFAULT_CONFIG seeds new
# configs with it, and every `config.get("context_size", ...)` fallback across
# the codebase (CLI run/serve/gui, the GUI backend, the Agent) references this
# constant instead of a hard-coded number — so the default can never drift
# between the terminal and the GUI again.
DEFAULT_CONTEXT_SIZE = 8192

# The embedding server's context is deliberately INDEPENDENT and much smaller:
# it is sized for a single ~500-token chunk served to one slot, never for a
# conversation. It is intentionally NOT tied to DEFAULT_CONTEXT_SIZE — raising
# the chat window must not bloat the embedder's KV cache.
EMBEDDING_CONTEXT_SIZE = 4096

# Base directory for the application
LLAMPACA_DIR = Path.home() / ".llampaca"
CONFIG_PATH = LLAMPACA_DIR / "config.json"
BIN_DIR = LLAMPACA_DIR / "bin"
MODELS_DIR = LLAMPACA_DIR / "models"
LOGS_DIR = LLAMPACA_DIR / "logs"
DB_PATH = LLAMPACA_DIR / "history.db"
# The personal wiki: plain markdown pages the model (and the user) can read
# and update across sessions. Lives in the app data dir — NOT in the launch
# workspace — because it is the user's memory, shared by every project.
WIKI_DIR = LLAMPACA_DIR / "wiki"

# Recommended model presets.
# The "kind" field separates chat models (loaded by `llampaca run`) from
# embedding models (loaded by the RAG indexing pipeline); presets without a
# "kind" are chat models. Embedding presets carry the model-specific details
# the pipeline must respect:
#   - "pooling": llama-server's --pooling mode for this architecture
#     (wrong pooling produces meaningless vectors WITHOUT any error);
#   - "query_prefix"/"document_prefix": asymmetric instruction prefixes.
#     Queries and documents must be embedded with *different* framing for
#     retrieval-tuned models — omitting them silently degrades similarity
#     quality, so they live here, next to the model they belong to.
MODEL_PRESETS = {
    "qwen3.5-4b-instruct": {
        "repo": "Benasd/Qwen3.5-4B-Instruct-GGUF",
        # Fixed: old filename "Qwen3.5-4B-Instruct-Q4_K_M.gguf" doesn't exist in the repo (404), causing download failures
        "file": "Qwen3.5-4B-Q8_0.gguf",  # "Qwen3.5-4B-Instruct-Q4_K_M.gguf" (previous, invalid value)
        "description": "Qwen 3.5 4B Instruct - Excellent balance of performance and footprint (Recommended)",
        "default": True,
        "size_gb": 4.17
    },
    "qwen2.5-coder-1.5b-instruct": {
        "repo": "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF",
        "file": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
        "description": "Qwen 2.5 Coder 1.5B Instruct - Super fast, outstanding for coding tasks",
        "default": False,
        "size_gb": 1.04
    },
    "llama3.2-3b-instruct": {
        "repo": "unsloth/Llama-3.2-3B-Instruct-GGUF",
        "file": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "description": "Llama 3.2 3B Instruct - Meta's lightweight general-purpose model",
        "default": False,
        "size_gb": 2.02
    },
    "qwen3-embedding-0.6b": {
        "repo": "Qwen/Qwen3-Embedding-0.6B-GGUF",
        "file": "Qwen3-Embedding-0.6B-Q8_0.gguf",
        "description": "Qwen 3 Embedding 0.6B - Multilingual embedder for document search/RAG (not a chat model)",
        "default": False,
        "kind": "embedding",
        # Qwen3-Embedding uses last-token pooling (its GGUF is trained that
        # way); llama-server's default (mean/none) would return garbage.
        "pooling": "last",
        # Qwen3-Embedding retrieval format: the QUERY carries an instruction,
        # the documents are embedded bare. Wording taken from the model card.
        "query_prefix": (
            "Instruct: Given a web search query, retrieve relevant passages "
            "that answer the query\nQuery: "
        ),
        "document_prefix": "",
    }
}

DEFAULT_CONFIG = {
    "llama_server_path": "",
    "default_model": "qwen3.5-4b-instruct",
    "server_port": 8080,
    "context_size": DEFAULT_CONTEXT_SIZE,
    "n_threads": max(1, os.cpu_count() - 2 if os.cpu_count() else 4),
    "gpu_layers": -1,  # -1 means auto (enable metal/cuda if supported)
    "mcp_servers": {},
    # Embedding (RAG) settings. embedding_model may be a preset name or a
    # GGUF filename in MODELS_DIR, same resolution rules as default_model.
    # The embedding server starts on its own port range so it never races
    # the chat server's auto-increment scan (8080, 8081, ...).
    "embedding_model": "qwen3-embedding-0.6b",
    "embedding_port": 8180,
    # GPU layers for the embedding server, resolved INDEPENDENTLY from the
    # chat model's "gpu_layers" (they are two separate llama-server
    # processes and can run on different backends). Default 0 (CPU-only):
    # a 0.6B model embeds a handful of short chunks fast enough on CPU
    # alone, and running it off Metal/CUDA frees the GPU and the thermal
    # budget for the chat model, which is where GPU offload actually
    # matters for interactive token-generation speed. Set to -1 (auto) or
    # a specific layer count here to offload it too, e.g. on a machine
    # with GPU/RAM to spare.
    "embedding_gpu_layers": 0
}


def get_preset_for_file(filename: str) -> dict | None:
    """
    Reverse lookup: the preset entry whose "file" matches a GGUF filename,
    or None. Used to recover per-model metadata (pooling, prefixes) when
    the config stores a plain filename instead of a preset name.
    """
    for preset in MODEL_PRESETS.values():
        if preset["file"] == filename:
            return preset
    return None

def ensure_dirs():
    """Ensure that all necessary application directories exist."""
    LLAMPACA_DIR.mkdir(parents=True, exist_ok=True)
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_DIR.mkdir(parents=True, exist_ok=True)

def load_config() -> dict:
    """Load configuration from the config file, creating it if it doesn't exist."""
    ensure_dirs()
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        # Ensure any missing keys are populated from defaults
        updated = False
        for k, v in DEFAULT_CONFIG.items():
            if k not in config:
                config[k] = v
                updated = True
        if updated:
            save_config(config)
        return config
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(config: dict):
    """Save configuration to the config file."""
    ensure_dirs()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

MCP_CONFIG_PATH = LLAMPACA_DIR / "mcp_config.json"
DEFAULT_REGISTRY = "https://glama.ai/api/mcp/v1/servers"

def load_mcp_config() -> dict:
    """Load configuration from mcp_config.json, creating it if it doesn't exist."""
    ensure_dirs()
    if not MCP_CONFIG_PATH.exists():
        initial_config = {
            "mcp_registries": [DEFAULT_REGISTRY],
            "mcp_servers": {}
        }
        save_mcp_config(initial_config)
        return initial_config
    
    try:
        with open(MCP_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        # Ensure registries and servers keys exist
        updated = False
        if "mcp_registries" not in config or not isinstance(config["mcp_registries"], list):
            config["mcp_registries"] = [DEFAULT_REGISTRY]
            updated = True
        if "mcp_servers" not in config or not isinstance(config["mcp_servers"], dict):
            config["mcp_servers"] = {}
            updated = True
            
        if updated:
            save_mcp_config(config)
        return config
    except Exception:
        return {
            "mcp_registries": [DEFAULT_REGISTRY],
            "mcp_servers": {}
        }

def save_mcp_config(config: dict):
    """Save configuration to mcp_config.json."""
    ensure_dirs()
    with open(MCP_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
