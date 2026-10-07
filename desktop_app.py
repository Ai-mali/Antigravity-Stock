"""Frameless Desktop Shell for VRE AC Stock.

Launches a native frameless Edge WebView2 window with custom dark titlebar,
smooth 8-direction resize handles, native rounded corners, and an integrated
background FastAPI backend server.
"""

import sys
import os
import time
import socket
import errno
import threading
import ctypes
import math
import colorsys
import traceback
from ctypes import wintypes

import boot_debug_kit as bk
bk.t("desktop_app imported")

APP_URL = 'http://127.0.0.1:8000/?app_mode=desktop'
BACKEND_PORT = 8000
HEALTH_URL = 'http://127.0.0.1:%d/api/ui-prefs' % BACKEND_PORT
_APP_DIR = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.abspath(__file__)))
PID_FILE = os.path.join(_APP_DIR, '.vre_app.pid')
ICON_FILE = os.path.join(_APP_DIR, 'VRE.ico')

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
.glow-svg{z-index:0;filter:blur(4.5px);opacity:.95;clip-path:inset(0 round 18px)}
.ring-svg{z-index:2}
.card{position:relative;z-index:1;background:linear-gradient(180deg,#151617 0%,#0b0c0d 100%);
border-radius:18px;width:460px;height:220px;box-sizing:border-box;padding:0 40px;
display:flex;flex-direction:column;align-items:center;justify-content:center;
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
  comets: 2,          /* how many comets (2 = second is 180deg behind)  */
  colorOffsetMs: 0,   /* color-cycle offset between comets (e.g. 3000)  */
  radius: 18,         /* must match .card border-radius                 */
  ringWidth: 2.4,     /* sharp line thickness at the head               */
  glowWidth: 4,       /* glow thickness before blur                     */
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
  var N = CONFIG.segments, C = CONFIG.comets, glow = [], ring = [], P = 1, segLen = 1, baseRect;

  function mk(svg, list) {
    for (var i = 0; i < N * C; i++) {
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
    for (var j = 0; j < N * C; j++) {
      var i = j % N;
      var k = i / (N - 1);
      var op = Math.pow(k, 1.5);
      ring[j].setAttribute("stroke-dasharray", da);
      ring[j].setAttribute("stroke-width", (CONFIG.ringWidth * (0.5 + 0.5 * k)).toFixed(2));
      ring[j].setAttribute("stroke-opacity", op.toFixed(3));
      glow[j].setAttribute("stroke-dasharray", da);
      glow[j].setAttribute("stroke-width", (CONFIG.glowWidth * (0.4 + 0.6 * k)).toFixed(2));
      glow[j].setAttribute("stroke-opacity", (op * 0.9).toFixed(3));
    }
  }

  function hueAt(ms) {
    var p = 0.5 - 0.5 * Math.cos(2 * Math.PI * ms / CONFIG.colorCycleMs);
    return CONFIG.hueStart + (CONFIG.hueEnd - CONFIG.hueStart) * p;
  }

  function frame(now) {
    var base = ((now % CONFIG.lapMs) / CONFIG.lapMs) * P;
    for (var c = 0; c < C; c++) {
      var head = base + c * P / C;                 /* c=1 of 2 -> 180 degrees behind */
      var tnow = now + c * CONFIG.colorOffsetMs;
      for (var i = 0; i < N; i++) {
        var j = c * N + i;
        var k = i / (N - 1);
        var s = head - CONFIG.tail * P + i * segLen;
        s = ((s % P) + P) % P;
        var light = 55 + 30 * Math.pow(k, 4);
        var col = "hsl(" + hueAt(tnow - (N - 1 - i) * CONFIG.lagMs).toFixed(1) + ",90%," + light.toFixed(1) + "%)";
        ring[j].setAttribute("stroke-dashoffset", -s);
        ring[j].setAttribute("stroke", col);
        glow[j].setAttribute("stroke-dashoffset", -s);
        glow[j].setAttribute("stroke", col);
      }
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
_u32.LoadImageW.restype = wintypes.HICON
_u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                            wintypes.UINT, ctypes.c_int, ctypes.c_int,
                            wintypes.UINT]
_u32.SendMessageW.restype = _LRESULT
_u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, _LRESULT]
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


# ---- GDI+ bindings: anti-aliased lines, ARGB alpha pens, premultiplied ----
# 32bpp surfaces for per-pixel-alpha layered windows.
_gd = ctypes.windll.gdiplus
_vp = ctypes.c_void_p


class _GDIPlusStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", wintypes.UINT),
                ("DebugEventCallback", wintypes.LPVOID),
                ("SuppressBackgroundThread", wintypes.BOOL),
                ("SuppressExternalCodecs", wintypes.BOOL)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 1)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", wintypes.BYTE), ("BlendFlags", wintypes.BYTE),
                ("SourceConstantAlpha", wintypes.BYTE),
                ("AlphaFormat", wintypes.BYTE)]


class _RECTF(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float),
                ("w", ctypes.c_float), ("h", ctypes.c_float)]


class _POINTF(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]


_gd.GdiplusStartup.argtypes = [ctypes.POINTER(_vp),
                               ctypes.POINTER(_GDIPlusStartupInput), _vp]
_gd.GdiplusStartup.restype = ctypes.c_int
_gd.GdiplusShutdown.argtypes = [_vp]
_gd.GdipCreateBitmapFromScan0.argtypes = [ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, _vp,
                                        ctypes.POINTER(_vp)]
_gd.GdipDisposeImage.argtypes = [_vp]
_gd.GdipGetImageGraphicsContext.argtypes = [_vp, ctypes.POINTER(_vp)]
_gd.GdipDeleteGraphics.argtypes = [_vp]
_gd.GdipFlush.argtypes = [_vp, ctypes.c_int]
_gd.GdipSetSmoothingMode.argtypes = [_vp, ctypes.c_int]
_gd.GdipSetTextRenderingHint.argtypes = [_vp, ctypes.c_int]
_gd.GdipGraphicsClear.argtypes = [_vp, wintypes.DWORD]
_gd.GdipCreatePen1.argtypes = [wintypes.DWORD, ctypes.c_float, ctypes.c_int,
                               ctypes.POINTER(_vp)]
_gd.GdipSetPenStartCap.argtypes = [_vp, ctypes.c_int]
_gd.GdipSetPenEndCap.argtypes = [_vp, ctypes.c_int]
_gd.GdipDeletePen.argtypes = [_vp]
_gd.GdipDrawLine.argtypes = [_vp, _vp] + [ctypes.c_float] * 4
_gd.GdipCreateSolidFill.argtypes = [wintypes.DWORD, ctypes.POINTER(_vp)]
_gd.GdipDeleteBrush.argtypes = [_vp]
_gd.GdipCreateLineBrush.argtypes = [ctypes.POINTER(_POINTF),
                                    ctypes.POINTER(_POINTF), wintypes.DWORD,
                                    wintypes.DWORD, ctypes.c_int,
                                    ctypes.POINTER(_vp)]
_gd.GdipSetLinePresetBlend.argtypes = [_vp, ctypes.POINTER(ctypes.c_uint),
                                       ctypes.POINTER(ctypes.c_float),
                                       ctypes.c_int]
_gd.GdipFillPolygon.argtypes = [_vp, _vp, ctypes.POINTER(_POINTF),
                              ctypes.c_int, ctypes.c_int]
_gd.GdipDrawPolygon.argtypes = [_vp, _vp, ctypes.POINTER(_POINTF),
                              ctypes.c_int]
_gd.GdipFillEllipse.argtypes = [_vp, _vp] + [ctypes.c_float] * 4
_gd.GdipCreateFontFamilyFromName.argtypes = [wintypes.LPCWSTR, _vp,
                                             ctypes.POINTER(_vp)]
_gd.GdipDeleteFontFamily.argtypes = [_vp]
_gd.GdipCreateFont.argtypes = [_vp, ctypes.c_float, ctypes.c_int,
                              ctypes.c_int, ctypes.POINTER(_vp)]
_gd.GdipDeleteFont.argtypes = [_vp]
_gd.GdipCreateStringFormat.argtypes = [ctypes.c_int, wintypes.WORD,
                                       ctypes.POINTER(_vp)]
_gd.GdipSetStringFormatAlign.argtypes = [_vp, ctypes.c_int]
_gd.GdipDeleteStringFormat.argtypes = [_vp]
_gd.GdipDrawString.argtypes = [_vp, ctypes.c_wchar_p, ctypes.c_int, _vp,
                              ctypes.POINTER(_RECTF), _vp, _vp]
_gd.GdipMeasureString.argtypes = [_vp, ctypes.c_wchar_p, ctypes.c_int, _vp,
                                 ctypes.POINTER(_RECTF), _vp,
                                 ctypes.POINTER(_RECTF),
                                 ctypes.POINTER(ctypes.c_int),
                                 ctypes.POINTER(ctypes.c_int)]

_g32.CreateDIBSection.restype = wintypes.HBITMAP
_g32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BITMAPINFO),
                                  wintypes.UINT, ctypes.POINTER(_vp),
                                  wintypes.HANDLE, wintypes.DWORD]
_u32.GetDC.restype = wintypes.HDC
_u32.GetDC.argtypes = [wintypes.HWND]
_u32.ReleaseDC.restype = ctypes.c_int
_u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_u32.UpdateLayeredWindow.restype = wintypes.BOOL
_u32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC,
                                     ctypes.POINTER(wintypes.POINT),
                                     ctypes.POINTER(wintypes.SIZE),
                                     wintypes.HDC,
                                     ctypes.POINTER(wintypes.POINT),
                                     wintypes.DWORD,
                                     ctypes.POINTER(_BLENDFUNCTION),
                                     wintypes.DWORD]
try:
    _u32.GetDpiForSystem.restype = wintypes.UINT
    _u32.GetDpiForSystem.argtypes = []
except AttributeError:
    pass


class _NativeSplash:
    """Instant Win32/GDI+ splash shown while WebView2 warms up.

    Layered window with per-pixel alpha (UpdateLayeredWindow) + GDI+
    rendering on a 32bpp premultiplied DIB. A color-lagged "shooting
    star" comet laps the rounded card border — the same dash-segment
    engine as SPLASH_HTML, ported to GDI+ strokes.
    """

    # ===================== TUNABLES =====================
    LAP_MS         = 3000     # one comet lap around the card border
    COLOR_CYCLE_MS = 6000     # green -> cyan -> blue -> purple -> back
    TAIL           = 0.24     # tail length, fraction of the perimeter
    SEGMENTS       = 30       # tail smoothness (segments behind the head)
    LAG_MS         = 55.0     # color lag inside the tail = gradient streak
    HUE_START      = 155.0    # green
    HUE_SPAN       = 120.0    # +120 deg -> purple (passes cyan, blue)
    RING_W         = 2.4      # sharp comet width at the head (logical px)
    GLOW_W         = 7.0      # widest glow pass at the head (logical px)
    GLOW_PASSES    = ((1.0, 0.16), (0.72, 0.22), (0.5, 0.30))  # (width, alpha) - fallback only
    GLOW_SIGMA     = 3.25     # glow softness inside the card edge (logical px)
    GLOW_AMP       = 0.85     # glow strength 0..1
    COMETS         = 2        # 2 = second comet is 180 degrees behind
    COMET_COLOR_OFFSET_MS = 0.0
    TITLE_SWEEP_MS = 4000     # title gradient sweep period (CSS shimmer 4s)
    CARD_R         = 18.0     # card corner radius (logical px)
    MARGIN         = 50       # transparent margin around the card
    WIN_W, WIN_H   = 560, 320 # logical window size incl. margins
    TIMER_MS       = 16       # ~60fps
    # ---- ARGB palette, 0xAARRGGBB (mirrors SPLASH_HTML) ----
    _CARD_TOP   = 0xFF151617
    _CARD_BOT   = 0xFF0B0C0D
    _CARD_EDGE  = 0x1AFFFFFF  # faint base border — white ~10%
    _ACCENT     = 0xFF10B981
    _ACCENT_HI  = 0xFF06B6D4
    _TEXT       = 0xFFCBD5E1
    _TRACK      = 0xFF222426
    _POLY_N     = 160         # rounded-rect polyline resolution
    # ======================================================

    def __init__(self):
        self._hwnd = None
        self._phase = 0
        self._ready = threading.Event()
        self._wndproc_ref = None
        self._sc = 1.0
        self._t0 = None
        self.W = self.WIN_W
        self.H = self.WIN_H
        self._m = float(self.MARGIN)
        self._rad = self.CARD_R
        self._cw = 0.0
        self._ch = 0.0
        self._perim = 1.0
        self._card_poly = None
        self._gdi_tok = _vp()
        self._mem = None
        self._hbmp = None
        self._gpimg = None
        self._gfx = None
        self._objs = []          # (deleter, handle) freed on destroy
        self._fmt_l = None
        self._fmt_c = None
        self._f_title = None
        self._f_small = None
        self._vre_w = 0.0
        self._ac_w = 0.0
        self._sub_w = 0.0
        self._bits_addr = None   # address of the DIB pixel memory (for CometLayer)
        self._cm = None          # CometLayer, built after the first frame is shown
        self._cm_failed = False  # True -> fall back to the old stroke comet
        self._fading = False     # WM_CLOSE starts a fade-out, not an instant cut
        self._fade_a = 255       # whole-window alpha pushed via SourceConstantAlpha

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
            try:
                self._sc = (_u32.GetDpiForSystem() or 96) / 96.0
            except Exception:
                self._sc = 1.0
            sc = self._sc
            self.W, self.H = round(self.WIN_W * sc), round(self.WIN_H * sc)
            self._m = self.MARGIN * sc
            self._rad = self.CARD_R * sc
            self._cw = self.W - 2 * self._m
            self._ch = self.H - 2 * self._m
            self._perim = (2 * (self._cw - 2 * self._rad)
                           + 2 * (self._ch - 2 * self._rad)
                           + 2 * math.pi * self._rad)
            pts = [self._point_at(self._perim * i / self._POLY_N)
                   for i in range(self._POLY_N)]
            self._card_poly = (_POINTF * self._POLY_N)(
                *[_POINTF(*p) for p in pts])
            hinst = _k32.GetModuleHandleW(None)
            class_name = "ACStockTrackerSplash"

            self._wndproc_ref = _WNDPROC(self._wnd_proc)
            wc = _WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = hinst
            wc.lpszClassName = class_name
            if not _u32.RegisterClassExW(ctypes.byref(wc)):
                return

            x = (_u32.GetSystemMetrics(0) - self.W) // 2
            y = (_u32.GetSystemMetrics(1) - self.H) // 2

            # WS_EX_LAYERED|TOPMOST|TOOLWINDOW + WS_POPUP — the window's
            # shape is defined by per-pixel alpha, no region needed.
            self._hwnd = _u32.CreateWindowExW(
                0x00080000 | 0x00000008 | 0x00000080,
                class_name, "ACStockSplash",
                0x80000000, x, y, self.W, self.H, None, None, hinst, None)
            if not self._hwnd:
                return

            self._init_gdi(self._hwnd)
            _u32.SetTimer(self._hwnd, 1, self.TIMER_MS, None)
            _u32.ShowWindow(self._hwnd, 5)           # SW_SHOW
            _u32.UpdateWindow(self._hwnd)
            self._render(self._hwnd)                 # first frame
            self._ready.set()
            # numpy import + comet geometry happen off-thread, AFTER the splash
            # is already visible, so startup speed is unchanged.
            threading.Thread(target=self._load_comet, daemon=True).start()

            msg = _MSG()
            while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _u32.TranslateMessage(ctypes.byref(msg))
                _u32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass
        finally:
            self._ready.set()

    def _load_comet(self):
        """Build the smooth numpy comet (see comet_layer.py). On any failure
        we silently keep the old GDI+ stroke comet as a fallback."""
        try:
            import numpy as np
            from comet_layer import CometLayer
            if not self._bits_addr:
                raise RuntimeError("no DIB memory")
            W, H = self.W, self.H
            buf = (ctypes.c_ubyte * (W * H * 4)).from_address(self._bits_addr)
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 4)
            cm = CometLayer(
                arr, self._m, self._cw, self._ch, self._rad, scale=self._sc,
                lap_ms=self.LAP_MS, cycle_ms=self.COLOR_CYCLE_MS,
                tail=self.TAIL, lag_total_ms=(self.SEGMENTS - 1) * self.LAG_MS,
                hue_start=self.HUE_START, hue_span=self.HUE_SPAN,
                ring_w=self.RING_W, glow_sigma=self.GLOW_SIGMA,
                glow_amp=self.GLOW_AMP,
                comets=self.COMETS, color_offset_ms=self.COMET_COLOR_OFFSET_MS)
            if self._gfx:                      # window may already be closing
                self._cm = cm
        except Exception:
            traceback.print_exc()
            self._cm_failed = True

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == 0x000F:                    # WM_PAINT — validate + re-push
            ps = _PAINTSTRUCT()
            _u32.BeginPaint(hwnd, ctypes.byref(ps))
            self._render(hwnd)
            _u32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == 0x0113:                    # WM_TIMER — animate / fade
            self._phase += 1
            if self._fading:
                self._fade_a -= 22             # ~12 frames ≈ 190ms fade-out
                if self._fade_a <= 0:
                    _u32.DestroyWindow(hwnd)
                    return 0
            self._render(hwnd)
            return 0
        if msg == 0x0014:                    # WM_ERASEBKGND
            return 1
        if msg == 0x0010:                    # WM_CLOSE — fade out, then destroy
            self._fading = True
            return 0
        if msg == 0x0002:                    # WM_DESTROY
            self._deinit_gdi()
            _u32.PostQuitMessage(0)
            return 0
        return _u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---------------- GDI+ object management ----------------

    def _mk_fmt(self, align):
        f = _vp()
        _gd.GdipCreateStringFormat(0, 0, ctypes.byref(f))
        _gd.GdipSetStringFormatAlign(f, align)
        self._objs.append((_gd.GdipDeleteStringFormat, f))
        return f

    def _mk_font(self, fam, size, style):
        f = _vp()
        _gd.GdipCreateFont(fam, size, style, 2, ctypes.byref(f))  # UnitPixel
        self._objs.append((_gd.GdipDeleteFont, f))
        return f

    def _mk_brush(self, argb):
        b = _vp()
        _gd.GdipCreateSolidFill(argb, ctypes.byref(b))
        self._objs.append((_gd.GdipDeleteBrush, b))
        return b

    def _mk_pen(self, argb, w):
        p = _vp()
        _gd.GdipCreatePen1(argb, w, 0, ctypes.byref(p))
        self._objs.append((_gd.GdipDeletePen, p))
        return p

    def _measure(self, s, font):
        if not (self._gfx and font):
            return 0.0
        r_in = _RECTF(0, 0, 10000, 10000)
        r_out = _RECTF()
        cp = ctypes.c_int()
        ln = ctypes.c_int()
        _gd.GdipMeasureString(self._gfx, s, -1, font, ctypes.byref(r_in),
                              self._fmt_l, ctypes.byref(r_out),
                              ctypes.byref(cp), ctypes.byref(ln))
        return r_out.w

    def _init_gdi(self, hwnd):
        """32bpp premultiplied DIB + GDI+ graphics context + cached objects."""
        try:
            si = _GDIPlusStartupInput(1, None, False, False)
            if _gd.GdiplusStartup(ctypes.byref(self._gdi_tok),
                                  ctypes.byref(si), None):
                return
            hdc = _u32.GetDC(hwnd)
            mem = _g32.CreateCompatibleDC(hdc)
            _u32.ReleaseDC(hwnd, hdc)
            if not mem:
                return
            bmi = _BITMAPINFO()
            h = bmi.bmiHeader
            h.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            h.biWidth = self.W
            h.biHeight = -self.H            # top-down
            h.biPlanes = 1
            h.biBitCount = 32
            bits = _vp()
            hbmp = _g32.CreateDIBSection(mem, ctypes.byref(bmi), 0,
                                         ctypes.byref(bits), None, 0)
            if not hbmp:
                _g32.DeleteDC(mem)
                return
            _g32.SelectObject(mem, hbmp)    # stays selected for ULW
            self._bits_addr = bits.value    # raw BGRA pixels, used by CometLayer
            img = _vp()
            gfx = _vp()
            # GpBitmap over the DIB's own bits — zero-copy, premultiplied
            # 32bpp PARGB so UpdateLayeredWindow composites correctly.
            if (_gd.GdipCreateBitmapFromScan0(self.W, self.H, self.W * 4,
                                              0xE200B, bits,
                                              ctypes.byref(img))
                    or _gd.GdipGetImageGraphicsContext(img, ctypes.byref(gfx))):
                _g32.DeleteObject(hbmp)
                _g32.DeleteDC(mem)
                return
            _gd.GdipSetSmoothingMode(gfx, 4)      # SmoothingModeAntiAlias
            _gd.GdipSetTextRenderingHint(gfx, 4)  # TextRenderingHintAntiAlias
            self._mem, self._hbmp, self._gpimg, self._gfx = mem, hbmp, img, gfx

            self._fmt_l = self._mk_fmt(0)   # near / left
            self._fmt_c = self._mk_fmt(1)   # center
            fam = _vp()
            _gd.GdipCreateFontFamilyFromName("Segoe UI", None,
                                             ctypes.byref(fam))
            self._objs.append((_gd.GdipDeleteFontFamily, fam))
            self._f_title = self._mk_font(fam, 22.0 * self._sc, 1)
            self._f_small = self._mk_font(fam, 11.5 * self._sc, 0)

            m = self._m
            p1 = _POINTF(0.0, m)
            p2 = _POINTF(0.0, m + self._ch)
            self._br_card = _vp()
            _gd.GdipCreateLineBrush(ctypes.byref(p1), ctypes.byref(p2),
                                    self._CARD_TOP, self._CARD_BOT, 0,
                                    ctypes.byref(self._br_card))
            self._objs.append((_gd.GdipDeleteBrush, self._br_card))
            self._br_text = self._mk_brush(self._TEXT)
            self._br_acc = self._mk_brush(self._ACCENT)
            self._br_acchi = self._mk_brush(self._ACCENT_HI)
            self._br_track = self._mk_brush(self._TRACK)
            self._pn_edge = self._mk_pen(self._CARD_EDGE, 1.0)

            self._vre_w = self._measure("VRE ", self._f_title)
            self._ac_w = self._measure("AC STOCK", self._f_title)
            self._sub_w = self._measure("AI Scan", self._f_small)
        except Exception:
            pass

    def _deinit_gdi(self):
        self._cm = None
        self._cm_failed = True
        try:
            for fn, obj in self._objs:
                try:
                    fn(obj)
                except Exception:
                    pass
            self._objs = []
            if self._gfx:
                _gd.GdipDeleteGraphics(self._gfx)
                self._gfx = None
            if self._gpimg:
                _gd.GdipDisposeImage(self._gpimg)
                self._gpimg = None
            if self._hbmp:
                _g32.DeleteObject(self._hbmp)
                self._hbmp = None
            if self._mem:
                _g32.DeleteDC(self._mem)
                self._mem = None
            if self._gdi_tok:
                _gd.GdiplusShutdown(self._gdi_tok)
                self._gdi_tok = _vp()
        except Exception:
            pass

    # ---------------- rounded-rect perimeter math ----------------

    @staticmethod
    def _rr_point(s, x0, y0, w, h, r, perim):
        """(x, y) at distance s along a rounded-rect border, clockwise,
        starting where the top edge meets the top-left arc."""
        sx, sy = w - 2 * r, h - 2 * r
        arc = math.pi * r / 2
        s %= perim
        if s < sx:                                # top edge, left->right
            return x0 + r + s, y0
        s -= sx
        if s < arc:                               # top-right arc (-90->0)
            a = -math.pi / 2 + (math.pi / 2) * (s / arc)
            return x0 + w - r + r * math.cos(a), y0 + r + r * math.sin(a)
        s -= arc
        if s < sy:                                # right edge, top->bottom
            return x0 + w, y0 + r + s
        s -= sy
        if s < arc:                               # bottom-right arc (0->90)
            a = (math.pi / 2) * (s / arc)
            return x0 + w - r + r * math.cos(a), y0 + h - r + r * math.sin(a)
        s -= arc
        if s < sx:                                # bottom edge, right->left
            return x0 + w - r - s, y0 + h
        s -= sx
        if s < arc:                               # bottom-left arc (90->180)
            a = math.pi / 2 + (math.pi / 2) * (s / arc)
            return x0 + r + r * math.cos(a), y0 + h - r + r * math.sin(a)
        s -= arc
        if s < sy:                                # left edge, bottom->top
            return x0, y0 + h - r - s
        s -= sy                                   # top-left arc (180->270)
        a = math.pi + (math.pi / 2) * (s / arc)
        return x0 + r + r * math.cos(a), y0 + r + r * math.sin(a)

    def _point_at(self, s):
        return self._rr_point(s, self._m, self._m, self._cw, self._ch,
                              self._rad, self._perim)

    @staticmethod
    def _rrect_poly(x0, y0, w, h, r, n=24):
        r = max(0.5, min(r, w / 2, h / 2))
        perim = 2 * (w - 2 * r) + 2 * (h - 2 * r) + 2 * math.pi * r
        n = max(8, n)
        return (_POINTF * n)(*[_POINTF(*_NativeSplash._rr_point(
            perim * i / n, x0, y0, w, h, r, perim)) for i in range(n)]), n

    # ---------------- frame rendering ----------------

    def _comet(self, head, tail, seg, t_ms, w_head, a_mul, inside=False):
        """One comet pass: SEGMENTS short strokes, tail->head, tapered.
        inside=True insets each stroke by half its own width so the glow
        stays within the card instead of bleeding into the margin."""
        gfx = self._gfx
        n = self.SEGMENTS
        P_out = self._perim
        for i in range(n):
            k = i / (n - 1)
            a = int(255 * (k ** 1.5) * a_mul)
            if a < 2:
                continue
            hue = self.HUE_START + self.HUE_SPAN * (
                0.5 - 0.5 * math.cos(2 * math.pi *
                                     (t_ms - (n - 1 - i) * self.LAG_MS)
                                     / self.COLOR_CYCLE_MS))
            light = 0.55 + 0.30 * (k ** 4)
            rr, gg, bb = colorsys.hls_to_rgb((hue % 360) / 360.0, light, 0.9)
            argb = ((a << 24) | (int(rr * 255) << 16)
                    | (int(gg * 255) << 8) | int(bb * 255))
            wk = w_head * (0.5 + 0.5 * k)
            pen = _vp()
            if _gd.GdipCreatePen1(argb, wk, 0,
                                  ctypes.byref(pen)):
                continue
            _gd.GdipSetPenStartCap(pen, 2)          # LineCapRound
            _gd.GdipSetPenEndCap(pen, 2)
            s0 = head - tail + i * seg
            if inside:
                off = wk / 2.0
                P_in = P_out - 2 * math.pi * off
                sc_s = P_in / P_out
                x1, y1 = self._rr_point(s0 * sc_s, self._m + off,
                                        self._m + off, self._cw - 2 * off,
                                        self._ch - 2 * off,
                                        max(0.5, self._rad - off), P_in)
                x2, y2 = self._rr_point((s0 + seg * 0.92) * sc_s,
                                        self._m + off, self._m + off,
                                        self._cw - 2 * off, self._ch - 2 * off,
                                        max(0.5, self._rad - off), P_in)
            else:
                x1, y1 = self._point_at(s0)
                x2, y2 = self._point_at(s0 + seg * 0.92)
            _gd.GdipDrawLine(gfx, pen, x1, y1, x2, y2)
            _gd.GdipDeletePen(pen)

    def _render(self, hwnd):
        """Draw one frame into the premultiplied DIB, push via ULW."""
        gfx = self._gfx
        if not gfx or not self._card_poly:
            return
        if self._t0 is None:
            self._t0 = time.monotonic()
        t_ms = (time.monotonic() - self._t0) * 1000.0
        sc = self._sc
        W, H = self.W, self.H
        _gd.GdipGraphicsClear(gfx, 0)               # transparent margin

        # card: dark-glass gradient fill + faint base border
        _gd.GdipFillPolygon(gfx, self._br_card, self._card_poly,
                            self._POLY_N, 0)
        _gd.GdipDrawPolygon(gfx, self._pn_edge, self._card_poly,
                            self._POLY_N)

        # comet: low-alpha wide passes behind, sharp pass on top
        # The smooth comet is composited per-pixel by CometLayer after the card
        # contents are drawn (see below). The old stroke comet is kept only as
        # a fallback if numpy / comet_layer.py is unavailable.
        if self._cm_failed:
            P = self._perim
            head = (t_ms % self.LAP_MS) / self.LAP_MS * P
            tail = self.TAIL * P
            seg = tail / self.SEGMENTS
            for c in range(self.COMETS):
                head_c = head + c * P / self.COMETS
                for ws, am in self.GLOW_PASSES:
                    self._comet(head_c, tail, seg, t_ms,
                                self.GLOW_W * ws * sc, am, inside=True)
                self._comet(head_c, tail, seg, t_ms, self.RING_W * sc, 1.0)

        # ---- card contents (unchanged layout) ----
        m = self._m
        title_y = m + 42.0 * sc
        tx = (W - self._vre_w - self._ac_w) / 2
        # --- animated gradient title (same sweep as the HTML splash) ---
        total_w = self._vre_w + self._ac_w
        tile_w = 2.0 * total_w                      # CSS background-size: 200%
        shift = (t_ms % self.TITLE_SWEEP_MS) / self.TITLE_SWEEP_MS * tile_w
        p1 = _POINTF(tx - shift, 0.0)               # gradient moves LEFT over time
        p2 = _POINTF(tx - shift + tile_w, 0.0)
        tb = _vp()
        if not _gd.GdipCreateLineBrush(ctypes.byref(p1), ctypes.byref(p2),
                                       0xFF475569, 0xFF475569, 0,   # 0 = WrapModeTile
                                       ctypes.byref(tb)):
            cols = (ctypes.c_uint * 5)(0xFF475569, 0xFF10B981, 0xFF06B6D4,
                                       0xFF3B82F6, 0xFF475569)
            pos = (ctypes.c_float * 5)(0.0, 0.25, 0.5, 0.75, 1.0)
            _gd.GdipSetLinePresetBlend(tb, cols, pos, 5)
            brush_vre, brush_ac = tb, tb            # ONE brush for both words
        else:
            tb = None
            brush_vre, brush_ac = self._br_text, self._br_acc   # old look as fallback

        rf = _RECTF(tx, title_y, self._vre_w + 8.0, 34.0 * sc)
        _gd.GdipDrawString(gfx, "VRE ", -1, self._f_title,
                           ctypes.byref(rf), self._fmt_l, brush_vre)
        rf = _RECTF(tx + self._vre_w, title_y, self._ac_w + 8.0, 34.0 * sc)
        _gd.GdipDrawString(gfx, "AC STOCK", -1, self._f_title,
                           ctypes.byref(rf), self._fmt_l, brush_ac)
        if tb:
            _gd.GdipDeleteBrush(tb)                 # created every frame, so free it every frame

        cy = m + 94.0 * sc                        # pulsing dot + AI Scan
        sub_x = (W - (18.0 * sc + self._sub_w)) / 2
        rad = (4.0 + math.sin(t_ms / 350.0) * 1.2) * sc
        _gd.GdipFillEllipse(gfx, self._br_acc,
                            sub_x + 7.0 * sc - rad, cy - rad,
                            rad * 2, rad * 2)
        rf = _RECTF(sub_x + 18.0 * sc, cy - 9.0 * sc,
                    self._sub_w + 8.0, 20.0 * sc)
        _gd.GdipDrawString(gfx, "AI Scan", -1, self._f_small,
                           ctypes.byref(rf), self._fmt_l, self._br_text)

        tw, th = 150.0 * sc, 5.0 * sc             # track + sliding pill
        tx0, ty = (W - tw) / 2, m + 126.0 * sc
        poly, n = self._rrect_poly(tx0, ty, tw, th, th / 2)
        _gd.GdipFillPolygon(gfx, self._br_track, poly, n, 0)
        pw = 46.0 * sc
        pos = (t_ms % 2200.0) / 2200.0 * (tw + pw) - pw
        hx = max(tx0, tx0 + pos)
        hr = min(tx0 + pos + pw, tx0 + tw)
        if hr > hx:
            poly, n = self._rrect_poly(hx, ty, hr - hx, th, th / 2)
            _gd.GdipFillPolygon(gfx, self._br_acchi, poly, n, 0)

        rf = _RECTF(0.0, m + 150.0 * sc, float(W), 22.0 * sc)
        _gd.GdipDrawString(gfx, "Starting services\u2026", -1,
                           self._f_small, ctypes.byref(rf), self._fmt_c,
                           self._br_text)

        cm = self._cm
        if cm is not None:
            try:
                _gd.GdipFlush(gfx, 1)          # GDI+ must finish writing the DIB first
                cm.apply(t_ms)
            except Exception:
                self._cm = None
                self._cm_failed = True

        size = wintypes.SIZE(W, H)
        src = wintypes.POINT(0, 0)
        blend = _BLENDFUNCTION(0, 0, max(0, self._fade_a), 1)  # AC_SRC_ALPHA
        _u32.UpdateLayeredWindow(hwnd, None, None, ctypes.byref(size),
                                 self._mem, ctypes.byref(src), 0,
                                 ctypes.byref(blend), 2)


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

                # 3. App icon — VRE.ico next to the script/exe. Frameless
                #    windows still take taskbar/alt-tab icons from WM_SETICON.
                if os.path.exists(ICON_FILE):
                    LR_LOADFROMFILE = 0x0010
                    LR_DEFAULTSIZE = 0x0040
                    IMAGE_ICON = 1
                    WM_SETICON = 0x0080
                    h_big = _u32.LoadImageW(None, ICON_FILE, IMAGE_ICON,
                                            0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
                    h_sm = _u32.LoadImageW(None, ICON_FILE, IMAGE_ICON,
                                           16, 16, LR_LOADFROMFILE)
                    if h_big:
                        _u32.SendMessageW(hwnd, WM_SETICON, 1, h_big)   # ICON_BIG
                    if h_sm:
                        _u32.SendMessageW(hwnd, WM_SETICON, 0, h_sm)    # ICON_SMALL
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


def is_port_in_use(port, host="127.0.0.1"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind((host, port))
        return False                     # we could take it exclusively -> nobody holds it
    except OSError as e:
        if getattr(e, "winerror", None) == 10048 or e.errno == errno.EADDRINUSE:
            return True                  # held by something -> existing stale-backend path runs
    finally:
        s.close()
    # any other error (e.g. 10013, reserved range): bounded fallback, never an unbounded connect
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c.settimeout(0.3)
    try:
        return c.connect_ex((host, port)) == 0
    finally:
        c.close()


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
    bk.t("cleanup: reading PID file")
    try:
        old_pid = int(open(PID_FILE, encoding='ascii').read().strip())
    except Exception:
        old_pid = None
    bk.t("cleanup: PID file -> %s" % old_pid)
    if old_pid and old_pid != my_pid and _pid_alive(old_pid):
        if not _u32.FindWindowW(None, 'VRE AC Stock'):
            bk.t("cleanup: pid %s alive, no window -> killing zombie" % old_pid)
            _kill_pid(old_pid)  # zombie: process alive, window gone
    bk.t("cleanup: zombie check done")

    _t = time.monotonic()
    in_use = is_port_in_use(BACKEND_PORT)
    bk.t("cleanup: is_port_in_use=%s (%.0fms)" % (in_use, (time.monotonic() - _t) * 1000))
    if in_use:
        _t = time.monotonic()
        healthy = backend_healthy()
        bk.t("cleanup: backend_healthy=%s (%.0fms)" % (healthy, (time.monotonic() - _t) * 1000))
        if not healthy:
            _t = time.monotonic()
            pid = _pid_listening_on(BACKEND_PORT)
            bk.t("cleanup: netstat owner -> pid %s (%.0fms)" % (pid, (time.monotonic() - _t) * 1000))
            if pid and pid != my_pid:
                _kill_pid(pid)
                bk.t("cleanup: killed owner pid %s" % pid)
            _t = time.monotonic()
            for _ in range(50):  # wait for the socket to be released
                if not is_port_in_use(BACKEND_PORT):
                    break
                time.sleep(0.1)
            bk.t("cleanup: port released after %.0fms" % ((time.monotonic() - _t) * 1000))
    bk.t("cleanup_stale_backend done")


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
    bk.t("native splash started")

    # 2. Reap a stale backend (zombie process or dead socket owner), record
    # our PID for the next launch, then boot the backend in parallel.
    cleanup_stale_backend()
    try:
        with open(PID_FILE, 'w', encoding='ascii') as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    # One port probe — bind() is instant on this box; connect probes cost ~2s each.
    # Give the process its own taskbar identity so the icon/pinning isn't
    # shared with other pythonw.exe windows.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            'VRE.ACStock')
    except Exception:
        pass

    port_free = not is_port_in_use(BACKEND_PORT)
    bk.t("port probe -> free=%s" % port_free)
    if port_free:
        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        bk.t("backend thread started")
    # Port already serving a healthy backend = instant relaunch, reuse it.

    # 3. webview import happens AFTER splash is up — overlaps backend boot.
    # WebView2 flags first: keep Chromium rendering while cloaked/occluded.
    bk.apply_webview2_flags()
    import webview
    bk.t("webview imported")

    api = DesktopApi()

    # hidden=True + no initial html: the window initializes and loads the app
    # completely invisible — no dark rectangle, no half-painted splash card
    # ever flashes on screen. The topmost native splash covers the whole boot;
    # on_started reveals the window only once the app has composited a frame.
    window = webview.create_window(
        title='VRE AC Stock',
        js_api=api,
        width=1320,
        height=840,
        min_size=(1050, 680),
        frameless=True,
        easy_drag=False,
        text_select=True,
        hidden=True,
        background_color='#05070a'
    )
    api.set_window(window)
    bk.t("window created")

    # boot_debug_kit: hide the main window while the app loads (DWM cloak ->
    # opacity-on-GUI-thread -> fail-open), confirm a real painted frame, then
    # reveal atomically and let the native splash fade out. All milestones go
    # to boot_debug.log when BOOT_DEBUG=1.
    on_started = bk.make_on_started(
        splash, APP_URL, backend_healthy,
        bring_to_front=api.bring_to_front,
        enable_window_features=api.enable_window_features)

    # Start the desktop window (blocking until closed). os._exit skips the
    # interpreter shutdown that can hang joining threads — the port and all
    # resources are released immediately, so an instant relaunch works.
    # private_mode=False: reuse a persistent WebView2 profile — the runtime's
    # caches survive between launches instead of cold-starting every time.
    bk.t("webview.start() called")
    webview.start(on_started, window, debug=False, private_mode=False)
    os._exit(0)


if __name__ == '__main__':
    main()
