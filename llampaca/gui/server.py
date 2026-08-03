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
logger = logging.getLogger(__name__)



def start_http_server(directory):
    config = load_config()
    agent_manager.start(config)
    
    import uvicorn
    import socket
    from llampaca.gui.routes import app
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    
    config_uv = uvicorn.Config(
        app, 
        host="127.0.0.1", 
        log_level="error",
        ws_ping_interval=None,
        ws_ping_timeout=None
    )
    server = uvicorn.Server(config_uv)
    
    def run_server():
        server.run(sockets=[sock])
        
    thread = threading.Thread(target=run_server)
    thread.daemon = True
    thread.start()
    
    # Wait for uvicorn to actually start listening
    import time
    while not server.started:
        time.sleep(0.05)
    
    class ServerWrapper:
        def __init__(self, srv):
            self.srv = srv
        def shutdown(self):
            self.srv.should_exit = True
        def server_close(self):
            pass
            
    return ServerWrapper(server), port

def start_gui_window(on_ready=None):
    """Start the background HTTP server and launch the pywebview standalone native window."""
    import sys
    import os
    import socket
    import threading
    import time
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

    # Pre-bind the socket so we know the port before starting the main window
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]

    # Create the frameless native splash screen
    splash_html = gui_dir / "splash.html"
    splash_window = webview.create_window(
        "Llampaca Startup",
        url=str(splash_html),
        frameless=True,
        width=400,
        height=320,
        resizable=False
    )
    
    # Create the main window, but hide it for now
    cache_buster = int(time.time())
    main_window = webview.create_window(
        "Llampaca Dashboard",
        f"http://127.0.0.1:{port}/index.html?v={cache_buster}",
        width=1150,
        height=780,
        min_size=(950, 680),
        hidden=True,
        text_select=True
    )
    shutdown_window = webview.create_window(
        "Llampaca Shutdown",
        url=str(splash_html) + "?mode=shutdown",
        frameless=True,
        width=400,
        height=320,
        resizable=False,
        hidden=True
    )
    
    server_wrapper = [None]
    
    def on_closing():
        if getattr(main_window, 'is_shutting_down', False):
            return True
        main_window.is_shutting_down = True
        
        main_window.hide()
        shutdown_window.show()
        
        def shutdown_task():
            if server_wrapper[0]:
                server_wrapper[0].shutdown()
            agent_manager.stop()
            
            from llampaca.engine.state import get_chat_server
            active = get_chat_server()
            if active:
                active.stop()
                
            # Allow short time for pywebview to paint before destroying
            import time
            time.sleep(0.5)
            shutdown_window.destroy()
            main_window.destroy()
            
        threading.Thread(target=shutdown_task, daemon=True).start()
        return False
        
    main_window.events.closing += on_closing
    
    def background_startup():
        try:
            _set_app_icon()
            
            # We give the splash screen a tiny bit of time to draw itself on macOS
            time.sleep(0.3)
            
            def update_splash_status(msg: str):
                try:
                    msg_escaped = msg.replace("'", "\\'")
                    splash_window.evaluate_js(f"window.setStatusMessage('{msg_escaped}')")
                except Exception:
                    pass

            # 0. Check and perform auto-init on first launch (updating splash screen UI)
            from llampaca.engine.downloader import ensure_initialized
            ensure_initialized(progress_callback=update_splash_status)
            
            # 1. Start the HTTP server (FastAPI/Uvicorn)
            from llampaca.config import load_config
            config = load_config()
            agent_manager.start(config)
            
            import uvicorn
            from llampaca.gui.routes import app
            
            config_uv = uvicorn.Config(
                app, 
                host="127.0.0.1", 
                log_level="error",
                ws_ping_interval=None,
                ws_ping_timeout=None
            )
            uv_server = uvicorn.Server(config_uv)
            
            def run_uvicorn():
                uv_server.run(sockets=[sock])
                
            uv_thread = threading.Thread(target=run_uvicorn, daemon=True)
            uv_thread.start()
            
            while not uv_server.started:
                time.sleep(0.05)
                
            class ServerWrapper:
                def shutdown(self):
                    uv_server.should_exit = True
                def server_close(self):
                    pass
            server_wrapper[0] = ServerWrapper()
            
            logger.info(f"GUI HTTP Server running locally at http://127.0.0.1:{port}")
            
            # 2. Trigger the heavy llama-server load
            if on_ready:
                try:
                    on_ready()
                except Exception as e:
                    logger.error(f"[GUI Server] Error in on_ready callback: {e}")
        except Exception as err:
            logger.error(f"[GUI Server] Critical error during background startup: {err}")
        finally:
            # 3. Transition: Show main window, destroy splash
            time.sleep(0.2) # small buffer to ensure main_window html is served
            try:
                main_window.show()
            except Exception as e:
                logger.error(f"Error showing main window: {e}")
            try:
                splash_window.destroy()
            except Exception as e:
                logger.error(f"Error destroying splash window: {e}")

    debug = os.environ.get("LLAMPACA_DEBUG", "").strip() in ("1", "true", "yes")
    
    try:
        webview.start(func=background_startup, debug=debug)
    finally:
        logger.info("Window closed. Stopping HTTP server...")
        if server_wrapper[0]:
            server_wrapper[0].shutdown()
            server_wrapper[0].server_close()
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
