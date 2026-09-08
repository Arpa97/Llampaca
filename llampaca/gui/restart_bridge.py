"""
GUI-specific server restart delegation.
The engine layer NEVER imports gui; the gui layer imports engine.
"""
import concurrent.futures
from llampaca.engine.server import _restart_active_server_fallback


def restart_via_agent_manager(agent_manager, **params):
    """Call this from gui/server.py instead of engine/server.py importing gui."""
    fut = concurrent.futures.Future()
    def put():
        agent_manager.request_queue.put_nowait(("restart_server", params, fut))
    agent_manager.loop.call_soon_threadsafe(put)
    return fut.result(timeout=65)
