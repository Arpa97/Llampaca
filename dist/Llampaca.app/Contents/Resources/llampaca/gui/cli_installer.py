import os
import sys
import subprocess
from pathlib import Path

CLI_PATH = Path("/usr/local/bin/llampaca")
USER_CLI_PATH = Path.home() / ".local" / "bin" / "llampaca"

def is_cli_installed() -> bool:
    """Check if the llampaca terminal shortcut exists and is linked properly."""
    return CLI_PATH.exists() or USER_CLI_PATH.exists()

def install_cli_symlink() -> dict:
    """Attempt to create the llampaca symlink in /usr/local/bin or ~/.local/bin."""
    if getattr(sys, "frozen", False):
        exec_path = Path(sys.executable)
    else:
        exec_path = Path(__file__).resolve().parent.parent / "cli.py"

    target_dir = Path("/usr/local/bin")
    target_link = CLI_PATH

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if target_link.exists() or target_link.is_symlink():
            target_link.unlink()
        target_link.symlink_to(exec_path)
        return {"status": "ok", "path": str(target_link), "message": "Comando 'llampaca' installato in /usr/local/bin/llampaca"}
    except PermissionError:
        # Fallback to user local bin ~/.local/bin/llampaca
        user_bin = Path.home() / ".local" / "bin"
        user_bin.mkdir(parents=True, exist_ok=True)
        user_link = user_bin / "llampaca"
        if user_link.exists() or user_link.is_symlink():
            user_link.unlink()
        user_link.symlink_to(exec_path)
        return {"status": "ok", "path": str(user_link), "message": f"Installato in {user_link}. Assicurati che ~/.local/bin sia nel tuo PATH."}
    except Exception as e:
        return {"status": "error", "message": f"Impossibile creare il link simbolico: {e}"}
