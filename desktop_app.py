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

APP_URL = 'http://127.0.0.1:8000/?app_mode=desktop'

# Shown instantly while the backend (heavy imports + workbook parse) boots
# in a background thread; replaced via load_url() once port 8000 is live.
SPLASH_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#0f171c;display:flex;align-items:center;
justify-content:center;font-family:'Segoe UI',system-ui,sans-serif;overflow:hidden;
user-select:none;-webkit-user-select:none}
.box{text-align:center}
.logo{font-size:26px;font-weight:700;letter-spacing:2px;color:#e8f0f2}
.logo span{color:#26d07c}
.sub{margin-top:6px;font-size:12px;letter-spacing:5px;color:#5f7683}
.spinner{margin:28px auto 14px;width:34px;height:34px;border:3px solid #1d2b33;
border-top-color:#26d07c;border-radius:50%;animation:spin .8s linear infinite}
.status{font-size:12px;color:#8aa0ab;letter-spacing:.5px}
@keyframes spin{to{transform:rotate(360deg)}}
</style></head><body><div class="box">
<div class="logo">AC <span>STOCK</span> TRACKER</div>
<div class="sub">S M A</div>
<div class="spinner"></div>
<div class="status">Starting services&hellip;</div>
</div></body></html>"""


# ------------------------------------------------------------------ native splash
# A tiny borderless Win32 window that appears in <0.5s — before pywebview and
# WebView2 (the ~1.5s hard cost) have even finished importing/initializing.
# Fully self-contained: own thread + own message loop + double-buffered GDI
# paint. It NEVER interacts with the pywebview window; close() posts WM_CLOSE
# once the real window is showing its own identical splash.
# Any failure degrades silently to "no splash" — it can never break the app.

_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", _POINT)]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT), ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL),
                ("rgbReserved", wintypes.BYTE * 32)]


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", _WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
                ("hIconSm", wintypes.HICON)]


_u32 = ctypes.windll.user32
_g32 = ctypes.windll.gdi32
_k32 = ctypes.windll.kernel32

_u32.DefWindowProcW.restype = _LRESULT
_u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
_u32.RegisterClassExW.restype = wintypes.ATOM
_u32.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
_u32.CreateWindowExW.restype = wintypes.HWND
_u32.CreateWindowExW.argtypes = [wintypes.DWORD, ctypes.c_wchar_p,
                                 ctypes.c_wchar_p, wintypes.DWORD,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                 wintypes.HINSTANCE, wintypes.LPVOID]
_u32.GetMessageW.restype = wintypes.BOOL
_u32.GetMessageW.argtypes = [ctypes.POINTER(_MSG), wintypes.HWND,
                             wintypes.UINT, wintypes.UINT]
_u32.TranslateMessage.argtypes = [ctypes.POINTER(_MSG)]
_u32.DispatchMessageW.restype = _LRESULT
_u32.DispatchMessageW.argtypes = [ctypes.POINTER(_MSG)]
_u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.UpdateWindow.argtypes = [wintypes.HWND]
_u32.GetSystemMetrics.restype = ctypes.c_int
_u32.GetSystemMetrics.argtypes = [ctypes.c_int]
_u32.SetTimer.restype = ctypes.c_size_t
_u32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT,
                          wintypes.LPVOID]
_u32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
_u32.InvalidateRect.argtypes = [wintypes.HWND, wintypes.LPVOID, wintypes.BOOL]
_u32.PostMessageW.restype = wintypes.BOOL
_u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                              wintypes.LPARAM]
_u32.PostQuitMessage.argtypes = [ctypes.c_int]
_u32.DestroyWindow.argtypes = [wintypes.HWND]
_u32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
_u32.BeginPaint.restype = wintypes.HDC
_u32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(_PAINTSTRUCT)]
_u32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(_PAINTSTRUCT)]
_u32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                          wintypes.HBRUSH]
_u32.DrawTextW.restype = ctypes.c_int
_u32.DrawTextW.argtypes = [wintypes.HDC, ctypes.c_wchar_p, ctypes.c_int,
                           ctypes.POINTER(wintypes.RECT), wintypes.UINT]
_u32.SetProcessDPIAware.restype = wintypes.BOOL
_k32.GetModuleHandleW.restype = wintypes.HMODULE
_g32.CreateSolidBrush.restype = wintypes.HBRUSH
_g32.CreateSolidBrush.argtypes = [wintypes.DWORD]
_g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_g32.CreateFontW.restype = wintypes.HFONT
_g32.CreateFontW.argtypes = [ctypes.c_int] * 13 + [ctypes.c_wchar_p]
_g32.SelectObject.restype = wintypes.HGDIOBJ
_g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_g32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
_g32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
_g32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, ctypes.c_wchar_p,
                                       ctypes.c_int,
                                       ctypes.POINTER(wintypes.SIZE)]
_g32.CreateCompatibleDC.restype = wintypes.HDC
_g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_g32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_g32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int,
                                        ctypes.c_int]
_g32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                        ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                        wintypes.DWORD]
_g32.DeleteDC.argtypes = [wintypes.HDC]
_g32.CreateRoundRectRgn.restype = wintypes.HANDLE
_g32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6


class _NativeSplash:
    """Instant Win32 splash shown while WebView2 warms up (see module note)."""

    W, H = 460, 240

    # COLORREF is 0x00BBGGRR
    _BG     = 0x001C170F   # #0F171C
    _ACCENT = 0x007CD026   # #26D07C
    _TEXT   = 0x00F2F0E8   # #E8F0F2
    _SUB    = 0x0083765F   # #5F7683
    _STATUS = 0x00ABA08A   # #8AA0AB
    _TRACK  = 0x00332B1D   # #1D2B33

    def __init__(self):
        self._hwnd = None
        self._phase = 0
        self._ready = threading.Event()
        self._wndproc_ref = None

    def start(self):
        try:
            threading.Thread(target=self._run, daemon=True).start()
        except Exception:
            pass

    def close(self):
        try:
            if self._ready.wait(2.0) and self._hwnd:
                _u32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE
        except Exception:
            pass

    def _run(self):
        try:
            _u32.SetProcessDPIAware()
            hinst = _k32.GetModuleHandleW(None)
            class_name = "ACStockTrackerSplash"

            self._wndproc_ref = _WNDPROC(self._wnd_proc)
            wc = _WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = hinst
            wc.hbrBackground = _g32.CreateSolidBrush(self._BG)
            wc.lpszClassName = class_name
            if not _u32.RegisterClassExW(ctypes.byref(wc)):
                return

            x = (_u32.GetSystemMetrics(0) - self.W) // 2
            y = (_u32.GetSystemMetrics(1) - self.H) // 2

            # WS_POPUP | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
            self._hwnd = _u32.CreateWindowExW(
                0x00000008 | 0x00000080, class_name, "AC Stock Tracker",
                0x80000000, x, y, self.W, self.H, None, None, hinst, None)
            if not self._hwnd:
                return

            # rounded corners (region ownership passes to the window)
            rgn = _g32.CreateRoundRectRgn(0, 0, self.W + 1, self.H + 1, 20, 20)
            _u32.SetWindowRgn(self._hwnd, rgn, True)

            _u32.SetTimer(self._hwnd, 1, 33, None)   # ~30fps shimmer
            _u32.ShowWindow(self._hwnd, 5)           # SW_SHOW
            _u32.UpdateWindow(self._hwnd)
            self._ready.set()

            msg = _MSG()
            while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _u32.TranslateMessage(ctypes.byref(msg))
                _u32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass
        finally:
            self._ready.set()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == 0x000F:                    # WM_PAINT
            self._paint(hwnd)
            return 0
        if msg == 0x0113:                    # WM_TIMER
            self._phase += 1
            _u32.InvalidateRect(hwnd, None, False)
            return 0
        if msg == 0x0014:                    # WM_ERASEBKGND (we paint all)
            return 1
        if msg == 0x0010:                    # WM_CLOSE
            _u32.DestroyWindow(hwnd)
            return 0
        if msg == 0x0002:                    # WM_DESTROY
            _u32.PostQuitMessage(0)
            return 0
        return _u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _text_width(self, hdc, s):
        sz = wintypes.SIZE()
        _g32.GetTextExtentPoint32W(hdc, s, len(s), ctypes.byref(sz))
        return sz.cx

    def _paint(self, hwnd):
        ps = _PAINTSTRUCT()
        hdc = _u32.BeginPaint(hwnd, ctypes.byref(ps))
        if not hdc:
            return
        W, H = self.W, self.H
        mem = _g32.CreateCompatibleDC(hdc)
        bmp = _g32.CreateCompatibleBitmap(hdc, W, H)
        old = _g32.SelectObject(mem, bmp)
        fonts = []
        try:
            full = wintypes.RECT(0, 0, W, H)
            bg = _g32.CreateSolidBrush(self._BG)
            _u32.FillRect(mem, ctypes.byref(full), bg)
            _g32.DeleteObject(bg)
            _g32.SetBkMode(mem, 1)  # TRANSPARENT

            # Title: "AC STOCK TRACKER" with STOCK in accent — two-tone via
            # measured widths, same look as the HTML splash.
            f_title = _g32.CreateFontW(-24, 0, 0, 0, 700, 0, 0, 0, 1,
                                       0, 0, 0, 0, "Segoe UI")
            f_small = _g32.CreateFontW(-11, 0, 0, 0, 400, 0, 0, 0, 1,
                                       0, 0, 0, 0, "Segoe UI")
            fonts += [f_title, f_small]

            _g32.SelectObject(mem, f_title)
            parts = [("AC ", self._TEXT), ("STOCK", self._ACCENT),
                     (" TRACKER", self._TEXT)]
            total = sum(self._text_width(mem, s) for s, _ in parts)
            x = (W - total) // 2
            for s, col in parts:
                _g32.SetTextColor(mem, col)
                r = wintypes.RECT(x, 66, W, 100)
                _u32.DrawTextW(mem, s, -1, ctypes.byref(r), 0x0020)  # LEFT|SINGLELINE
                x += self._text_width(mem, s)

            _g32.SelectObject(mem, f_small)
            _g32.SetTextColor(mem, self._SUB)
            r = wintypes.RECT(0, 102, W, 122)
            _u32.DrawTextW(mem, "S M A", -1, ctypes.byref(r),
                           0x0001 | 0x0004 | 0x0020)  # CENTER|VCENTER|SINGLELINE

            # Sweeping accent bar (the "spinner" equivalent)
            tw, th = 150, 3
            tx, ty = (W - tw) // 2, 158
            track = wintypes.RECT(tx, ty, tx + tw, ty + th)
            tb = _g32.CreateSolidBrush(self._TRACK)
            _u32.FillRect(mem, ctypes.byref(track), tb)
            _g32.DeleteObject(tb)

            hw = 46
            pos = (self._phase * 4) % (tw + hw) - hw
            hx = max(tx, tx + pos)
            hr = min(tx + pos + hw, tx + tw)
            if hr > hx:
                hl = wintypes.RECT(hx, ty, hr, ty + th)
                hb = _g32.CreateSolidBrush(self._ACCENT)
                _u32.FillRect(mem, ctypes.byref(hl), hb)
                _g32.DeleteObject(hb)

            _g32.SetTextColor(mem, self._STATUS)
            r = wintypes.RECT(0, 176, W, 196)
            _u32.DrawTextW(mem, "Starting services…", -1, ctypes.byref(r),
                           0x0001 | 0x0004 | 0x0020)

            _g32.BitBlt(hdc, 0, 0, W, H, mem, 0, 0, 0x00CC0020)  # SRCCOPY
        finally:
            _g32.SelectObject(mem, old)
            for f in fonts:
                _g32.DeleteObject(f)
            _g32.DeleteObject(bmp)
            _g32.DeleteDC(mem)
            _u32.EndPaint(hwnd, ctypes.byref(ps))


class DesktopApi:
    """JS-Python bridge exposed to the web frontend."""

    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    def get_hwnd(self):
        if self._window and hasattr(self._window, 'native') and self._window.native:
            try:
                return int(self._window.native.Handle.ToInt32())
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

        if self._window:
            return {
                'x': int(getattr(self._window, 'x', 0)),
                'y': int(getattr(self._window, 'y', 0)),
                'width': int(getattr(self._window, 'width', 1320)),
                'height': int(getattr(self._window, 'height', 840))
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
                if self._window:
                    self._window._x = int(x)
                    self._window._y = int(y)
                    self._window._width = int(w)
                    self._window._height = int(h)
                return True
        except Exception:
            pass

        if self._window:
            try:
                if x is not None and y is not None:
                    self._window.move(int(x), int(y))
                self._window.resize(w, h)
                return True
            except Exception:
                pass
        return False

    def minimize(self):
        if self._window:
            self._window.minimize()

    def maximize(self):
        """Snap-friendly maximize: only maximizes, never restores."""
        if self._window and not getattr(self._window, '_is_maximized', False):
            try:
                self._window.maximize()
                self._window._is_maximized = True
                return True
            except Exception:
                pass
        return False

    def restore(self):
        """Snap-friendly restore: only restores a maximized window."""
        if self._window and getattr(self._window, '_is_maximized', False):
            try:
                self._window.restore()
                self._window._is_maximized = False
                return True
            except Exception:
                pass
        return False

    def is_maximized(self):
        return bool(self._window and getattr(self._window, '_is_maximized', False))

    def toggle_maximize(self):
        if self._window:
            try:
                if getattr(self._window, '_is_maximized', False):
                    self._window.restore()
                    self._window._is_maximized = False
                    return False
                else:
                    self._window.maximize()
                    self._window._is_maximized = True
                    return True
            except Exception:
                try:
                    self._window.maximize()
                    return True
                except Exception:
                    pass
        return False

    def close(self):
        if self._window:
            self._window.destroy()

    def is_desktop_app(self):
        return True


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


def run_server():
    """Runs uvicorn in a daemon thread if not already running.

    Heavy imports (fastapi/uvicorn/openpyxl) and the initial workbook parse
    happen HERE, inside the thread, so the window can appear immediately.
    """
    import uvicorn
    from backend import app
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
    # 1. Instant native splash — visible in <0.5s, covers the entire boot.
    splash = _NativeSplash()
    splash.start()

    # 2. Backend boots in parallel (imports + workbook parse in the thread).
    if not is_port_in_use(8000):
        t = threading.Thread(target=run_server, daemon=True)
        t.start()

    # 3. webview import happens AFTER splash is up — overlaps backend boot.
    import webview

    api = DesktopApi()

    # Window appears right away on the splash screen (no serial port-wait).
    window = webview.create_window(
        title='AC Stock Tracker',
        html=SPLASH_HTML,
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
        # Runs on a pywebview worker thread: wait for the backend, then swap
        # the splash for the real app.
        for _ in range(300):  # up to 30s for slow machines
            if is_port_in_use(8000):
                break
            time.sleep(0.1)
        try:
            w.load_url(APP_URL)
        except Exception:
            pass
        splash.close()  # hand off — the webview's identical splash is beneath
        time.sleep(0.4)
        api.enable_window_features()

    # Start the desktop window (blocking until closed)
    webview.start(on_started, window, debug=False)
    sys.exit(0)


if __name__ == '__main__':
    main()
