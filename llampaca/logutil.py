"""
Structured logging setup. Call setup_logging() once at app startup.
"""
import logging
import sys
from typing import Optional
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

def setup_logging(
    level: int = logging.INFO,
    log_dir: Optional[Path] = None,
    console: bool = True,
) -> None:
    """
    Configure root logger.
    - console: stdout stream handler (for CLI)
    - log_dir: if given, also write to ~/.llampaca/logs/llampaca.log
    """
    handlers = []
    if console:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handlers.append(stream_handler)
    
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_dir / "llampaca.log", encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,
    )
