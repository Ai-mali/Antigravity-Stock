"""Frameless Desktop Shell for AC Stock Tracker.

Launches a native frameless Edge WebView2 window with custom dark titlebar,
window drag support, minimize/maximize/close controls, and an integrated
background FastAPI backend server.
"""

import sys
import time
import socket
import threading
import uvicorn
import webview

# Import the FastAPI app
from backend import app


class DesktopApi:
    """JS-Python bridge exposed to the web frontend."""

    def __init__(self):
        self.window = None

    def set_window(self, window):
        self.window = window

    def minimize(self):
        if self.window:
            self.window.minimize()

    def toggle_maximize(self):
        if self.window:
            try:
                if getattr(self.window, '_is_maximized', False):
                    self.window.restore()
                    self.window._is_maximized = False
                    return False
                else:
                    self.window.maximize()
                    self.window._is_maximized = True
                    return True
            except Exception:
                try:
                    self.window.maximize()
                    return True
                except Exception:
                    pass
        return False

    def close(self):
        if self.window:
            self.window.destroy()

    def is_desktop_app(self):
        return True


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


def run_server():
    """Runs uvicorn in a daemon thread if not already running."""
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
        access_log=False
    )
    server = uvicorn.Server(config)
    server.run()


def main():
    # If server is not already running on 8000, start it in background thread
    if not is_port_in_use(8000):
        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        # Wait up to 5s for server to start
        for _ in range(50):
            if is_port_in_use(8000):
                break
            time.sleep(0.1)

    api = DesktopApi()

    # Create true frameless window
    window = webview.create_window(
        title='AC Stock Tracker',
        url='http://127.0.0.1:8000/',
        js_api=api,
        width=1320,
        height=840,
        min_size=(1050, 680),
        frameless=True,
        easy_drag=False,
        background_color='#0f171c'
    )
    api.set_window(window)

    # Start the desktop window (blocking until closed)
    webview.start(debug=False)
    sys.exit(0)


if __name__ == '__main__':
    main()
