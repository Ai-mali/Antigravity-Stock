"""
comet_layer.py - smooth "shooting star" border for the native splash.

Why this exists
---------------
The GDI+ version draws the comet as ~30 separate short strokes per pass.
Where neighbouring strokes overlap, their alpha is blended twice, which
shows up as a beaded / dashed line, and GDI+ has no blur, so the "glow"
is just a few flat translucent bands.

This module instead computes the comet per pixel (analytically) for the
thin band of pixels around the card border, and composites it straight
into the splash window's premultiplied BGRA DIB memory:
  * sharp ring: anti-aliased, tapering width, color-lagged tail
  * glow: true Gaussian falloff outside the card edge
No overlapping strokes, so no beads. Geometry is precomputed once, so
each frame only touches ~10k pixels (well under 2 ms with numpy).
"""
import math
import numpy as np


def _hsl_to_rgb(h, l, s=0.9):
    """Vectorised HSL -> RGB. h in degrees, l/s in 0..1. Returns r, g, b (0..1)."""
    a = s * np.minimum(l, 1.0 - l)
    out = []
    for n in (0.0, 8.0, 4.0):
        k = np.mod(n + h / 30.0, 12.0)
        out.append(l - a * np.clip(np.minimum(k - 3.0, 9.0 - k), -1.0, 1.0))
    return out


class CometLayer:
    def __init__(self, arr, margin, card_w, card_h, radius, scale=1.0,
                 lap_ms=3000.0, cycle_ms=6000.0, tail=0.24,
                 lag_total_ms=1595.0, hue_start=155.0, hue_span=120.0,
                 ring_w=2.4, glow_sigma=6.5, glow_amp=0.85,
                 comets=2, color_offset_ms=0.0):
        """arr: (H, W, 4) uint8 view of the premultiplied BGRA DIB (shares memory).
        All sizes are PHYSICAL pixels (already multiplied by DPI scale).
        comets: how many comets run around the card, spread evenly
                (2 = the second one is 180 degrees / half a lap behind).
        color_offset_ms: color-cycle offset between comets (0 = same colors;
                set to colorCycle/2 so one is green while the other is purple)."""
        self.arr = arr
        self.flat = arr.reshape(-1, 4)
        H, W = arr.shape[:2]
        sc = float(scale)
        self.sc = sc
        self.lap_ms, self.cycle_ms = float(lap_ms), float(cycle_ms)
        self.lag_total_ms = float(lag_total_ms)
        self.hue_start, self.hue_span = float(hue_start), float(hue_span)
        self.ring_w = float(ring_w) * sc
        self.sig_o = float(glow_sigma) * sc      # glow falloff outside the card
        self.sig_i = 1.6 * sc                    # glow falloff inside (hidden mostly)
        self.glow_amp = float(glow_amp)
        self.n_comets = max(1, int(comets))
        self.color_offset_ms = float(color_offset_ms)

        r = float(radius)
        hx, hy = card_w / 2.0, card_h / 2.0
        cx, cy = margin + hx, margin + hy
        ix, iy = hx - r, hy - r
        sx, sy = card_w - 2 * r, card_h - 2 * r
        arcl = math.pi * r / 2.0
        self.P = 2 * sx + 2 * sy + 4 * arcl
        self.L = float(tail) * self.P

        # ---- one-time geometry: signed distance d (outside > 0) and
        # ---- arc-length s along the border (clockwise from top-left start)
        yy, xx = np.mgrid[0:H, 0:W]
        ux = xx.astype(np.float32) + 0.5 - cx
        uy = yy.astype(np.float32) + 0.5 - cy
        qx = np.abs(ux) - ix
        qy = np.abs(uy) - iy
        corner = (qx > 0) & (qy > 0)
        vert = (~corner) & (qx > qy)
        horiz = (~corner) & (~vert)
        sgx = np.where(ux < 0, -1.0, 1.0).astype(np.float32)
        sgy = np.where(uy < 0, -1.0, 1.0).astype(np.float32)

        d = np.where(corner, np.hypot(qx, qy) - r,
                     np.where(vert, qx - r, qy - r)).astype(np.float32)

        phi = np.arctan2(qy, qx)
        ang = np.arctan2(sgy * np.sin(phi), sgx * np.cos(phi))
        s_tr = sx + r * (ang + math.pi / 2)
        s_br = sx + arcl + sy + r * ang
        s_bl = 2 * sx + 2 * arcl + sy + r * (ang - math.pi / 2)
        s_tl = 2 * sx + 3 * arcl + 2 * sy + r * (ang + math.pi)
        s_corner = np.where(sgx > 0, np.where(sgy < 0, s_tr, s_br),
                            np.where(sgy > 0, s_bl, s_tl))
        s_vert = np.where(sgx > 0, sx + arcl + (uy + iy),
                          2 * sx + 3 * arcl + sy + (iy - uy))
        s_horiz = np.where(sgy < 0, ux + ix,
                           sx + 2 * arcl + sy + (ix - ux))
        s = np.where(corner, s_corner, np.where(vert, s_vert, s_horiz)).astype(np.float32)

        b_out = 3.3 * self.sig_o
        b_in = self.ring_w + 2.0
        band = (d > -b_in) & (d < b_out)
        self.idx = np.flatnonzero(band.ravel())
        self.d = d.ravel()[self.idx]
        self.s = s.ravel()[self.idx]

    def apply(self, t_ms):
        """Composite all comets for time t_ms onto the DIB (premultiplied over)."""
        P = self.P
        base = (t_ms % self.lap_ms) / self.lap_ms * P
        for c in range(self.n_comets):
            head = (base + c * P / self.n_comets) % P     # c=1 of 2 -> 180 deg behind
            self._one(head, t_ms + c * self.color_offset_ms)

    def _one(self, head, t_ms):
        """Composite ONE comet whose head is at arc-length `head`."""
        P, L, sc = self.P, self.L, self.sc
        delta = np.mod(head - self.s + P / 2.0, P) - P / 2.0     # > 0 behind the head
        m = (delta > -3.0 * self.sig_o) & (delta < L)
        sel = np.flatnonzero(m)
        if sel.size == 0:
            return
        dl = delta[sel]
        d = self.d[sel]
        u = np.clip(dl / L, 0.0, 1.0)            # 0 at head -> 1 at tail end
        k = 1.0 - u

        # Distance from the head point for pixels ahead of it -> round tip
        # (instead of a flat cut) for both the ring and the glow.
        ahead = np.minimum(dl, 0.0)
        rad = np.hypot(d, ahead)

        hue = self.hue_start + self.hue_span * (
            0.5 - 0.5 * np.cos(2 * math.pi * (t_ms - u * self.lag_total_ms) / self.cycle_ms))
        light = 0.55 + 0.30 * k ** 4
        r, g, b = _hsl_to_rgb(hue, light)

        # glow: Gaussian falloff, strong outside the card, tiny inside
        gauss = np.where(d > 0, np.exp(-0.5 * (rad / self.sig_o) ** 2),
                         np.exp(-0.5 * (rad / self.sig_i) ** 2))
        glow_a = np.clip(self.glow_amp * gauss * (k ** 1.2), 0.0, 0.95)

        # sharp ring: anti-aliased, tapering width, round head cap
        w = self.ring_w * (0.5 + 0.5 * k)
        cov = np.clip(w / 2.0 + 0.5 - rad, 0.0, 1.0)
        ring_a = np.clip(cov * (k ** 1.5), 0.0, 1.0)

        pix = self.flat[self.idx[sel]].astype(np.float32) / 255.0   # B,G,R,A premult
        for a in (glow_a, ring_a):                                   # glow first, ring on top
            ia = 1.0 - a
            pix[:, 0] = b * a + pix[:, 0] * ia
            pix[:, 1] = g * a + pix[:, 1] * ia
            pix[:, 2] = r * a + pix[:, 2] * ia
            pix[:, 3] = a + pix[:, 3] * ia
        self.flat[self.idx[sel]] = (np.clip(pix, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
