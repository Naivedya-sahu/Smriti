"""
screen.py — Smriti screenshot + workarea layer (separate from pen capture).

Talks to goMarkableStream on the tablet (USB or tailnet, set
SMRITI_SCREEN_URL) and turns real pixels into placement facts: where ink
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


def screenshot(url: str | None = None,
               user: str = "admin", password: str = "password"
               ) -> Image.Image | None:
    """Actual visible screen via goMarkableStream's /screenshot (JWT login).
    Returns grayscale PIL image, or None if the service is unreachable."""
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
        return Image.open(io.BytesIO(data)).convert("L")
    except Exception:
        return None


def ink_floor(img: Image.Image, min_dark_px: int = 80) -> int:
    """Lowest page y that holds ink, from a real screenshot. Template dots
    are sparse (~35 dark px/row); real ink rows are dense — threshold splits
    them. Returns 0 for a blank page."""
    a = np.asarray(img.resize((CANVAS_W, CANVAS_H))) < 100
    rows = np.nonzero(a.sum(axis=1) >= min_dark_px)[0]
    return int(rows[-1]) if len(rows) else 0


def free_bands(img: Image.Image, min_h: int = 120,
               min_dark_px: int = 80) -> list[tuple[int, int]]:
    """Empty horizontal bands (y0, y1) on the page — the workarea parser.
    Same row-density threshold as ink_floor, so template dots don't count
    as ink. Bands shorter than min_h are dropped; a fully blank page
    returns [(0, CANVAS_H)]."""
    a = np.asarray(img.resize((CANVAS_W, CANVAS_H))) < 100
    inked = a.sum(axis=1) >= min_dark_px
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
    a = np.asarray(img.resize((CANVAS_W, CANVAS_H))) < 100
    return float((a.sum(axis=1) > CANVAS_W * 0.4).mean()) > frac


def grab(url: str | None = None, host: str | None = None) -> Image.Image | None:
    """screenshot() + corruption auto-heal: a corrupt frame triggers a
    goMarkableStream restart over ssh and one regrab."""
    import subprocess
    import time
    img = screenshot(url)
    if img is not None and not looks_corrupt(img):
        return img
    host = host or os.environ.get("SMRITI_TABLET_HOST", "rm2")
    print("[screen] corrupt/absent frame — restarting goMarkableStream",
          flush=True)
    subprocess.run(["ssh", host, "systemctl restart goMarkableStream"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    img = screenshot(url)
    if img is not None and looks_corrupt(img):
        return None
    return img


def page_diff(before: Image.Image, after: Image.Image,
              thresh: int = 60, min_px: int = 250
              ) -> tuple[int, int, int, int] | None:
    """Bounding box (x0, y0, x1, y1) of what changed between two
    screenshots, or None when nothing meaningful changed. This is the
    backfeed guard: after Smriti inks a reply the baseline is refreshed,
    so its own ink never reads as new user input."""
    a = np.asarray(before.resize((CANVAS_W, CANVAS_H)), dtype=np.int16)
    b = np.asarray(after.resize((CANVAS_W, CANVAS_H)), dtype=np.int16)
    changed = np.abs(a - b) > thresh
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
