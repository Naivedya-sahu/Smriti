"""
screen.py — Smriti screenshot + workarea layer (separate from pen capture).

Reads the screen from xochitl's memory over the persistent tablet shell
(goMarkableStream, SMRITI_SCREEN_URL, only as fallback) and turns real pixels into placement facts: where ink
is, where free space is.

Layer test CLI:
    python host/screen.py shot out.png     # grab a screenshot
    python host/screen.py floor            # lowest inked row
    python host/screen.py bands            # free horizontal bands
"""

from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image

CANVAS_W, CANVAS_H = 1404, 1872


def screen_url() -> str:
    return os.environ.get("SMRITI_SCREEN_URL", "https://10.11.99.1:2001")


# rM2 fw >= 3.24: xochitl keeps the screen as BGRA 1404x1872 in the anonymous
# mapping right after /dev/fb0, at +2629632 (+8 header) -- goMarkableStream
# internal/remarkable/pointer.go + detect.go; checked on fw 3.27 by
# cross-correlating against a gms frame (2026-09-30). 2629632 = 642 pages.
FB_OFFSET, FB_PAGES = 2629632, 2568          # 2568 pages >= 8 + 1404*1872*4
FB_SCRIPT = ("p=$(pidof xochitl) && a=$(grep -A1 /dev/fb0 /proc/$p/maps | tail -n1 | "
             "cut -d- -f1) && dd if=/proc/$p/mem bs=4096 "
             f"skip=$(((0x$a+{FB_OFFSET})/4096)) count={FB_PAGES} 2>/dev/null | gzip -1")


def fb_decode(gz: bytes) -> Image.Image | None:
    """gzip'd raw read -> RGB page; None on a short/garbled read."""
    import gzip
    try:
        raw = gzip.decompress(gz)[8:8 + CANVAS_W * CANVAS_H * 4]
    except (OSError, EOFError):
        return None
    if len(raw) < CANVAS_W * CANVAS_H * 4:
        return None
    a = np.frombuffer(raw, np.uint8).reshape(CANVAS_H, CANVAS_W, 4)
    return Image.fromarray(np.ascontiguousarray(a[..., 2::-1]), "RGB")


def fb_screenshot(host: str | None = None) -> Image.Image | None:
    """Visible screen straight from xochitl's memory over the persistent
    shell: no service, no login, pid resolved per call (0.45 s on device)."""
    import tablet
    host = host or os.environ.get("SMRITI_TABLET_HOST", "rm2")
    try:
        rc, gz = tablet.run(host, FB_SCRIPT, timeout=15)
    except OSError:
        return None
    return fb_decode(gz) if rc == 0 else None


def screenshot(url: str | None = None,
               user: str = "admin", password: str = "password"
               ) -> Image.Image | None:
    """Actual visible screen: framebuffer read first, goMarkableStream's
    /screenshot (JWT login) only when that fails (other firmware).
    Preserve RGB for the reader; analysis functions convert to gray as needed.
    Returns None if neither answers."""
    img = fb_screenshot()
    return img if img is not None else gms_screenshot(url, user, password)


def gms_screenshot(url: str | None = None,
                   user: str = "admin", password: str = "password"
                   ) -> Image.Image | None:
    """goMarkableStream fallback. Returns None if the service is unreachable."""
    import io
    import json
    import ssl
    import urllib.request
    url = url or screen_url()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE      # gms uses a self-signed cert
    try:
        body = json.dumps({"username": user, "password": password}).encode()
        req = urllib.request.Request(url + "/login", data=body,
                                     headers={"Content-Type": "application/json"})
        tok = json.load(urllib.request.urlopen(req, context=ctx, timeout=10))["token"]
        req = urllib.request.Request(url + "/screenshot",
                                     headers={"Authorization": f"Bearer {tok}"})
        data = urllib.request.urlopen(req, context=ctx, timeout=30).read()
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None


# xochitl UI that shows up in screenshots but is NOT page ink: toolbar column
# (black tool block + icons when the toolbar is open), top row (toolbar
# toggle, close), and the Smriti floating eye (bottom-right). Unmasked, an
# open toolbar read as ink rows 112-1830 -> "no room" on a blank page
# (device, 2026-09-26).
UI_BOXES = [(0, 0, 120, CANVAS_H), (0, 0, CANVAS_W, 100),
            (1290, 1760, CANVAS_W, CANVAS_H),
            # ~5 s after a page turn: 'Page n of m' label + scrollbar (run 7).
            # ponytail: fixed boxes also hide real ink there; mask by time if it bites
            (560, 1810, 850, CANVAS_H), (1335, 0, 1365, CANVAS_H)]


def ink_mask(img: Image.Image, thresh: int = 100) -> np.ndarray:
    """Dark-pixel mask of the page with UI zones blanked."""
    a = np.asarray(img.convert("L").resize((CANVAS_W, CANVAS_H))) < thresh
    for x0, y0, x1, y1 in UI_BOXES:
        a[y0:y1, x0:x1] = False
    return a


def stroke_mask(img: Image.Image) -> np.ndarray:
    """ink_mask without the template: the dot grid is isolated 2x2 dots (4 px,
    ~42 px apart, device 2026-09-30), so a dark pixel with <= 4 dark pixels
    in its 5x5 window is a dot; any stroke, even a 1 px graph line, has more."""
    a = ink_mask(img)
    s = np.pad(a.astype(np.int32), ((3, 2), (3, 2))).cumsum(0).cumsum(1)
    win = s[5:, 5:] - s[:-5, 5:] - s[5:, :-5] + s[:-5, :-5]
    m = a & (win >= 5)
    # vector hybrid: a frame may carry the page's own strokes (monke
    # _attach_vectors), computed once on first use, None when unusable
    if "vectors" in img.info and "vectors_mask" not in img.info:
        img.info["vectors_mask"] = img.info["vectors"](a)
    v = img.info.get("vectors_mask")
    return m | v if v is not None else m


def _ink_rows(img: Image.Image, min_px: int) -> np.ndarray:
    return stroke_mask(img).sum(axis=1) >= min_px


def ink_floor(img: Image.Image, min_px: int = 3) -> int:
    """Lowest page y that holds ink, from a real screenshot. Dots filtered
    by stroke_mask, so a thin line counts (run 14: a graph at 5-19 dark
    px/row read as free under the old 80 px/row threshold -> reply inked
    over it). Returns 0 for a blank page."""
    rows = np.nonzero(_ink_rows(img, min_px))[0]
    return int(rows[-1]) if len(rows) else 0


def free_bands(img: Image.Image, min_h: int = 120,
               min_px: int = 3) -> list[tuple[int, int]]:
    """Empty horizontal bands (y0, y1) on the page — the workarea parser.
    Same stroke test as ink_floor. Bands shorter than min_h are dropped; a
    fully blank page returns [(0, CANVAS_H)]."""
    inked = _ink_rows(img, min_px)
    bands, y0 = [], 0
    for y in np.nonzero(inked)[0]:
        if int(y) - y0 >= min_h:
            bands.append((y0, int(y)))
        y0 = int(y) + 1
    if CANVAS_H - y0 >= min_h:
        bands.append((y0, CANVAS_H))
    return bands


def looks_corrupt(img: Image.Image, frac: float = 0.04) -> bool:
    """goMarkableStream sometimes serves a stale/offset buffer after
    xochitl redraws: wide bands of dense noise. On a paper-white page no
    real row is >40% dark; many such rows = garbage frame."""
    a = np.asarray(img.convert("L").resize((CANVAS_W, CANVAS_H))) < 100
    return float((a.sum(axis=1) > CANVAS_W * 0.4).mean()) > frac


def looks_valid(img: Image.Image, template: str = "dots",
                grid_min: float = 0.25) -> bool:
    """Frame sanity gate — placement facts must never come from a corrupt/
    offset/rotated buffer. Checks (calibrated on real gms frames: good page
    mean≈245 dark≈4% grid-peak≈0.54; corrupt sample mean≈164 dark≈34%):
      1. paper mostly white (mean > 200, dark < 25%)
      2. no dense noise bands (looks_corrupt)
      3. template='dots': the dot grid shows as a column-projection
         autocorrelation peak (lag 20-120 px). Offset/wraparound/rotated
         buffers scramble it. Set [screen] template='none' for blank/lined
         notebooks.
    ponytail: grid_min=0.25 measured on one device — recalibrate knob if a
    heavily-inked page ever false-rejects (fallback is floor-only, safe)."""
    a = np.asarray(img.convert("L").resize((CANVAS_W, CANVAS_H)))
    dark = a < 100
    if a.mean() < 200 or dark.mean() > 0.25 or looks_corrupt(img):
        return False
    if template == "dots":
        # clip per-column ink: long vertical strokes (graph axes, circuit
        # wires) otherwise drown the dot-grid period — an inked plot page
        # measured 0.19 raw, 0.50 clipped (device, 2026-09-26)
        # Either axis may carry the period: ink confined to a row band
        # (erase residue across 1000 columns, device 2026-09-29) drowns the
        # column projection but not the row one. Offset/wraparound noise
        # scrambles both.
        def peak(p):
            p = np.minimum(p, max(4.0, 2 * float(np.percentile(p, 75)))).astype(float)
            p -= p.mean()
            ac = np.correlate(p, p, "full")[p.size - 1:]
            return float(max(ac[20:120]) / ac[0]) if ac[0] > 0 else 0.0
        if max(peak(dark.sum(axis=0)), peak(dark.sum(axis=1))) < grid_min:
            return False
    return True


def grab(url: str | None = None, host: str | None = None,
         user: str = "admin", password: str = "password",
         template: str = "dots") -> Image.Image | None:
    """screenshot() + validity gate. An invalid frame (mid-redraw, noise,
    lost grid) gets one regrab; when only goMarkableStream answers, it is
    restarted first (it caches xochitl's pid and serves stale buffers after
    a xochitl restart). Returns None when no VALID frame can be had —
    callers degrade to stroke/persisted floor, never place ink off a bad
    buffer."""
    import time
    import tablet
    host = host or os.environ.get("SMRITI_TABLET_HOST", "rm2")
    img = screenshot(url, user, password)
    if img is not None and looks_valid(img, template):
        return img
    if fb_screenshot(host) is None:
        print("[screen] no framebuffer read — restarting goMarkableStream", flush=True)
        try:
            tablet.run(host, "systemctl restart goMarkableStream", timeout=15)
        except OSError:
            pass
        time.sleep(3)
    else:
        time.sleep(0.5)                       # xochitl mid-redraw: settle, regrab
    img = screenshot(url, user, password)
    if img is not None and not looks_valid(img, template):
        return None
    return img


def page_diff(before: Image.Image, after: Image.Image,
              thresh: int = 60, min_px: int = 250
              ) -> tuple[int, int, int, int] | None:
    """Bounding box (x0, y0, x1, y1) of what changed between two
    screenshots, or None when nothing meaningful changed. This is the
    backfeed guard: after Smriti inks a reply the baseline is refreshed,
    so its own ink never reads as new user input."""
    a = np.asarray(before.convert("L").resize((CANVAS_W, CANVAS_H)), dtype=np.int16)
    b = np.asarray(after.convert("L").resize((CANVAS_W, CANVAS_H)), dtype=np.int16)
    changed = np.abs(a - b) > thresh
    for x0, y0, x1, y1 in UI_BOXES:      # toolbar open/close, eye state
        changed[y0:y1, x0:x1] = False    # flips are not page changes
    if int(changed.sum()) < min_px:
        return None
    ys, xs = np.nonzero(changed)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    img = screenshot()
    if img is None:
        sys.exit(f"screenshot service unreachable at {screen_url()} "
                 "(set SMRITI_SCREEN_URL)")
    if cmd == "shot":
        out = sys.argv[2] if len(sys.argv) > 2 else "screen.png"
        img.save(out)
        print(out)
    elif cmd == "floor":
        print(ink_floor(img))
    elif cmd == "bands":
        for y0, y1 in free_bands(img):
            print(f"free band: y {y0}-{y1} ({y1 - y0}px)")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
