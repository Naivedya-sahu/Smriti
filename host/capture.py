"""
capture.py — Smriti pen input capture (v0.1.2).

Streams raw wacom events from the RM2 over ssh (`cat /dev/input/event1`) —
no device-side server needed. Builds strokes, and after an idle period with
the pen up, "commits" the page: renders strokes to a PNG.

    python host/capture.py -o page.png              # exit after first commit
    python host/capture.py --watch                  # page_001.png, page_002.png, ...
    python host/capture.py --idle 2.8 --min-strokes 1

Works with the real EMR pen and with lamp-injected strokes alike (both write
to the same evdev node), so the pipeline is testable without a pen.
"""

from __future__ import annotations

import argparse
import queue
import struct
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# armv7 input_event: u32 sec, u32 usec, u16 type, u16 code, s32 value
EV_FMT = "<IIHHi"
EV_SIZE = struct.calcsize(EV_FMT)
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, ABS_PRESSURE = 0, 1, 24
BTN_TOUCH = 330

CANVAS_W, CANVAS_H = 1404, 1872
WACOM_X_MAX, WACOM_Y_MAX = 15725.0, 20966.0
MIN_PRESSURE = 200  # hover noise floor


def to_screen(ax: int, ay: int) -> tuple[int, int]:
    """Wacom axes → screen px. Axes are swapped + y-flipped (see lamp PEN_X/Y)."""
    return (int(ay * CANVAS_W / WACOM_X_MAX),
            CANVAS_H - int(ax * CANVAS_H / WACOM_Y_MAX))


class Capture:
    def __init__(self, host: str = "rm2", device: str = "/dev/input/event1"):
        self.proc = subprocess.Popen(
            ["ssh", host, f"cat {device}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.q: queue.Queue[tuple] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        buf = b""
        while True:
            # raw read: return whatever is available, don't block for a full block
            chunk = self.proc.stdout.raw.read(EV_SIZE * 256)
            if not chunk:
                self.q.put(None)
                return
            buf += chunk
            while len(buf) >= EV_SIZE:
                self.q.put(struct.unpack(EV_FMT, buf[:EV_SIZE]))
                buf = buf[EV_SIZE:]

    def close(self):
        self.proc.kill()


class StrokeBuilder:
    """Feed raw events, get completed strokes back."""

    def __init__(self, min_pressure: int = MIN_PRESSURE):
        self.min_pressure = min_pressure
        self.ax = self.ay = self.pressure = 0
        self.touching = False
        self.cur: list[tuple[int, int]] = []

    def feed(self, ev) -> list[tuple[int, int]] | None:
        """Returns a finished stroke on pen-up, else None."""
        _, _, etype, code, value = ev
        if etype == EV_ABS:
            if code == ABS_X:
                self.ax = value
            elif code == ABS_Y:
                self.ay = value
            elif code == ABS_PRESSURE:
                self.pressure = value
        elif etype == EV_KEY and code == BTN_TOUCH:
            self.touching = bool(value)
            done, self.cur = self.cur, []
            if not value and len(done) > 1:
                return done
        elif etype == EV_SYN and self.touching and self.pressure >= self.min_pressure:
            self.cur.append(to_screen(self.ax, self.ay))
        return None


def render_strokes(strokes: list[list[tuple[int, int]]],
                   crop: bool = False, pad: int = 30) -> Image.Image:
    """Strokes → PIL image. crop=True trims to ink bbox (+pad) — far fewer
    vision tokens for a few lines of writing than a full blank page."""
    img = Image.new("L", (CANVAS_W, CANVAS_H), 255)
    d = ImageDraw.Draw(img)
    for s in strokes:
        if len(s) > 1:
            d.line(s, fill=0, width=4)
        elif s:
            d.ellipse([s[0][0] - 2, s[0][1] - 2, s[0][0] + 2, s[0][1] + 2], fill=0)
    if crop and strokes:
        xs = [x for s in strokes for x, _ in s]
        ys = [y for s in strokes for _, y in s]
        img = img.crop((max(0, min(xs) - pad), max(0, min(ys) - pad),
                        min(CANVAS_W, max(xs) + pad), min(CANVAS_H, max(ys) + pad)))
    return img


def strokes_to_png(strokes: list[list[tuple[int, int]]], out: Path) -> None:
    render_strokes(strokes).save(out)


# Screenshot + workarea analysis moved to screen.py (separate layer).


def run(host: str, idle: float, out: str, watch: bool, min_strokes: int) -> None:
    cap = Capture(host)
    print(f"capturing from {host} (idle commit {idle}s) — write on the tablet…",
          flush=True)
    sb = StrokeBuilder()
    strokes: list[list[tuple[int, int]]] = []
    page_n = 0
    try:
        while True:
            try:
                ev = cap.q.get(timeout=idle)
            except queue.Empty:
                if not sb.touching and len(strokes) >= min_strokes:
                    page_n += 1
                    path = Path(out if not watch else
                                Path(out).stem + f"_{page_n:03d}.png")
                    strokes_to_png(strokes, path)
                    print(f"committed {len(strokes)} strokes -> {path}", flush=True)
                    strokes = []
                    if not watch:
                        return
                continue
            if ev is None:
                print("stream ended")
                return
            if (s := sb.feed(ev)) is not None:
                strokes.append(s)
    finally:
        cap.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="rm2")
    ap.add_argument("--idle", type=float, default=2.8)
    ap.add_argument("-o", "--out", default="page.png")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--min-strokes", type=int, default=1)
    ap.add_argument("--touchtest", action="store_true",
                    help="decode finger touches live (both x orientations) — "
                         "touch the bottom-right corner and see which mapping "
                         "reads ~(1300, 1750)")
    a = ap.parse_args()
    if a.touchtest:
        cap = Capture(a.host, "/dev/input/event2")
        print("touch the tablet (Ctrl-C to stop)…", flush=True)
        tx = ty = 0
        try:
            while (ev := cap.q.get()) is not None:
                _, _, etype, code, value = ev
                if etype != EV_ABS:
                    continue
                if code == 53:            # ABS_MT_POSITION_X
                    tx = value
                elif code == 54:          # ABS_MT_POSITION_Y
                    ty = value
                elif code == 57 and value >= 0:   # ABS_MT_TRACKING_ID down
                    sx = int(tx * CANVAS_W / 767)
                    sy = CANVAS_H - int(ty * CANVAS_H / 1023)
                    print(f"down raw=({tx},{ty})  noflip=({sx},{sy})  "
                          f"xflip=({CANVAS_W - sx},{sy})  yflip-off=({sx},{CANVAS_H - sy})",
                          flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            cap.close()
        return
    run(a.host, a.idle, a.out, a.watch, a.min_strokes)


if __name__ == "__main__":
    main()
