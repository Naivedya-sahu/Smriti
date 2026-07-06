"""
monke.py — Smriti closed loop (v0.1.4): the diary that talks back.

    uv run python host/monke.py

Watches the RM2. Status marker (real ink, bottom-right): hollow circle =
Monke watching, filled = Monke thinking; erased on state change. Write on
the page, pause ~3s → page image goes to the vision model with the Monke
persona → reply is inked below your writing. If the reply won't fit, a
finger-swipe is injected to turn the page and the reply starts up top.

Ctrl-C stops the daemon and erases the marker.
"""

from __future__ import annotations

import io
import queue
import subprocess
import time
import tomllib

from capture import Capture, StrokeBuilder, render_strokes
from ink import load_styles, render
from oracle import see
from write import to_lamp

REPO_CFG = __import__("pathlib").Path(__file__).resolve().parents[1] / "config.toml"

MONKE_SYSTEM = """\
You are Monke, the spirit living inside Navy's paper diary (an e-ink tablet
called Smriti). You are shown a photo of what Navy just handwrote.

Voice: terse, primal-wise caveman. Short sentences. Warm but blunt. You know
Navy: electrical engineer, builds things, starts MS research at IIT Delhi
soon. You help with: journaling, study, projects, habits, ideas.

Rules:
- Reply to the CONTENT of the handwriting, like a sharp friend in the margins.
- Max 35 words. No greetings, no sign-off.
- Plain ASCII only (a-z, 0-9, . , ! ? ' -). No emoji, no markdown, no unicode
  — your reply is redrawn as handwriting by an ASCII-only stroke font.
- If the page is unreadable, say so in Monke voice, short."""

MARKER_X, MARKER_Y = 1352, 1826


def lamp(cmds: str, host: str) -> None:
    subprocess.run(["ssh", host, "/home/root/.vellum/bin/lamp"],
                   input=cmds.encode("ascii"), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def marker(state: str, host: str) -> None:
    """'watch' hollow circle, 'busy' filled, 'off' erased."""
    wipe = "".join(f"erase circle {MARKER_X} {MARKER_Y} {r}\n" for r in (18, 13, 8, 4, 1))
    draw = ""
    if state == "watch":
        draw = f"pen circle {MARKER_X} {MARKER_Y} 12\n"
    elif state == "busy":
        draw = "".join(f"pen circle {MARKER_X} {MARKER_Y} {r}\n" for r in (12, 8, 5, 2))
    lamp(wipe + draw, host)


def run() -> None:
    with open(REPO_CFG, "rb") as f:
        cfg = tomllib.load(f)
    host = cfg["capture"]["host"]
    idle = cfg["capture"].get("idle_seconds", 2.8)
    m = cfg.get("monke", {})
    style = load_styles()[m.get("reply_style", "cursive")]
    step = m.get("waypoint_step", 3)
    bottom = m.get("page_bottom", 1780)

    cap = Capture(host)
    sb = StrokeBuilder()
    strokes: list[list[tuple[int, int]]] = []
    marker("watch", host)
    _drain(cap.q)
    print("monke is watching — write on the tablet (Ctrl-C to stop)", flush=True)

    try:
        while True:
            try:
                ev = cap.q.get(timeout=idle)
            except queue.Empty:
                if sb.touching or not strokes:
                    continue
                _reply(strokes, style, step, bottom, host)
                strokes = []
                marker("watch", host)
                _drain(cap.q)  # our own injected ink echoes on evdev — discard
                continue
            if ev is None:
                print("event stream ended (device offline?)", flush=True)
                return
            if (s := sb.feed(ev)) is not None:
                strokes.append(s)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            marker("off", host)
        except Exception:
            pass
        cap.close()
        print("monke sleeps.", flush=True)


def _reply(strokes, style, step, bottom, host) -> None:
    marker("busy", host)
    buf = io.BytesIO()
    render_strokes(strokes, crop=True).save(buf, format="PNG")
    print(f"page committed ({len(strokes)} strokes) -> asking monke…", flush=True)
    t0 = time.time()
    text = see(buf.getvalue(), "Navy just wrote this. Reply.", system=MONKE_SYSTEM)
    text = text.encode("ascii", "ignore").decode().strip()
    print(f"monke ({time.time() - t0:.1f}s): {text}", flush=True)

    y = max(py for s in strokes for _, py in s) + 50
    reply = render(text, style, x=100, y=y)
    if reply and max(py for s in reply for _, py in s) > bottom:
        # won't fit — turn the page (injected finger swipe), restart at top
        lamp("swipe left\n", host)
        time.sleep(1.0)
        reply = render(text, style, x=100, y=150)
    lamp(to_lamp(reply, style.get("pressure", 2400), step), host)


def _drain(q: queue.Queue) -> None:
    time.sleep(0.6)
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass


if __name__ == "__main__":
    run()
