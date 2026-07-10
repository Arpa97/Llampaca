import os
import json
from pathlib import Path

# Base directory for the application
LLAMPACA_DIR = Path.home() / ".llampaca"
CONFIG_PATH = LLAMPACA_DIR / "config.json"
BIN_DIR = LLAMPACA_DIR / "bin"
MODELS_DIR = LLAMPACA_DIR / "models"
LOGS_DIR = LLAMPACA_DIR / "logs"
DB_PATH = LLAMPACA_DIR / "history.db"

# Recommended model presets
MODEL_PRESETS = {
    "qwen3.5-4b-instruct": {
        "repo": "Benasd/Qwen3.5-4B-Instruct-GGUF",
        # Fixed: old filename "Qwen3.5-4B-Instruct-Q4_K_M.gguf" doesn't exist in the repo (404), causing download failures
        "file": "Qwen3.5-4B-Q8_0.gguf",  # "Qwen3.5-4B-Instruct-Q4_K_M.gguf" (previous, invalid value)
        "description": "Qwen 3.5 4B Instruct - Excellent balance of performance and footprint (Recommended)",
        "default": True
    },
    "qwen2.5-coder-1.5b-instruct": {
        "repo": "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF",
        "file": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
        "description": "Qwen 2.5 Coder 1.5B Instruct - Super fast, outstanding for coding tasks",
        "default": False
    },
    "llama3.2-3b-instruct": {
        "repo": "unsloth/Llama-3.2-3B-Instruct-GGUF",
        "file": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "description": "Llama 3.2 3B Instruct - Meta's lightweight general-purpose model",
        "default": False
    }
}

DEFAULT_CONFIG = {
    "llama_server_path": "",
    "default_model": "qwen3.5-4b-instruct",
    "server_port": 8080,
    "context_size": 4096,
    "n_threads": max(1, os.cpu_count() - 2 if os.cpu_count() else 4),
    "gpu_layers": -1  # -1 means auto (enable metal/cuda if supported)
}

def ensure_dirs():
    """Ensure that all necessary application directories exist."""
    LLAMPACA_DIR.mkdir(parents=True, exist_ok=True)
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

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
