import os
import sys

# Ensure SSL certificates work in frozen PyInstaller bundles for requests and huggingface_hub
if getattr(sys, "frozen", False):
    try:
        import certifi
        cert_path = certifi.where()
        os.environ["SSL_CERT_FILE"] = cert_path
        os.environ["REQUESTS_CA_BUNDLE"] = cert_path
    except Exception:
        pass

from llampaca.cli import main

if __name__ == "__main__":
    # Explicit list of CLI subcommands supported by Llampaca
    KNOWN_COMMANDS = {
        "gui", "run", "serve", "init", "models", "config",
        "integrations", "mcp", "generate-image", "history",
        "--help", "-h", "--version"
    }

    # When double-clicked from macOS Finder or launched by macOS LaunchServices,
    # macOS passes extra flags (e.g., -psn_..., -B, -NSDocumentRevisionsDebugMode).
    # If the first argument is not a known CLI command, default to launching the GUI dashboard directly.
    if len(sys.argv) <= 1 or (len(sys.argv) >= 2 and sys.argv[1] not in KNOWN_COMMANDS):
        sys.argv = [sys.argv[0], "gui"]

    main()
