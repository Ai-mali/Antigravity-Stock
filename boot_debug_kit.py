"""
boot_debug_kit.py  -  boot instrumentation + flash-free reveal for the VRE AC Stock desktop app.

Drop this file next to desktop_app.py. It does NOT touch _NativeSplash or comet_layer.py.

What it gives you
-----------------
  t(msg)                      env-gated milestone log (BOOT_DEBUG=1) -> console + boot_debug.log
  apply_webview2_flags()      call once BEFORE `import webview` (stops Chromium from treating a
                              hidden/cloaked/covered window as "occluded" and pausing rendering)
  find_form(w)                locate the WinForms form of a pywebview window (logs how)
  find_hwnd_by_pid(exclude)   locate the main window's HWND without relying on pywebview internals
  BootHider(w, form, hwnd)    hide the main window while it loads, reveal it atomically
                              modes: auto (cloak -> opacity-on-GUI-thread), cloak, opacity, none
  wait_for_paint(w)           stricter "app really painted" check than readyState != 'loading'
  make_on_started(...)        a complete on_started(w) that wires all of the above together

Everything Windows-specific is imported lazily, so the module imports (and its flow can be unit
tested) on any OS.
"""
import os
import sys
import time
import threading

_T0 = time.monotonic()
_DBG = bool(os.environ.get("BOOT_DEBUG"))
_LOG_PATH = os.environ.get("BOOT_DEBUG_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "boot_debug.log")
_log_lock = threading.Lock()


def t(msg):
    """Milestone log. No-op unless BOOT_DEBUG is set."""
    if not _DBG:
        return
    line = "[boot %7.1f ms] %s" % ((time.monotonic() - _T0) * 1000.0, msg)
    with _log_lock:
        try:
            print(line, flush=True)
        except Exception:
            pass
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def apply_webview2_flags():
    """Call BEFORE webview.start() (ideally before `import webview`).
    CalculateNativeWinOcclusion makes Chromium pause rendering/rAF for windows it believes are
    hidden or covered. A cloaked or fully-covered window can trigger that, which would stall the
    'app painted' confirmation. Disabling it keeps the page rendering while invisible."""
    flag = "--disable-features=CalculateNativeWinOcclusion"
    cur = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    if flag not in cur:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (cur + " " + flag).strip()
    t("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=%r" % os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"])


# ----------------------------------------------------------------------------------------------
#  Win32 helpers (lazy; own DLL instances so we never change argtypes on desktop_app's handles)
# ----------------------------------------------------------------------------------------------
DWMWA_TRANSITIONS_FORCEDISABLED = 3
DWMWA_CLOAK = 13
DWMWA_CLOAKED = 14
_api = None


def _get_api():
    global _api
    if _api is None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        d = ctypes.WinDLL("dwmapi", use_last_error=True)
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetWindowRect.restype = wintypes.BOOL
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowVisible.restype = wintypes.BOOL
        u.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
        u.EnumWindows.restype = wintypes.BOOL
        d.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        d.DwmSetWindowAttribute.restype = ctypes.c_long
        d.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        d.DwmGetWindowAttribute.restype = ctypes.c_long
        _api = (u, d, enum_proc)
    return _api


def _hr(hr):
    return "0x%08X" % (hr & 0xFFFFFFFF)



def find_hwnd_by_pid(exclude=(), min_area=40000, class_prefix="WindowsForms", quiet=False):
    """Largest REAL top-level window of this process. Ignores helper windows such as the 1x1
    'GDI+ Hook Window Class' / IME windows (they were picked by mistake in the first Windows run):
    needs area >= min_area and a class name starting with class_prefix (WinForms host window)."""
    import ctypes
    from ctypes import wintypes
    u, _d, enum_proc = _get_api()
    pid = os.getpid()
    found = []

    def cb(hwnd, _lp):
        try:
            p = wintypes.DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid and hwnd not in exclude:
                r = wintypes.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                buf = ctypes.create_unicode_buffer(256)
                u.GetClassNameW(hwnd, buf, 256)
                area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
                found.append((area, hwnd, buf.value, bool(u.IsWindowVisible(hwnd))))
        except Exception:
            pass
        return True

    u.EnumWindows(enum_proc(cb), 0)
    found.sort(reverse=True)
    if not quiet:
        for area, hwnd, cls, vis in found[:6]:
            t("top-level window of this process: hwnd=%s class=%s area=%d visible=%s" % (hwnd, cls, area, vis))
    for area, hwnd, cls, vis in found:
        if area >= min_area and (not class_prefix or cls.startswith(class_prefix)):
            return hwnd
    return None


def wait_for_hwnd_by_pid(exclude=(), timeout_s=10.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        h = find_hwnd_by_pid(exclude, quiet=True)
        if h:
            t("real main window found by pid scan after %.2fs: hwnd=%s" % (time.monotonic() - t0, h))
            return h
        time.sleep(0.03)
    find_hwnd_by_pid(exclude)      # log what exists, for diagnosis
    t("no real main window within %.1fs" % timeout_s)
    return None


def _set_dwm(hwnd, attr, value):
    import ctypes
    _u, d, _e = _get_api()
    v = ctypes.c_int(int(value))
    return d.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))


def is_cloaked(hwnd):
    import ctypes
    from ctypes import wintypes
    _u, d, _e = _get_api()
    v = wintypes.DWORD(0)
    hr = d.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
    return (hr == 0 and v.value != 0), hr, v.value



# pywebview / WinForms helpers

def _try_find_form(w):
    f = getattr(w, "native", None)
    if f is not None:
        return f, "w.native"
    try:
        from webview.platforms import winforms as _wf
        inst = _wf.BrowserView.instances
        uid = getattr(w, "uid", "master")
        f = inst.get(uid)
        if f is not None:
            return f, "instances[%r]" % (uid,)
        if len(inst) == 1:
            return next(iter(inst.values())), "only instance (uid mismatch: %r vs %r)" % (list(inst.keys()), uid)
    except Exception:
        pass
    return None, None


def wait_for_form(w, timeout_s=15.0, poll_s=0.01):
    """pywebview registers the WinForms form SHORTLY AFTER on_started fires (measured: instances was
    empty at on_started on the user's machine). Poll until it exists instead of a one-shot lookup."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        f, how = _try_find_form(w)
        if f is not None:
            t("form found via %s after %.0f ms" % (how, (time.monotonic() - t0) * 1000))
            return f
        time.sleep(poll_s)
    t("form NOT found within %.1fs" % timeout_s)
    return None


def gui_invoke(form, fn, timeout=3.0):
    """Run fn() on the form's GUI thread; wait at most `timeout` s. Returns (ok, error_text).
    Uses BeginInvoke (asynchronous) + an Event so a GUI thread that is not pumping messages can
    never hang the caller (a blocking Invoke / direct property set did exactly that)."""
    try:
        from System import Action
    except Exception as e:
        return False, "System.Action import failed: %r" % (e,)
    done = threading.Event()
    box = {}

    def run():
        try:
            fn()
            box["ok"] = True
        except Exception as e:
            box["err"] = repr(e)
        finally:
            done.set()

    try:
        if form.IsHandleCreated:
            form.BeginInvoke(Action(run))
        else:
            run()          # no handle yet -> no window to marshal to; plain property store is safe
    except Exception as e:
        return False, "BeginInvoke raised %r" % (e,)
    if not done.wait(timeout):
        return False, "GUI thread did not run the call within %.1fs" % timeout
    return bool(box.get("ok")), box.get("err")


def form_hwnd(form, wait_s=3.0):
    """HWND of the form, WITHOUT ever forcing handle creation from this (non-GUI) thread:
    reading form.Handle before the handle exists would create the window on the wrong thread."""
    t0 = time.monotonic()
    while True:
        try:
            if form.IsHandleCreated:
                return int(form.Handle.ToInt64())
        except Exception as e:
            t("form handle read failed: %r" % (e,))
            return None
        if time.monotonic() - t0 > wait_s:
            t("form handle not created within %.1fs" % wait_s)
            return None
        time.sleep(0.05)


# (probe_direct_opacity was REMOVED: a direct cross-thread form.Opacity set deadlocks on the
#  user's machine. Never touch WinForms properties from a worker thread; use gui_invoke.)


# Hide while loading / reveal atomically
class BootHider:
    def __init__(self, w, form, hwnd, mode=None):
        self.w, self.form, self.hwnd = w, form, hwnd
        self.mode = (mode or os.environ.get("BOOT_HIDE") or "auto").lower()
        self.active = None            # "cloak" | "opacity" | None
        self.revealed = False

    def hide(self):
        t("hide(): mode=%s hwnd=%s form=%s" % (self.mode, self.hwnd, self.form is not None))
        if self.mode == "none":
            t("hide(): mode none -> window will be visible while loading")
            return
        if self.mode in ("auto", "cloak") and self.hwnd:
            try:
                hr0 = _set_dwm(self.hwnd, DWMWA_TRANSITIONS_FORCEDISABLED, 1)  # no show/hide animation
                hr = _set_dwm(self.hwnd, DWMWA_CLOAK, 1)
                ok, hrg, val = is_cloaked(self.hwnd)
                t("DWM transitions-disable hr=%s | cloak set hr=%s | cloaked now=%s (get hr=%s val=%s)"
                  % (_hr(hr0), _hr(hr), ok, _hr(hrg), val))
                if hr == 0 and ok:
                    self.active = "cloak"
                    return
                t("cloak did not take effect")
            except Exception as e:
                t("cloak raised %r" % (e,))
        if self.mode in ("auto", "cloak", "opacity") and self.form is not None:
            ok, err = gui_invoke(self.form, lambda: setattr(self.form, "Opacity", 0.0))
            t("Opacity=0 on GUI thread: ok=%s err=%s" % (ok, err))
            if ok:
                self.active = "opacity"
                return
        t("WARNING: no hide method worked -> the window WILL be visible (dark) while loading")

    def reveal(self):
        if self.revealed:
            return
        self.revealed = True
        if self.active == "cloak":
            hr = _set_dwm(self.hwnd, DWMWA_CLOAK, 0)
            ok, _hrg, _val = is_cloaked(self.hwnd)
            t("uncloak hr=%s  still cloaked=%s" % (_hr(hr), ok))
        elif self.active == "opacity":
            ok, err = gui_invoke(self.form, lambda: setattr(self.form, "Opacity", 1.0))
            t("Opacity=1 on GUI thread: ok=%s err=%s" % (ok, err))
        else:
            t("reveal(): nothing was hidden")



# NOTE: evaluate_js in pywebview 6.2.1 does NOT resolve Promises (an async IIFE came back as {}),
# so this script is fully SYNCHRONOUS. A tiny rAF counter is installed once per document; we poll it.
PAINT_JS = r"""(function () {
  try {
    if (!window.__bk) {
      window.__bk = {n: 0};
      (function f() { window.__bk.n++; if (window.__bk.n < 90) requestAnimationFrame(f); })();
    }
    var b = document.body;
    return JSON.stringify({
      href: location.href, rs: document.readyState, vis: document.visibilityState,
      fonts: (document.fonts ? document.fonts.status : 'na'), n: window.__bk.n,
      flag: (window.__READY === undefined ? null : !!window.__READY),
      bg: (b ? getComputedStyle(b).backgroundColor : null)
    });
  } catch (e) { return JSON.stringify({err: String(e)}); }
})()"""


def _parse_js(res):
    import json
    if isinstance(res, dict):
        return res
    if isinstance(res, str):
        try:
            d = json.loads(res)
            return d if isinstance(d, dict) else None
        except Exception:
            return None
    return None


def wait_for_paint(w, timeout_s=20.0, poll_s=0.1, unusable_grace_s=4.0):
    """Poll until the app page is fully loaded and >=3 animation frames ran on THIS document.
    Returns (confirmed, last_result). If evaluate_js never gives a usable answer for
    `unusable_grace_s`, gives up early (fail-open) instead of holding the splash for the full timeout."""
    t_start = time.monotonic()
    deadline = t_start + timeout_s
    n, last, ever_usable = 0, None, False
    while time.monotonic() < deadline:
        try:
            res = _parse_js(w.evaluate_js(PAINT_JS))
        except Exception as e:
            res = None
            last = "evaluate_js raised %r" % (e,)
        n += 1
        if res is not None and "href" in res:     # {} (unresolved Promise) or {err:..} is NOT usable
            ever_usable = True
            last = res
            if (("app_mode" in (res.get("href") or "")) and res.get("rs") == "complete"
                    and res.get("fonts") in ("loaded", "na") and (res.get("n") or 0) >= 3
                    and res.get("flag") in (None, True)):
                t("paint CONFIRMED after %d polls: %r" % (n, res))
                return True, res
            if n % 10 == 1:
                t("paint not yet: %r" % (res,))
        elif not ever_usable and time.monotonic() - t_start > unusable_grace_s:
            t("evaluate_js gave no usable result for %.1fs (last=%r) -> giving up the paint check"
              % (unusable_grace_s, last))
            return False, last
        time.sleep(poll_s)
    t("paint-confirm TIMEOUT after %.1fs, last=%r" % (timeout_s, last))
    return False, last


# ----------------------------------------------------------------------------------------------
#  Complete on_started
# ----------------------------------------------------------------------------------------------
def make_on_started(splash, app_url, backend_healthy, bring_to_front=None,
                    enable_window_features=None, backend_wait_s=30.0,
                    paint_timeout_s=20.0, settle_s=0.25, form_wait_s=15.0):
    """Returns on_started(w) for webview.start(on_started, window, ...).
    Order: WAIT for the real form/HWND -> hide (cloak) -> show (invisible) -> backend healthy ->
    load app -> paint confirmed -> bring to front (still invisible) -> reveal atomically -> settle ->
    splash.close() (fade-out) -> bring to front again.
    Fail-open: the window is ALWAYS revealed and the splash ALWAYS closed, even on errors."""
    def on_started(w):
        t("on_started entered (thread=%s)" % threading.current_thread().name)
        hider = None
        try:
            splash_hwnd = getattr(splash, "_hwnd", None)
            excl = {splash_hwnd} if splash_hwnd else ()
            form = wait_for_form(w, timeout_s=form_wait_s)
            if form is not None:
                # handle may not exist yet; then BootHider falls back to Opacity=0, which is safe
                # before the handle exists (no window to marshal to) and applies at creation.
                hwnd = form_hwnd(form, wait_s=1.0)
            else:
                hwnd = wait_for_hwnd_by_pid(excl, timeout_s=8.0)
            t("main window hwnd=%s (splash hwnd=%s) form=%s" % (hwnd, splash_hwnd, form is not None))

            hider = BootHider(w, form, hwnd)
            hider.hide()                      # BEFORE show(), so no visible frame can ever exist
            t("hider.active=%s" % (hider.active,))

            w.show()
            t("w.show() returned")

            t0 = time.monotonic()
            healthy = False
            while time.monotonic() - t0 < backend_wait_s:
                try:
                    if backend_healthy():
                        healthy = True
                        break
                except Exception:
                    pass
                time.sleep(0.1)
            t("backend_healthy=%s after %.2fs" % (healthy, time.monotonic() - t0))

            w.load_url(app_url)
            t("load_url() returned")

            confirmed, _res = wait_for_paint(w, timeout_s=paint_timeout_s)
            t("paint confirmed=%s" % confirmed)
        except Exception as e:
            t("on_started raised %r" % (e,))
        finally:
            # z-order first, while the window is still hidden, so it does not end up behind the IDE
            try:
                if bring_to_front:
                    bring_to_front()
            except Exception as e:
                t("pre-reveal bring_to_front raised %r" % (e,))
            try:
                if hider is not None:
                    hider.reveal()
            except Exception as e:
                t("reveal raised %r" % (e,))
            time.sleep(settle_s)
            try:
                splash.close()
                t("splash.close() sent")
            except Exception as e:
                t("splash.close raised %r" % (e,))
            for fn in (bring_to_front, enable_window_features):
                try:
                    if fn:
                        fn()
                except Exception as e:
                    t("post-reveal call raised %r" % (e,))
            t("on_started done")
    return on_started
