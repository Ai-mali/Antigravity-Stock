import sys, time, ctypes
sys.path.insert(0, '.')
import desktop_app as d
from ctypes import wintypes
from PIL import ImageGrab

sp = d._NativeSplash()
t_start = time.monotonic()
sp.start()
sp._ready.wait(3)
print("window up at t=%.2fs, hwnd=%s" % (time.monotonic() - t_start, bool(sp._hwnd)))

t_cm = None
shots = 0
while time.monotonic() - t_start < 12:
    time.sleep(0.05)
    if t_cm is None and sp._cm is not None:
        t_cm = time.monotonic() - t_start
        print("comet layer ready at t=%.2fs" % t_cm)
    if sp._hwnd and shots < 4 and time.monotonic() - t_start > shots * 3.0:
        r = wintypes.RECT()
        d._u32.GetWindowRect(sp._hwnd, ctypes.byref(r))
        ImageGrab.grab(bbox=(r.left - 30, r.top - 30,
                             r.right + 30, r.bottom + 30)).save(
            "_ns_shot%d.png" % shots)
        shots += 1

sp.close()
time.sleep(0.4)
print("cm_failed:", sp._cm_failed, "| comet appeared at:", t_cm, "s")
print("done")
