"""Frameless Desktop Shell for VRE AC Stock.

Launches a native frameless Edge WebView2 window with custom dark titlebar,
smooth 8-direction resize handles, native rounded corners, and an integrated
background FastAPI backend server.
"""

import sys
import os
import time
import socket
import threading
import ctypes
from ctypes import wintypes

APP_URL = 'http://127.0.0.1:8000/?app_mode=desktop'
BACKEND_PORT = 8000
HEALTH_URL = 'http://127.0.0.1:%d/api/ui-prefs' % BACKEND_PORT
_APP_DIR = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.abspath(__file__)))
PID_FILE = os.path.join(_APP_DIR, '.vre_app.pid')

# Shown instantly while the backend (heavy imports + workbook parse) boots
# in a background thread; replaced via load_url() once port 8000 is live.
SPLASH_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><style>
/* Dark glass splash — SVG-comet border engine. Self-contained:
   no CDN, no @property, no mask-composite. */
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#05070a;font-family:"Inter","Segoe UI",-apple-system,BlinkMacSystemFont,Roboto,Arial,sans-serif;
overflow:hidden;user-select:none;-webkit-user-select:none}
.splash-container{padding:50px}
.wrapper{position:relative;width:460px;max-width:86vw}
.glow-svg,.ring-svg{position:absolute;left:0;top:0;overflow:visible;pointer-events:none}
.glow-svg{z-index:0;filter:blur(9px);opacity:.95}
.ring-svg{z-index:2}
.card{position:relative;z-index:1;background:linear-gradient(180deg,#151617 0%,#0b0c0d 100%);
border-radius:18px;padding:42px 40px 34px;display:flex;flex-direction:column;align-items:center;
text-align:center;box-shadow:0 24px 50px -18px rgba(0,0,0,.8),inset 0 1px 0 rgba(255,255,255,.04)}
.brand{font-size:30px;font-weight:800;letter-spacing:.06em;
background:linear-gradient(90deg,#475569 0%,#10B981 25%,#06B6D4 50%,#3B82F6 75%,#475569 100%);
background-size:200% 100%;-webkit-background-clip:text;background-clip:text;
-webkit-text-fill-color:transparent;color:transparent;animation:shimmer 4s linear infinite}
@keyframes shimmer{from{background-position:0% 0}to{background-position:-200% 0}}
.sub{margin-top:12px;display:flex;align-items:center;gap:6px;color:#cbd5e1;
font-size:12px;font-weight:500;letter-spacing:.02em}
.gear{width:14px;height:14px;color:#10B981;animation:spin 8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.bar{width:250px;height:5px;background:#222426;border-radius:9999px;margin-top:34px;
position:relative;overflow:hidden}
.bar i{position:absolute;top:0;bottom:0;left:0;width:40%;border-radius:9999px;
background:linear-gradient(90deg,#10B981,#14b8a6,#06B6D4);
box-shadow:0 0 10px rgba(20,184,166,.6);
animation:slide 2.2s cubic-bezier(.4,0,.2,1) infinite}
@keyframes slide{from{transform:translateX(-100%)}to{transform:translateX(250%)}}
.status{margin-top:16px;font-size:12px;color:#cbd5e1;letter-spacing:.02em}
</style></head><body>
<div class="splash-container"><div class="wrapper" id="wrapper">
<svg class="glow-svg" id="glow"></svg>
<div class="card" id="card">
<div class="brand">VRE AC STOCK</div>
<div class="sub"><svg class="gear" viewBox="0 0 24 24" fill="none" stroke="currentColor"
stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
<path d="m12 2 2.4 2.4 3.4-.6 1.2 3.2 3.1 1.5-.7 3.3 2.1 2.7-2.1 2.7.7 3.3-3.1 1.5-1.2 3.2-3.4-.6L12 22l-2.4-2.4-3.4.6-1.2-3.2-3.1-1.5.7-3.3-2.1-2.7 2.1-2.7-.7-3.3 3.1-1.5 1.2-3.2 3.4.6z"/>
<circle cx="12" cy="12" r="3"/></svg><span>AI Scan</span></div>
<div class="bar"><i></i></div>
<div class="status">Starting services&hellip;</div>
</div>
<svg class="ring-svg" id="ring"></svg>
</div></div>
<script>
/* ===================== EDIT THESE ===================== */
var CONFIG = {
  lapMs: 3000,        /* time for one lap around the card               */
  colorCycleMs: 6000, /* green -> cyan -> blue -> purple -> back        */
  tail: 0.24,         /* tail length, fraction of the border (0.24=24%) */
  segments: 30,       /* tail smoothness                                */
  radius: 18,         /* must match .card border-radius                 */
  ringWidth: 2.4,     /* sharp line thickness at the head               */
  glowWidth: 8,       /* glow thickness before blur                     */
  hueStart: 155,      /* green                                          */
  hueEnd: 275,        /* purple (passes cyan ~190, blue ~220)           */
  lagMs: 55           /* color lag inside the tail = gradient streak    */
};
/* ====================================================== */
(function () {
  var NS = "http://www.w3.org/2000/svg";
  var card = document.getElementById("card");
  var glowSvg = document.getElementById("glow");
  var ringSvg = document.getElementById("ring");
  var N = CONFIG.segments, glow = [], ring = [], P = 1, segLen = 1, baseRect;

  function mk(svg, list) {
    for (var i = 0; i < N; i++) {
      var r = document.createElementNS(NS, "rect");
      r.setAttribute("fill", "none");
      svg.appendChild(r);
      list.push(r);
    }
  }
  baseRect = document.createElementNS(NS, "rect");
  baseRect.setAttribute("fill", "none");
  baseRect.setAttribute("stroke", "rgba(255,255,255,0.10)");
  baseRect.setAttribute("stroke-width", "1");
  ringSvg.appendChild(baseRect);
  mk(glowSvg, glow);
  mk(ringSvg, ring);

  function layout() {
    var W = card.offsetWidth, H = card.offsetHeight, R = CONFIG.radius;
    [glowSvg, ringSvg].forEach(function (s) {
      s.setAttribute("width", W); s.setAttribute("height", H);
      s.setAttribute("viewBox", "0 0 " + W + " " + H);
    });
    P = 2 * (W - 2 * R) + 2 * (H - 2 * R) + 2 * Math.PI * R;
    segLen = CONFIG.tail * P / N;
    var dash = segLen + 0.8;
    var da = dash + " " + (P - dash);
    [baseRect].concat(glow, ring).forEach(function (r) {
      r.setAttribute("x", 0); r.setAttribute("y", 0);
      r.setAttribute("width", W); r.setAttribute("height", H);
      r.setAttribute("rx", R); r.setAttribute("ry", R);
    });
    for (var i = 0; i < N; i++) {
      var k = i / (N - 1);
      var op = Math.pow(k, 1.5);
      ring[i].setAttribute("stroke-dasharray", da);
      ring[i].setAttribute("stroke-width", (CONFIG.ringWidth * (0.5 + 0.5 * k)).toFixed(2));
      ring[i].setAttribute("stroke-opacity", op.toFixed(3));
      glow[i].setAttribute("stroke-dasharray", da);
      glow[i].setAttribute("stroke-width", (CONFIG.glowWidth * (0.4 + 0.6 * k)).toFixed(2));
      glow[i].setAttribute("stroke-opacity", (op * 0.9).toFixed(3));
    }
  }

  function hueAt(ms) {
    var p = 0.5 - 0.5 * Math.cos(2 * Math.PI * ms / CONFIG.colorCycleMs);
    return CONFIG.hueStart + (CONFIG.hueEnd - CONFIG.hueStart) * p;
  }

  function frame(now) {
    var head = ((now % CONFIG.lapMs) / CONFIG.lapMs) * P;
    for (var i = 0; i < N; i++) {
      var k = i / (N - 1);
      var s = head - CONFIG.tail * P + i * segLen;
      s = ((s % P) + P) % P;
      var light = 55 + 30 * Math.pow(k, 4);
      var col = "hsl(" + hueAt(now - (N - 1 - i) * CONFIG.lagMs).toFixed(1) + ",90%," + light.toFixed(1) + "%)";
      ring[i].setAttribute("stroke-dashoffset", -s);
      ring[i].setAttribute("stroke", col);
      glow[i].setAttribute("stroke-dashoffset", -s);
      glow[i].setAttribute("stroke", col);
    }
    requestAnimationFrame(frame);
  }

  layout();
  window.addEventListener("resize", layout);
  if (window.ResizeObserver) new ResizeObserver(layout).observe(card);
  requestAnimationFrame(frame);
})();
</script></body></html>"""


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


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


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
_u32.FindWindowW.restype = wintypes.HWND
_u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_u32.MonitorFromWindow.restype = wintypes.HMONITOR
_u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_u32.GetMonitorInfoW.restype = wintypes.BOOL
_u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
_u32.IsZoomed.restype = wintypes.BOOL
_u32.IsZoomed.argtypes = [wintypes.HWND]
_u32.SetWindowPos.restype = wintypes.BOOL
_u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              wintypes.UINT]
_u32.GetWindowRect.restype = wintypes.BOOL
_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.TerminateProcess.restype = wintypes.BOOL
_k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.CloseHandle.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.GetExitCodeProcess.restype = wintypes.BOOL
_k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
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
_g32.Ellipse.restype = wintypes.BOOL
_g32.Ellipse.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int]
_g32.FrameRgn.restype = wintypes.BOOL
_g32.FrameRgn.argtypes = [wintypes.HDC, wintypes.HANDLE, wintypes.HBRUSH,
                          ctypes.c_int, ctypes.c_int]
_g32.FillRgn.restype = wintypes.BOOL
_g32.FillRgn.argtypes = [wintypes.HDC, wintypes.HANDLE, wintypes.HBRUSH]


class _NativeSplash:
    """Instant Win32 splash shown while WebView2 warms up (see module note)."""

    W, H = 460, 240

    # COLORREF is 0x00BBGGRR — near-black backdrop, dark-glass card,
    # comet on the card edge (mirrors SPLASH_HTML)
    _BG     = 0x000A0705   # #05070A
    _CARD   = 0x00131210   # #101213 — mid of the card's dark gradient
    _CARD_RGB = (0x10, 0x12, 0x13)   # card color as plain RGB for blends
    _CARD_LINE = 0x00272625 # ~white 10% over the card — faint border
    _ACCENT = 0x0081B910   # #10B981
    _TEXT   = 0x00E1D5CB   # #CBD5E1
    _SUB    = 0x00E1D5CB   # #CBD5E1
    _STATUS = 0x00E1D5CB   # #CBD5E1
    _TRACK  = 0x00262422   # #222426
    _ACCENT_DIM = 0x005A2E15  # dim teal — halo on the dark card
    _CARD_M  = 30          # card margin from window edge (px)
    # comet palette (plain RGB) — the head cycles through these as it
    # laps the border, the trail dots just blend the head toward white
    _RUN_COLORS = ((0x10, 0xB9, 0x81),   # emerald
                   (0x06, 0xB6, 0xD4),   # cyan
                   (0x3B, 0x82, 0xF6),   # blue
                   (0x8B, 0x5C, 0xF6))   # purple

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
                0x00000008 | 0x00000080, class_name, "ACStockSplash",
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

    @staticmethod
    def _perim_pt(d, x0, y0, w, h):
        """Point d px along a rect's border, clockwise from (x0,y0)."""
        d %= 2 * w + 2 * h
        if d < w:
            return x0 + d, y0                # top edge, left→right
        d -= w
        if d < h:
            return x0 + w, y0 + d            # right edge, top→bottom
        d -= h
        if d < w:
            return x0 + w - d, y0 + h        # bottom edge, right→left
        return x0, y0 + h - (d - w)          # left edge, bottom→top

    @classmethod
    def _runner_rgb(cls, frac):
        """Comet head color at frac [0,1) of a lap — smooth hue cycling."""
        seg = frac * len(cls._RUN_COLORS)
        i = int(seg) % len(cls._RUN_COLORS)
        t = seg - int(seg)
        c0, c1 = cls._RUN_COLORS[i], cls._RUN_COLORS[(i + 1) % len(cls._RUN_COLORS)]
        return tuple(round(c0[k] + (c1[k] - c0[k]) * t) for k in range(3))

    @staticmethod
    def _blend(c, target, t):
        return tuple(round(c[k] + (target[k] - c[k]) * t) for k in range(3))

    @staticmethod
    def _ref(rgb):
        return rgb[0] | rgb[1] << 8 | rgb[2] << 16   # COLORREF is 0x00BBGGRR

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

            # White rounded card inset — the comet runs ITS border.
            m = self._CARD_M
            try:
                card = _g32.CreateRoundRectRgn(m, m, W - m, H - m, 40, 40)
                cb = _g32.CreateSolidBrush(self._CARD)
                _g32.FillRgn(mem, card, cb)
                _g32.DeleteObject(cb)
                edge = _g32.CreateSolidBrush(self._CARD_LINE)
                _g32.FrameRgn(mem, card, edge, 1, 1)
                _g32.DeleteObject(edge)
                _g32.DeleteObject(card)
            except Exception:
                pass

            # Title: "VRE AC STOCK" with AC STOCK in accent — two-tone via
            # measured widths, same look as the HTML splash.
            f_title = _g32.CreateFontW(-24, 0, 0, 0, 700, 0, 0, 0, 1,
                                       0, 0, 0, 0, "Segoe UI")
            f_small = _g32.CreateFontW(-11, 0, 0, 0, 400, 0, 0, 0, 1,
                                       0, 0, 0, 0, "Segoe UI")
            fonts += [f_title, f_small]

            _g32.SelectObject(mem, f_title)
            parts = [("VRE ", self._TEXT), ("AC STOCK", self._ACCENT)]
            total = sum(self._text_width(mem, s) for s, _ in parts)
            x0 = (W - total) // 2
            # soft halo behind the accent word — 4 dim offset copies
            acc_x = x0 + self._text_width(mem, parts[0][0])
            _g32.SetTextColor(mem, self._ACCENT_DIM)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                r = wintypes.RECT(acc_x + dx, 66 + dy, W, 100)
                _u32.DrawTextW(mem, parts[1][0], -1, ctypes.byref(r), 0x0020)
            x = x0
            for s, col in parts:
                _g32.SetTextColor(mem, col)
                r = wintypes.RECT(x, 66, W, 100)
                _u32.DrawTextW(mem, s, -1, ctypes.byref(r), 0x0020)
                x += self._text_width(mem, s)

            _g32.SelectObject(mem, f_small)
            # pulsing live dot + "AI Scan" (mirrors the HTML splash)
            sub = "AI Scan"
            sw = self._text_width(mem, sub)
            sub_x = (W - (10 + 9 + sw)) // 2
            cy = 113
            s = self._phase % 20
            rad = 3 + (s if s < 10 else 20 - s) // 5   # gentle 3-5px pulse
            cx = sub_x + 5
            halo = _g32.CreateSolidBrush(self._ACCENT_DIM)
            old = _g32.SelectObject(mem, halo)
            _g32.Ellipse(mem, cx - rad - 3, cy - rad - 3, cx + rad + 3, cy + rad + 3)
            _g32.SelectObject(mem, old)
            _g32.DeleteObject(halo)
            dot = _g32.CreateSolidBrush(self._ACCENT)
            old = _g32.SelectObject(mem, dot)
            _g32.Ellipse(mem, cx - rad, cy - rad, cx + rad, cy + rad)
            _g32.SelectObject(mem, old)
            _g32.DeleteObject(dot)
            _g32.SetTextColor(mem, self._SUB)
            r = wintypes.RECT(sub_x + 19, 102, W, 122)
            _u32.DrawTextW(mem, sub, -1, ctypes.byref(r), 0x0004 | 0x0020)

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

            # Shooting-star comet running the CARD border: hue-cycling
            # head + closely-spaced trail dots blended toward white so it
            # reads as a tapering streak, not discrete circles.
            cw, ch = W - 2 * m, H - 2 * m
            perim = 2 * cw + 2 * ch
            head = (self._phase * 7) % perim
            head_rgb = self._runner_rgb(head / perim)
            halo_rgb = self._blend(head_rgb, self._CARD_RGB, 0.8)
            trail_pts = tuple(
                (back, self._ref(self._blend(head_rgb, self._CARD_RGB, t)), rad)
                for back, t, rad in ((14, 0.35, 3), (28, 0.55, 3),
                                     (42, 0.75, 2), (56, 0.88, 1)))
            for back, ref, rad in ((0, self._ref(head_rgb), 4),) + trail_pts:
                x, y = self._perim_pt(head - back, m, m, cw, ch)
                if back == 0:  # soft halo behind the head dot
                    hb = _g32.CreateSolidBrush(self._ref(halo_rgb))
                    old2 = _g32.SelectObject(mem, hb)
                    _g32.Ellipse(mem, x - rad - 4, y - rad - 4,
                                 x + rad + 4, y + rad + 4)
                    _g32.SelectObject(mem, old2)
                    _g32.DeleteObject(hb)
                db = _g32.CreateSolidBrush(ref)
                old2 = _g32.SelectObject(mem, db)
                _g32.Ellipse(mem, x - rad, y - rad, x + rad, y + rad)
                _g32.SelectObject(mem, old2)
                _g32.DeleteObject(db)

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
            hwnd = ctypes.windll.user32.FindWindowW(None, 'VRE AC Stock')
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

    def bring_to_front(self):
        """Raise the window to the top of the NORMAL z-order and take focus —
        once, at startup. Deliberately no WS_EX_TOPMOST: afterwards the window
        behaves like any other app and yields when the user clicks away."""
        try:
            hwnd = self.get_hwnd()
            if not hwnd:
                return
            u = ctypes.windll.user32
            # Windows only grants foreground rights to the foreground process.
            # A no-op Alt press makes us eligible long enough to claim focus.
            u.keybd_event(0x12, 0, 0, 0)          # VK_MENU down
            u.keybd_event(0x12, 0, 0x0002, 0)     # VK_MENU up
            u.ShowWindow(hwnd, 9)                  # SW_RESTORE
            u.BringWindowToTop(hwnd)
            _u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                              0x0001 | 0x0002 | 0x0040)  # NOSIZE|NOMOVE|SHOWWINDOW @ HWND_TOP
            u.SetForegroundWindow(hwnd)
            u.SetActiveWindow(hwnd)
        except Exception:
            pass

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

    def save_file(self, filename: str, content_b64: str):
        """Native Save-As dialog then write file. Returns saved path or None."""
        try:
            import webview
            import base64
            if not self._window:
                return None
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=filename,
                file_types=('All files (*.*)',))
            if not result:
                return None
            path = result if isinstance(result, str) else result[0]
            with open(path, 'wb') as f:
                f.write(base64.b64decode(content_b64))
            return str(path)
        except Exception:
            return None

    def open_data_folder(self):
        """Open the app data folder (workbook + backups) in Explorer."""
        try:
            os.startfile(_APP_DIR)
            return True
        except Exception:
            return False

    def _get_work_area(self, hwnd):
        """Rect of the monitor's work area (screen minus taskbar) holding the window."""
        try:
            MONITOR_DEFAULTTONEAREST = 2
            hmon = _u32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            if _u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                r = mi.rcWork
                return int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)
        except Exception:
            pass
        return None

    def _is_zoomed(self, hwnd):
        try:
            return bool(hwnd and _u32.IsZoomed(hwnd))
        except Exception:
            return False

    def maximize(self):
        """Snap-friendly maximize: sizes to the work area so the taskbar stays
        visible (frameless windows otherwise cover it)."""
        if self._window and not getattr(self._window, '_is_maximized', False):
            try:
                hwnd = self.get_hwnd()
                if hwnd:
                    if self._is_zoomed(hwnd):
                        self._window._is_maximized = True
                        return True
                    wa = self._get_work_area(hwnd)
                    if wa:
                        rect = wintypes.RECT()
                        _u32.GetWindowRect(hwnd, ctypes.byref(rect))
                        self._window._restore_bounds = (
                            int(rect.left), int(rect.top),
                            int(rect.right - rect.left), int(rect.bottom - rect.top))
                        _u32.SetWindowPos(hwnd, 0, wa[0], wa[1], wa[2], wa[3], 0x0004)
                        self._window._is_maximized = True
                        return True
                self._window.maximize()
                self._window._is_maximized = True
                return True
            except Exception:
                pass
        return False

    def restore(self):
        """Snap-friendly restore: only restores a maximized window."""
        if not self._window:
            return False
        hwnd = self.get_hwnd()
        if not getattr(self._window, '_is_maximized', False) and not self._is_zoomed(hwnd):
            return False
        try:
            if hwnd:
                if self._is_zoomed(hwnd):
                    _u32.ShowWindow(hwnd, 9)  # SW_RESTORE
                else:
                    rb = getattr(self._window, '_restore_bounds', None)
                    if rb:
                        _u32.SetWindowPos(hwnd, 0, rb[0], rb[1], rb[2], rb[3], 0x0004)
                    else:
                        self._window.restore()
            else:
                self._window.restore()
            self._window._is_maximized = False
            return True
        except Exception:
            pass
        return False

    def is_maximized(self):
        if not self._window:
            return False
        if getattr(self._window, '_is_maximized', False):
            return True
        return self._is_zoomed(self.get_hwnd())

    def toggle_maximize(self):
        if not self._window:
            return False
        if self.is_maximized():
            self.restore()
            return False
        return bool(self.maximize())

    def close(self):
        if self._window:
            self._window.destroy()

    def is_desktop_app(self):
        return True


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


def backend_healthy(timeout: float = 1.5) -> bool:
    """True only if the backend answers HTTP — a zombie process can hold the
    port open (accepts TCP, never responds) while it is dying."""
    try:
        import urllib.request
        urllib.request.urlopen(HEALTH_URL, timeout=timeout).read(8)
        return True
    except Exception:
        return False


def _pid_alive(pid: int) -> bool:
    try:
        h = _k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            code = wintypes.DWORD(0)
            _k32.GetExitCodeProcess(h, ctypes.byref(code))
            _k32.CloseHandle(h)
            return code.value == 259  # STILL_ACTIVE
    except Exception:
        pass
    return False


def _kill_pid(pid: int) -> bool:
    try:
        h = _k32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
        if h:
            _k32.TerminateProcess(h, 1)
            _k32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def _pid_listening_on(port: int):
    """Fallback: PID owning the listening socket via netstat (no extra deps)."""
    try:
        import subprocess
        out = subprocess.check_output(
            ['netstat', '-ano', '-p', 'tcp'],
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000),
            stderr=subprocess.DEVNULL).decode('utf-8', 'ignore')
        suffix = ':%d' % port
        for line in out.splitlines():
            if 'LISTENING' in line:
                parts = line.split()
                if len(parts) >= 5 and parts[1].endswith(suffix):
                    return int(parts[-1])
    except Exception:
        pass
    return None


def cleanup_stale_backend():
    """Kill leftover backend processes from a previous run.

    Two cases: (a) PID file points at a live process with no app window —
    a zombie; (b) port 8000 is open but the server doesn't answer HTTP —
    kill whoever owns it. A healthy backend with a real window is reused.
    """
    my_pid = os.getpid()
    try:
        old_pid = int(open(PID_FILE, encoding='ascii').read().strip())
    except Exception:
        old_pid = None
    if old_pid and old_pid != my_pid and _pid_alive(old_pid):
        if not _u32.FindWindowW(None, 'VRE AC Stock'):
            _kill_pid(old_pid)  # zombie: process alive, window gone

    if is_port_in_use(BACKEND_PORT) and not backend_healthy():
        pid = _pid_listening_on(BACKEND_PORT)
        if pid and pid != my_pid:
            _kill_pid(pid)
        for _ in range(50):  # wait for the socket to be released
            if not is_port_in_use(BACKEND_PORT):
                break
            time.sleep(0.1)


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

    # 2. Reap a stale backend (zombie process or dead socket owner), record
    # our PID for the next launch, then boot the backend in parallel.
    cleanup_stale_backend()
    try:
        with open(PID_FILE, 'w', encoding='ascii') as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    if not is_port_in_use(BACKEND_PORT):
        t = threading.Thread(target=run_server, daemon=True)
        t.start()
    # Port already serving a healthy backend = instant relaunch, reuse it.

    # 3. webview import happens AFTER splash is up — overlaps backend boot.
    import webview

    api = DesktopApi()

    # Window appears right away on the splash screen (no serial port-wait).
    window = webview.create_window(
        title='VRE AC Stock',
        html=SPLASH_HTML,
        js_api=api,
        width=1320,
        height=840,
        min_size=(1050, 680),
        frameless=True,
        easy_drag=False,
        text_select=True,
        background_color='#05070a'
    )
    api.set_window(window)

    def on_started(w):
        # The real window is already showing its identical HTML splash —
        # hand off to it NOW so the two splashes never overlap on screen.
        splash.close()
        # Jump the window in front of whatever launched it — one-shot raise,
        # NOT always-on-top; it yields normally once the user clicks away.
        api.bring_to_front()
        # Runs on a pywebview worker thread: wait for the backend, then swap
        # the splash for the real app.
        for _ in range(300):  # up to 30s for slow machines
            if backend_healthy():
                break
            time.sleep(0.1)
        try:
            w.load_url(APP_URL)
        except Exception:
            pass
        time.sleep(0.4)
        api.enable_window_features()
        # The backend wait can take seconds — re-assert foreground once the
        # real UI is actually displayed, in case focus drifted meanwhile.
        api.bring_to_front()

    # Start the desktop window (blocking until closed). os._exit skips the
    # interpreter shutdown that can hang joining threads — the port and all
    # resources are released immediately, so an instant relaunch works.
    webview.start(on_started, window, debug=False)
    os._exit(0)


if __name__ == '__main__':
    main()
