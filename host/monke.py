"""
monke.py — Smriti closed loop (v0.1.4): the diary that talks back.

    uv run python host/monke.py

Watches the RM2. Status marker (real ink, bottom-right corner):
    hollow circle  = Monke watching
    filled circle  = Monke thinking
    dash           = paused
    (erased)       = daemon not running

Write on the page, pause ~3s → page image + conversation history go to the
vision model with the Monke persona → reply is inked below your writing.
If the reply won't fit, a finger-swipe is injected to turn the page.

TOGGLE FROM THE TABLET: press and hold a finger ~1s on the marker (bottom-
right corner) → pause/resume. No laptop needed.

Don't write while Monke is inking a reply — those strokes are discarded
with the reply's own echo. Wait for the hollow circle.

Ctrl-C stops the daemon and erases the marker.
"""

from __future__ import annotations

import io
import queue
import subprocess
import time
import tomllib

from capture import Capture, StrokeBuilder, render_strokes, CANVAS_W, CANVAS_H
from ink import load_styles, render
from oracle import chat, image_msg
from write import to_lamp

REPO_CFG = __import__("pathlib").Path(__file__).resolve().parents[1] / "config.toml"

MONKE_SYSTEM = """\
You are Monke, the spirit living inside Navy's paper diary (an e-ink tablet
called Smriti). You are shown a photo of what Navy just handwrote.

Voice: terse, primal-wise caveman. Short sentences. Warm but blunt. You know
Navy: electrical engineer, builds things, starts MS research at IIT Delhi
soon. You help with: journaling, study, projects, habits, ideas.

This is an ongoing conversation: earlier pages and your replies may precede
the latest photo. Stay consistent with what was already said.

Rules:
- Reply to the CONTENT of the handwriting, like a sharp friend in the margins.
- Max 35 words. No greetings, no sign-off.
- Plain ASCII only (a-z, 0-9, . , ! ? ' -). No emoji, no markdown, no unicode
  — your reply is redrawn as handwriting by an ASCII-only stroke font.
- If the page is unreadable, say so in Monke voice, short."""

MARKER_X, MARKER_Y = 1352, 1826
CORNER_X, CORNER_Y = 1180, 1620      # touch-hold anywhere right/below this
HOLD_S = 0.9

# multitouch event codes (pt_mt, /dev/input/event2)
ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 53, 54, 57
TOUCH_X_MAX, TOUCH_Y_MAX = 767.0, 1023.0
EV_ABS = 3


def lamp(cmds: str, host: str) -> None:
    subprocess.run(["ssh", host, "/home/root/.vellum/bin/lamp"],
                   input=cmds.encode("ascii"), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def marker(state: str, host: str) -> None:
    """'watch' hollow, 'busy' filled, 'pause' dash, 'off' erased."""
    wipe = "".join(f"erase circle {MARKER_X} {MARKER_Y} {r}\n" for r in (18, 13, 8, 4, 1))
    draw = ""
    if state == "watch":
        draw = f"pen circle {MARKER_X} {MARKER_Y} 12\n"
    elif state == "busy":
        draw = "".join(f"pen circle {MARKER_X} {MARKER_Y} {r}\n" for r in (12, 8, 5, 2))
    elif state == "pause":
        draw = f"pen line {MARKER_X - 12} {MARKER_Y} {MARKER_X + 12} {MARKER_Y}\n"
    lamp(wipe + draw, host)


class TouchGesture:
    """Detects a ~1s stationary finger hold in the marker corner."""

    def __init__(self):
        self.tx = self.ty = 0
        self.down_at: float | None = None
        self.stayed = True

    def _in_corner(self) -> bool:
        sx = self.tx * CANVAS_W / TOUCH_X_MAX
        sy = CANVAS_H - self.ty * CANVAS_H / TOUCH_Y_MAX
        return sx >= CORNER_X and sy >= CORNER_Y

    def feed(self, ev) -> bool:
        """True when a corner-hold completed (finger lifted)."""
        _, _, etype, code, value = ev
        if etype != EV_ABS:
            return False
        if code == ABS_MT_POSITION_X:
            self.tx = value
        elif code == ABS_MT_POSITION_Y:
            self.ty = value
        elif code == ABS_MT_TRACKING_ID:
            if value >= 0:
                self.down_at, self.stayed = time.time(), True
                return False
            if self.down_at is None:
                return False
            held, self.down_at = time.time() - self.down_at, None
            return held >= HOLD_S and self.stayed and self._in_corner()
        if self.down_at is not None and not self._in_corner():
            self.stayed = False
        return False


def _pull(q: queue.Queue) -> list:
    evs = []
    try:
        while True:
            evs.append(q.get_nowait())
    except queue.Empty:
        return evs


def _drain(*qs: queue.Queue) -> None:
    time.sleep(0.6)
    for q in qs:
        _pull(q)


def run() -> None:
    with open(REPO_CFG, "rb") as f:
        cfg = tomllib.load(f)
    host = cfg["capture"]["host"]
    idle = cfg["capture"].get("idle_seconds", 2.8)
    m = cfg.get("monke", {})
    style = load_styles()[m.get("reply_style", "cursive")]
    step = m.get("waypoint_step", 3)
    bottom = m.get("page_bottom", 1780)
    max_turns = m.get("history_turns", 6)

    pen = Capture(host)
    touch = Capture(host, "/dev/input/event2")
    sb = StrokeBuilder()
    tg = TouchGesture()
    history: list[dict] = []
    strokes: list[list[tuple[int, int]]] = []
    floor = 0            # lowest ink y seen this session (inputs + replies)
    paused = False
    last_pen = 0.0
    marker("watch", host)
    _drain(pen.q, touch.q)
    print("monke is watching — write on the tablet; hold a finger ~1s on the "
          "corner marker to pause/resume (Ctrl-C to stop)", flush=True)

    try:
        while True:
            for ev in _pull(pen.q):
                if ev is None:
                    print("pen stream ended (device offline?)", flush=True)
                    return
                last_pen = time.time()
                if not paused and (s := sb.feed(ev)) is not None:
                    strokes.append(s)
            for ev in _pull(touch.q):
                if ev is not None and tg.feed(ev):
                    paused = not paused
                    strokes = []
                    print("paused" if paused else "watching", flush=True)
                    marker("pause" if paused else "watch", host)
                    _drain(pen.q, touch.q)
            if (not paused and strokes and not sb.touching
                    and time.time() - last_pen >= idle):
                floor = _reply(strokes, style, step, bottom, host, history, floor)
                del history[:-2 * max_turns]
                strokes = []
                marker("watch", host)
                _drain(pen.q, touch.q)   # our own ink echoes on evdev — discard
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            marker("off", host)
        except Exception:
            pass
        pen.close()
        touch.close()
        print("monke sleeps.", flush=True)


def _reply(strokes, style, step, bottom, host, history, floor) -> int:
    """Returns the new ink floor (max y written this session)."""
    marker("busy", host)
    buf = io.BytesIO()
    render_strokes(strokes, crop=True).save(buf, format="PNG")
    print(f"page committed ({len(strokes)} strokes) -> asking monke…", flush=True)
    t0 = time.time()
    user = image_msg(buf.getvalue(), "Navy just wrote this. Reply.")
    text = chat([{"role": "system", "content": MONKE_SYSTEM}] + history + [user])
    text = text.encode("ascii", "ignore").decode().strip()
    history += [user, {"role": "assistant", "content": text}]
    print(f"monke ({time.time() - t0:.1f}s): {text}", flush=True)

    # place below everything inked this session, not just this commit —
    # else writing above an old reply would overwrite it
    y = max(floor, max(py for s in strokes for _, py in s)) + 50
    reply = render(text, style, x=100, y=y)
    if reply and max(py for s in reply for _, py in s) > bottom:
        # won't fit — turn the page (injected finger swipe), restart at top
        lamp("swipe left\n", host)
        time.sleep(1.0)
        reply = render(text, style, x=100, y=150)
        floor = 0
    lamp(to_lamp(reply, style.get("pressure", 2400), step), host)
    return max(floor, max((py for s in reply for _, py in s), default=floor))


if __name__ == "__main__":
    run()
