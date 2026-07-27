import threading
import socket
import sys
import json
import asyncio
import traceback
import queue
from pathlib import Path

# Import database methods
from llampaca.engine.db import (
    list_conversations,
    get_conversation,
    create_conversation,
    add_message,
    delete_conversation,
    update_conversation_title,
    update_conversation_summary
)
from llampaca.config import load_config, DEFAULT_CONTEXT_SIZE
from llampaca.gui.agent_manager import agent_manager

import logging
import logging
logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)
def find_free_port(start_port=8090):
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                port += 1

def start_http_server(directory, port):
    config = load_config()
    agent_manager.start(config)
    
    import uvicorn
    from llampaca.gui.routes import app
    
    config_uv = uvicorn.Config(
        app, 
        host="127.0.0.1", 
        port=port, 
        log_level="error",
        ws_ping_interval=None,
        ws_ping_timeout=None
    )
    server = uvicorn.Server(config_uv)
    
    thread = threading.Thread(target=server.run)
    thread.daemon = True
    thread.start()
    
    # Wait for uvicorn to actually start listening
    import time, socket
    started = False
    for _ in range(50):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                started = True
                break
        except OSError:
            time.sleep(0.1)
    
    class ServerWrapper:
        def __init__(self, srv):
            self.srv = srv
        def shutdown(self):
            self.srv.should_exit = True
        def server_close(self):
            pass
            
    return ServerWrapper(server)

def start_gui_window():
    """Start the background HTTP server and launch the pywebview standalone native window."""
    import sys
    import os
    from llampaca.logutil import setup_logging
    from llampaca.config import LLAMPACA_DIR
    from pathlib import Path
    setup_logging(console=True, log_dir=Path(LLAMPACA_DIR) / "logs")
    
    # macOS runtime hack: override Application Menu Name in menu bar
    if sys.platform == 'darwin':
        try:
            from Foundation import NSBundle
            bundle = NSBundle.mainBundle()
            if bundle:
                info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
                if info:
                    info['CFBundleName'] = 'Llampaca'
                    info['CFBundleDisplayName'] = 'Llampaca'
        except Exception:
            pass

    try:
        import webview
    except ImportError:
        logger.error("Error: 'pywebview' is not installed.")
        logger.info("Please install it running: pip install pywebview")
        sys.exit(1)

    gui_dir = Path(__file__).parent.resolve()

    def _set_app_icon():
        """Set OS-specific icon simple and fast without impacting startup time."""
        if sys.platform == 'darwin':
            # macOS: use padded HIG-compliant icon asset (fallback to logo.png)
            mac_icon = gui_dir / "app_icon_mac.png"
            target_path = mac_icon if mac_icon.exists() else (gui_dir / "logo.png")
            if target_path.exists():
                try:
                    from AppKit import NSApplication, NSImage, NSAlert
                    app = NSApplication.sharedApplication()
                    icon_image = NSImage.alloc().initWithContentsOfFile_(str(target_path))
                    if icon_image and icon_image.isValid():
                        app.setApplicationIconImage_(icon_image)

                        # Auto-set icon on all macOS alert / confirmation dialogs (NSAlert)
                        try:
                            import ctypes
                            import objc
                            objc_lib = ctypes.cdll.LoadLibrary('/usr/lib/libobjc.dylib')
                            objc_lib.objc_getClass.restype = ctypes.c_void_p
                            objc_lib.objc_getClass.argtypes = [ctypes.c_char_p]
                            objc_lib.class_getInstanceMethod.restype = ctypes.c_void_p
                            objc_lib.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                            objc_lib.method_getImplementation.restype = ctypes.c_void_p
                            objc_lib.method_getImplementation.argtypes = [ctypes.c_void_p]
                            objc_lib.sel_registerName.restype = ctypes.c_void_p
                            objc_lib.sel_registerName.argtypes = [ctypes.c_char_p]

                            nsalert_cls_ptr = objc_lib.objc_getClass(b'NSAlert')
                            init_sel = objc_lib.sel_registerName(b'init')
                            m_init = objc_lib.class_getInstanceMethod(nsalert_cls_ptr, init_sel)
                            orig_init_imp_ptr = objc_lib.method_getImplementation(m_init)

                            IMP_TYPE = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
                            orig_init_fn = IMP_TYPE(orig_init_imp_ptr)

                            def _swizzled_nsalert_init(self):
                                self_ptr = objc.pyobjc_id(self)
                                res_ptr = orig_init_fn(self_ptr, init_sel)
                                alert = objc.objc_object(c_void_p=res_ptr)
                                if alert:
                                    try:
                                        alert.setIcon_(icon_image)
                                    except Exception:
                                        pass
                                return alert

                            _set_app_icon._imp_holder = _swizzled_nsalert_init
                            NSAlert.init = _swizzled_nsalert_init
                        except Exception:
                            pass
                except Exception:
                    pass
        elif sys.platform == 'win32':
            # Windows: set explicit AppUserModelID for taskbar process icon
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Llampaca.Dashboard.App.1")
            except Exception:
                pass
        elif sys.platform.startswith('linux'):
            # Linux: icon handling relies on window manager / pywebview favicon
            pass

    # Set icon before starting window
    _set_app_icon()

    port = find_free_port()
    server = start_http_server(gui_dir, port)

    logger.info(f"GUI HTTP Server running locally at http://127.0.0.1:{port}")
    logger.info("Opening native window...")

    try:
        # Per-launch cache-buster on the window URL. pywebview's WebKit
        # backend keeps its own persistent HTTP cache (not fully cleared by
        # wiping ~/Library/WebKit/<app>/WebsiteData), so it can keep loading a
        # stale index.html / JS bundle across launches — making already-fixed
        # frontend bugs reappear. A fresh query string every launch forces the
        # main document (and, via the no-cache headers, its subresources) to
        # be re-fetched. Files are local, so always-fresh has no real cost.
        import time as _time
        cache_buster = int(_time.time())
        webview.create_window(
            "Llampaca Dashboard",
            f"http://127.0.0.1:{port}/index.html?v={cache_buster}",
            width=1150,
            height=780,
            min_size=(950, 680)
        )
        # Setting LLAMPACA_DEBUG=1 enables the WebKit Web Inspector
        # (right-click -> Inspect Element) so the Console/Network tabs can be
        # used to diagnose frontend issues live.
        debug = os.environ.get("LLAMPACA_DEBUG", "").strip() in ("1", "true", "yes")
        webview.start(func=_set_app_icon, debug=debug)
    finally:
        logger.info("Window closed. Stopping HTTP server...")
        server.shutdown()
        server.server_close()
        agent_manager.stop()

def start_api_server(port=8090):
    """Start the Llampaca HTTP API server in the foreground (blocking)."""
    from llampaca.logutil import setup_logging
    from llampaca.config import LLAMPACA_DIR
    from pathlib import Path
    setup_logging(console=True, log_dir=Path(LLAMPACA_DIR) / "logs")
    gui_dir = Path(__file__).parent.resolve()
    server = start_http_server(gui_dir, port)
    logger.info(f"Llampaca HTTP API Server running locally at http://127.0.0.1:{port}")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Stopping HTTP server...")
        server.shutdown()
        server.server_close()
        agent_manager.stop()
