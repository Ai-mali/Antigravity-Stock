"""Frameless Desktop Shell for AC Stock Tracker.

Launches a native frameless Edge WebView2 window with custom dark titlebar,
smooth 8-direction resize handles, native rounded corners, and an integrated
background FastAPI backend server.
"""

import sys
import time
import socket
import threading
import ctypes
from ctypes import wintypes
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

    def get_hwnd(self):
        if self.window and hasattr(self.window, 'native') and self.window.native:
            try:
                return int(self.window.native.Handle.ToInt32())
            except Exception:
                pass
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, 'AC Stock Tracker')
            if hwnd:
                return hwnd
        except Exception:
            pass
        return None

    def enable_window_features(self):
        """Enable Windows 11 DWM rounded corners and native sizing frame."""
        try:
            hwnd = self.get_hwnd()
            if hwnd:
                # 1. Windows 11 Native Rounded Corners (DWMWA_WINDOW_CORNER_PREFERENCE = 33)
                DWMWA_WINDOW_CORNER_PREFERENCE = 33
                DWMWCP_ROUND = 2
                val = ctypes.c_int(DWMWCP_ROUND)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_WINDOW_CORNER_PREFERENCE,
                    ctypes.byref(val),
                    ctypes.sizeof(val)
                )

                # 2. Add WS_THICKFRAME for native OS window sizing & aero snap
                GWL_STYLE = -16
                WS_THICKFRAME = 0x00040000
                WS_MINIMIZEBOX = 0x00020000
                WS_MAXIMIZEBOX = 0x00010000
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
                style |= WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    0x0027  # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                )
                return True
        except Exception:
            pass
        return False

    def start_native_resize(self, direction: str):
        """Initiate native Windows OS smooth hardware resizing for frameless window."""
        dir_map = {
            'left': 1,        # WMSZ_LEFT
            'right': 2,       # WMSZ_RIGHT
            'top': 3,         # WMSZ_TOP
            'top-left': 4,    # WMSZ_TOPLEFT
            'top-right': 5,   # WMSZ_TOPRIGHT
            'bottom': 6,      # WMSZ_BOTTOM
            'bottom-left': 7, # WMSZ_BOTTOMLEFT
            'bottom-right': 8 # WMSZ_BOTTOMRIGHT
        }
        dir_code = dir_map.get(direction)
        if not dir_code:
            return False

        try:
            hwnd = self.get_hwnd()
            if hwnd:
                ctypes.windll.user32.ReleaseCapture()
                # WM_SYSCOMMAND = 0x0112, SC_SIZE = 0xF000
                ctypes.windll.user32.SendMessageW(hwnd, 0x0112, 0xF000 + dir_code, 0)
                return True
        except Exception:
            pass
        return False

    def get_window_bounds(self):
        """Returns accurate screen coordinates and dimensions of the window."""
        try:
            hwnd = self.get_hwnd()
            if hwnd:
                rect = wintypes.RECT()
                if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return {
                        'x': int(rect.left),
                        'y': int(rect.top),
                        'width': int(rect.right - rect.left),
                        'height': int(rect.bottom - rect.top)
                    }
        except Exception:
            pass

        if self.window:
            return {
                'x': int(getattr(self.window, 'x', 0)),
                'y': int(getattr(self.window, 'y', 0)),
                'width': int(getattr(self.window, 'width', 1320)),
                'height': int(getattr(self.window, 'height', 840))
            }
        return {'x': 0, 'y': 0, 'width': 1320, 'height': 840}

    def set_bounds(self, x, y, width, height):
        """Atomic 0-latency window reposition and resize via Win32 SetWindowPos."""
        min_w, min_h = 1050, 680
        w = max(min_w, int(width))
        h = max(min_h, int(height))

        try:
            hwnd = self.get_hwnd()
            if hwnd:
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, int(x), int(y), int(w), int(h),
                    SWP_NOZORDER | SWP_NOACTIVATE
                )
                if self.window:
                    self.window._x = int(x)
                    self.window._y = int(y)
                    self.window._width = int(w)
                    self.window._height = int(h)
                return True
        except Exception:
            pass

        if self.window:
            try:
                if x is not None and y is not None:
                    self.window.move(int(x), int(y))
                self.window.resize(w, h)
                return True
            except Exception:
                pass
        return False

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

    # Create true frameless window with desktop mode query param
    window = webview.create_window(
        title='AC Stock Tracker',
        url='http://127.0.0.1:8000/?app_mode=desktop',
        js_api=api,
        width=1320,
        height=840,
        min_size=(1050, 680),
        frameless=True,
        easy_drag=False,
        background_color='#0f171c'
    )
    api.set_window(window)

    def on_started(w):
        time.sleep(0.4)
        api.enable_window_features()

    # Start the desktop window (blocking until closed)
    webview.start(on_started, window, debug=False)
    sys.exit(0)


if __name__ == '__main__':
    main()
