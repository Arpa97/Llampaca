"""
Process-wide server registry. Both CLI and GUI use this to discover
and manage the active llama-server instances without circular imports.
"""
from __future__ import annotations
from typing import Optional, Dict
import threading

_lock = threading.Lock()
_chat_server: Optional["LlamaServer"] = None  # type: ignore
_embed_server: Optional["LlamaServer"] = None  # type: ignore


def get_chat_server() -> Optional["LlamaServer"]:
    with _lock:
        return _chat_server


def set_chat_server(server: Optional["LlamaServer"]) -> None:
    global _chat_server
    with _lock:
        _chat_server = server


def get_embed_server() -> Optional["LlamaServer"]:
    with _lock:
        return _embed_server


def set_embed_server(server: Optional["LlamaServer"]) -> None:
    global _embed_server
    with _lock:
        _embed_server = server
