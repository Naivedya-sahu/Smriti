"""
monke.py — Smriti closed loop (v0.1.4): the diary that talks back.

    uv run python host/monke.py

Session model (v0.1.5): the daemon boots IDLE. Eye marker (real ink,
bottom-right corner):
    dash           = idle — no capture processing, no AI
    open eye       = session on, Monke watching
    filled circle  = Monke thinking
    (erased)       = daemon not running

TAP the eye (short finger tap on the corner) → session START: Monke scans
the visible page (screenshot), inks a short greeting below the existing
ink, then watches. Write, pause ~3s → page image + conversation history go
to the vision model → reply inked below your writing (placed into a free
band found on the real screenshot; page-turn if nothing fits).

HOLD the eye (~1s) → session OFF: marker becomes a dash, nothing is
captured or sent until the next tap. No laptop needed for any of this.

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
from screen import screenshot, ink_floor, free_bands
from ink import load_styles, render
from oracle import chat, image_msg
from write import to_lamp

REPO_CFG = __import__("pathlib").Path(__file__).resolve().parents[1] / "config.toml"

MONKE_SYSTEM = """\
You are Monke, the spirit living inside Navy's paper diary (an e-ink tablet
called Smriti). You are shown a photo of the visible page. Strokes drawn in
RED are what Navy JUST wrote — reply to those. Everything in black is page
context: earlier conversation (including your own past replies), diagrams,
document content. Use the context, answer the red.

Voice: terse, primal-wise caveman. Short sentences. Warm but blunt. You know
Navy: electrical engineer, builds things, starts MS research at IIT Delhi
soon. You help with: journaling, study, projects, habits, ideas.

This is an ongoing conversation: earlier pages and your replies may precede
the latest photo. Stay consistent with what was already said.

Rules:
- Reply to the CONTENT of the handwriting, like a sharp friend in the margins.
- Max 35 words of prose. No greetings, no sign-off.
- Plain ASCII only (a-z, 0-9, . , ! ? ' -). No emoji, no markdown, no unicode
  — your reply is redrawn as handwriting by an ASCII-only stroke font.
- YOUR REPLY IS RENDERED ON PAPER. Prose becomes handwriting. Everything
  between $$...$$ is compiled by real LaTeX (amsmath) and drawn as a
  typeset preview. The reader must NEVER see LaTeX source: no \\frac, no
  \\omega, no ^ or _ notation in the prose itself — ALL maths, every
  formula, every symbol, goes inside $$...$$.
- Circuits and simple diagrams are encouraged:
  $$\\begin{circuitikz}...\\end{circuitikz}$$ (a plain tikzpicture is also
  fine for non-circuit diagrams). Inside circuitikz, labels MUST be in math
  mode: to[R, l=$R_1$], to[R, l=$4\\Omega$], v=$12\\,V$ — bare \\Omega
  outside $ $ fails to compile and the drawing is lost.
- One $$...$$ block per equation or drawing; prose stays outside.
- If the page is unreadable, say so in Monke voice, short."""

MARKER_X, MARKER_Y = 1352, 1826
CORNER_X, CORNER_Y = 1180, 1620      # touch-hold anywhere right/below this
HOLD_S = 0.9

# multitouch event codes (pt_mt, /dev/input/event2)
ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 53, 54, 57
TOUCH_X_MAX, TOUCH_Y_MAX = 767.0, 1023.0
EV_ABS = 3


LAMP_BIN = __import__("os").environ.get("SMRITI_LAMP", "/home/root/.vellum/bin/lamp")


def lamp(cmds: str, host: str) -> None:
    subprocess.run(["ssh", host, LAMP_BIN],
                   input=cmds.encode("ascii"), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def erase_box(strokes: list, host: str, pad: int = 12, sweep: int = 14) -> None:
    """Wipe the bounding box of the given strokes with a serpentine eraser
    pass. sweep ≈ eraser contact width; lower it if stripes survive."""
    xs = [x for s in strokes for x, _ in s]
    ys = [y for s in strokes for _, y in s]
    if not xs:
        return
    x0, x1 = max(0, min(xs) - pad), min(1403, max(xs) + pad)
    y0, y1 = max(0, min(ys) - pad), min(1871, max(ys) + pad)
    cmds, y, left = [f"erase down {x0} {y0}"], y0, True
    while y <= y1:
        cmds.append(f"erase move {x1 if left else x0} {y}")
        y += sweep
        if y <= y1:
            cmds.append(f"erase move {x1 if left else x0} {y}")
        left = not left
    cmds.append("erase up")
    lamp("\n".join(cmds) + "\n", host)


# Drawing the eye as real ink costs an ssh+lamp round trip (~1-2s) per state
# change plus an echo drain, and the eraser wipes real ink in the corner.
# Off by default: the corner GESTURE zone works without any drawn marker,
# the greeting ink confirms session-on, `smriti-eye status` reports state.
# Turn back on with [monke] marker_ink = true.
MARKER_INK = False


def marker(state: str, host: str) -> None:
    """'watch' open eye, 'busy' filled, 'pause' dash (idle), 'off' erased."""
    if not MARKER_INK:
        return
    wipe = "".join(f"erase circle {MARKER_X} {MARKER_Y} {r}\n" for r in (18, 13, 8, 4, 1))
    draw = ""
    if state == "watch":
        # open eye: outline + pupil
        draw = (f"pen circle {MARKER_X} {MARKER_Y} 12\n"
                f"pen circle {MARKER_X} {MARKER_Y} 3\n"
                f"pen circle {MARKER_X} {MARKER_Y} 1\n")
    elif state == "busy":
        draw = "".join(f"pen circle {MARKER_X} {MARKER_Y} {r}\n" for r in (12, 8, 5, 2))
    elif state == "pause":
        draw = f"pen line {MARKER_X - 12} {MARKER_Y} {MARKER_X + 12} {MARKER_Y}\n"
    lamp(wipe + draw, host)


class TouchGesture:
    """Corner-tap (short, stationary) => 'tap' (session start).
    Corner-hold (~1s, stationary) => 'hold' (session off).
    Fast wide horizontal swipe => 'pageturn' (xochitl changed page)."""

    def __init__(self):
        self.tx = self.ty = 0
        self.down_at: float | None = None
        self.down_sx = 0.0
        self.stayed = True

    def _sx(self) -> float:
        return self.tx * CANVAS_W / TOUCH_X_MAX

    def _in_corner(self) -> bool:
        sy = CANVAS_H - self.ty * CANVAS_H / TOUCH_Y_MAX
        sx = self._sx()
        # ponytail: real panel x-orientation unverified — lamp-injected taps
        # round-trip through lamp's own transform so they can't catch a
        # mirror error; a real finger might read mirrored. Accept both x
        # orientations until `capture.py --touchtest` with a real finger
        # settles it, then delete the mirrored branch.
        return sy >= CORNER_Y and (sx >= CORNER_X or CANVAS_W - sx >= CORNER_X)

    def feed(self, ev) -> str | None:
        _, _, etype, code, value = ev
        if etype != EV_ABS:
            return None
        if code == ABS_MT_POSITION_X:
            self.tx = value
        elif code == ABS_MT_POSITION_Y:
            self.ty = value
        elif code == ABS_MT_TRACKING_ID:
            if value >= 0:
                self.down_at, self.stayed = time.time(), True
                self.down_sx = self._sx()
                return None
            if self.down_at is None:
                return None
            held, self.down_at = time.time() - self.down_at, None
            if self.stayed and self._in_corner():
                return "hold" if held >= HOLD_S else "tap"
            if held < 0.6 and abs(self._sx() - self.down_sx) > 400:
                return "pageturn"
            return None
        if self.down_at is not None and not self._in_corner():
            self.stayed = False
        return None


def _screen_floor(cfg=None) -> int | None:
    """Occupied-ink floor from the actual visible screen, or None if the
    goMarkableStream service is unreachable."""
    if cfg is None:
        with open(REPO_CFG, "rb") as f:
            cfg = tomllib.load(f)
    s = cfg.get("screen", {})
    img = screenshot(__import__("os").environ.get("SMRITI_SCREEN_URL") or s.get("url", "https://10.11.99.1:2001"),
                     s.get("user", "admin"), s.get("password", "password"))
    return ink_floor(img) if img is not None else None


_FLOOR_FILE = __import__("pathlib").Path.home() / ".config" / "smriti" / "floor"


def _load_floor() -> int:
    try:
        return int(_FLOOR_FILE.read_text())
    except (OSError, ValueError):
        return 0


def _save_floor(v: int) -> None:
    try:
        _FLOOR_FILE.parent.mkdir(parents=True, exist_ok=True)
        _FLOOR_FILE.write_text(str(v))
    except OSError:
        pass


def _pull(q: queue.Queue) -> list:
    evs = []
    try:
        while True:
            evs.append(q.get_nowait())
    except queue.Empty:
        return evs


def _drain(*qs: queue.Queue, quiet: float = 1.2, cap: float = 15.0) -> None:
    """Discard our own ink echo from the event queues. The echo of a long
    reply keeps trickling over the ssh stream well past any fixed sleep —
    that leftover used to be committed as 'user input' and Monke answered
    itself. So: drain until the streams stay quiet for `quiet` seconds
    (bounded by `cap`)."""
    t0 = last = time.time()
    while time.time() - last < quiet and time.time() - t0 < cap:
        time.sleep(0.2)
        if any(len(_pull(q)) for q in qs):
            last = time.time()


def _control_server(ctl: queue.Queue, state: dict, port: int = 7333) -> None:
    """Tiny HTTP control plane: GET /start, /stop, /status. Lets the tablet
    (smriti-eye CLI) or any curl drive sessions without touch gestures."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            p = self.path.strip("/")
            if p in ("start", "stop"):
                ctl.put(p)
                body = f"{p} queued\n"
            elif p == "status":
                body = ("watching" if state.get("watching") else "idle") + "\n"
            else:
                self.send_error(404)
                return
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    try:
        srv = http.server.ThreadingHTTPServer(("", port), H)
    except OSError as e:
        print(f"[control] port {port} unavailable ({e}) — HTTP control off",
              flush=True)
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[control] http://<this-host>:{port}/start|stop|status", flush=True)


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
    fade = m.get("fade", False)
    fade_hold = m.get("fade_hold", 10)
    greet_fade = m.get("greet_fade", True)   # erase greeting once answered
    global MARKER_INK
    MARKER_INK = m.get("marker_ink", False)

    # pen stream (event1) is opened only while a session is ON: a hovering
    # pen floods evdev even when idle, and that all rides the ssh link.
    pen: Capture | None = None
    touch = Capture(host, "/dev/input/event2")
    sb = StrokeBuilder()
    tg = TouchGesture()
    ctl: queue.Queue[str] = queue.Queue()
    state = {"watching": False}
    _control_server(ctl, state, cfg.get("control", {}).get("port", 7333))
    history: list[dict] = []
    strokes: list[list[tuple[int, int]]] = []
    greet_ink: list = []      # greeting strokes, erased at first reply/stop
    # ink floor = lowest y known to hold ink. Primary source: a REAL
    # screenshot (goMarkableStream /screenshot) of the visible page,
    # taken when a session starts; fallback: floor persisted last run.
    floor = _load_floor()
    paused = True                     # boot idle: no capture, no AI
    last_pen = 0.0
    marker("pause", host)
    _drain(touch.q)
    print("monke is idle — TAP the corner eye to start a session, "
          "HOLD ~1s to stop one; or smriti-eye start/stop from the tablet "
          "(Ctrl-C quits)", flush=True)

    try:
        while True:
            if pen is not None:
                for ev in _pull(pen.q):
                    if ev is None:
                        print("pen stream ended (device offline?)", flush=True)
                        return
                    last_pen = time.time()
                    if not paused and (s := sb.feed(ev)) is not None:
                        strokes.append(s)
            cmds = _pull(ctl)         # smriti-eye / curl commands
            for ev in _pull(touch.q):
                if ev is None:
                    print("touch stream ended (device offline?)", flush=True)
                    return
                if (g := tg.feed(ev)) is not None:
                    cmds.append(g)
            for g in cmds:
                if g in ("tap", "start") and paused:
                    # session start: scan the page, greet, watch
                    sf = _screen_floor(cfg)
                    floor = sf if sf is not None else _load_floor()
                    print(f"session start — ink floor {floor}"
                          + (" (screenshot)" if sf is not None else " (persisted)"),
                          flush=True)
                    floor, greet_ink = _greet(style, step, bottom, host,
                                              history, floor)
                    _save_floor(floor)
                    pen, sb = Capture(host), StrokeBuilder()
                    paused, strokes = False, []
                    state["watching"] = True
                    marker("watch", host)
                    _drain(pen.q, touch.q)
                    print("watching", flush=True)
                elif g in ("hold", "stop") and not paused:
                    if greet_fade and greet_ink:
                        erase_box(greet_ink, host)
                        greet_ink = []
                    paused, strokes = True, []
                    state["watching"] = False
                    if pen is not None:
                        pen.close()
                        pen = None
                    print("session off", flush=True)
                    marker("pause", host)
                    _drain(touch.q)
                elif g == "pageturn" and not paused:
                    time.sleep(1.0)          # let xochitl repaint
                    sf = _screen_floor(cfg)
                    floor = sf if sf is not None else 0
                    _save_floor(floor)
                    print(f"page turned — floor {floor}", flush=True)
            if (not paused and pen is not None and strokes and not sb.touching
                    and time.time() - last_pen >= idle):
                floor = _reply(strokes, style, step, bottom, host, history, floor,
                               fade, fade_hold,
                               greet=greet_ink if greet_fade else None)
                greet_ink = []
                _save_floor(floor)
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
        if pen is not None:
            pen.close()
        touch.close()
        print("monke sleeps.", flush=True)


def _page_context(strokes) -> tuple[bytes, "object"]:
    """Vision input: real page screenshot with the fresh strokes overlaid in
    red — model sees full context (old replies, diagrams, documents) AND
    exactly what is new. Falls back to plain stroke render if the
    goMarkableStream service is unreachable. Returns (png_bytes, shot);
    shot is the raw grayscale screenshot (or None) so callers can also run
    workarea analysis (free_bands) on it without a second grab."""
    from PIL import ImageDraw
    shot = None
    try:
        with open(REPO_CFG, "rb") as f:
            s = tomllib.load(f).get("screen", {})
        shot = screenshot(__import__("os").environ.get("SMRITI_SCREEN_URL") or s.get("url", "https://10.11.99.1:2001"),
                          s.get("user", "admin"), s.get("password", "password"))
    except Exception:
        pass
    if shot is None:
        img = render_strokes(strokes, crop=True) if strokes \
            else render_strokes([], crop=False)
    else:
        img = shot.convert("RGB")
        d = ImageDraw.Draw(img)
        for s_ in strokes:
            if len(s_) > 1:
                d.line(s_, fill=(220, 0, 0), width=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), shot


def _place(text: str, style, y: int, shot, bottom: int) -> list | None:
    """Lay the reply out at y; if it overflows the page, retry inside the
    first free band (workarea parser on the real screenshot) tall enough to
    hold it. None = no room anywhere on this page."""
    reply = _layout(text, style, y)
    if not reply:
        return reply
    h = max(py for s in reply for _, py in s) - y
    if y + h <= bottom:
        return reply
    if shot is not None:
        for y0, y1 in free_bands(shot):
            if y0 >= 100 and min(y1, bottom) - y0 >= h + 30:
                return _layout(text, style, y0 + 10)
    return None


def _greet(style, step, bottom, host, history, floor) -> tuple[int, list]:
    """Session-start greeting: one chat call with the scanned page as
    context, short Monke line inked below the existing ink. Returns
    (new floor, greeting strokes) — the strokes get erased later when
    greet_fade is on."""
    marker("busy", host)
    png, shot = _page_context([])
    user = image_msg(png, "Navy just tapped the eye: a diary session starts "
                          "now. Greet him in ONE short Monke line (under 12 "
                          "words). Reference what is on the page if useful.")
    try:
        text = chat([{"role": "system", "content": MONKE_SYSTEM}] + history + [user])
    except Exception as e:
        print(f"[greet] AI unreachable: {str(e)[:120]}", flush=True)
        return floor, []
    text = text.encode("ascii", "ignore").decode().strip()
    history += [user, {"role": "assistant", "content": text}]
    print(f"monke greets: {text}", flush=True)
    y = floor + 50 if floor else 150
    reply = _place(text, style, min(y, bottom - 100), shot, bottom)
    if not reply:
        print("no room for greeting — page full, greeting in log only", flush=True)
        return floor, []
    lamp(to_lamp(reply, style.get("pressure", 2400), step), host)
    return max(floor, max(py for s in reply for _, py in s)), reply


def _reply(strokes, style, step, bottom, host, history, floor,
           fade=False, fade_hold=10, greet=None) -> int:
    """Returns the new ink floor (max y written this session)."""
    marker("busy", host)
    print(f"page committed ({len(strokes)} strokes) -> asking monke…", flush=True)
    t0 = time.time()
    png, shot = _page_context(strokes)
    user = image_msg(png, "The red strokes are Navy's newest writing. Reply. "
                          "Maths/circuits only inside $$...$$ — they are "
                          "typeset onto the paper; never show LaTeX source "
                          "in prose.")
    text = chat([{"role": "system", "content": MONKE_SYSTEM}] + history + [user])
    text = text.encode("ascii", "ignore").decode().strip()
    history += [user, {"role": "assistant", "content": text}]
    print(f"monke ({time.time() - t0:.1f}s): {text}", flush=True)
    if greet:
        # the greeting served its purpose — fade it before the answer lands
        erase_box(greet, host)

    # ponytail: fade/riddle mode DISABLED pending visual verification on
    # device — erase sweep width/completeness unconfirmed. Re-enable by
    # uncommenting; config [monke] fade/fade_hold are already plumbed.
    # if fade:
    #     # riddle mode: your words dissolve, the answer appears in their
    #     # place, lingers, then dissolves too — page returns clean
    #     erase_box(strokes, host)
    #     y = max(80, min(py for s in strokes for _, py in s))
    #     reply = render(text, style, x=100, y=y)
    #     lamp(to_lamp(reply, style.get("pressure", 2400), step), host)
    #     time.sleep(fade_hold)
    #     erase_box(reply, host)
    #     return floor

    # keep mode: place below everything inked this session, not just this
    # commit — else writing above an old reply would overwrite it. If that
    # overflows, _place retries inside a free band from the screenshot.
    y = max(floor, max(py for s in strokes for _, py in s)) + 50
    reply = _place(text, style, y, shot, bottom)
    if reply is None:
        # won't fit — turn pages (injected finger swipe) until one has room;
        # each landing checked with a REAL screenshot, never assumed blank
        for _ in range(3):
            lamp("swipe left\n", host)
            time.sleep(1.2)
            sf = _screen_floor() or 0
            if sf + 100 < bottom:
                break
        else:
            # every reachable page is full (injected swipes can't CREATE
            # pages — only a real finger swipe on the last page can).
            # Don't ink into the void; the reply is in the log.
            print("NO ROOM on any page — reply not inked. Turn to a fresh "
                  "page by hand.", flush=True)
            return floor
        floor = sf
        reply = _layout(text, style, max(150, sf + 50))
    lamp(to_lamp(reply, style.get("pressure", 2400), step), host)
    return max(floor, max((py for s in reply for _, py in s), default=floor))


def _layout(text: str, style, y: int) -> list:
    """Reply → strokes. Prose via the stroke font; $$...$$ blocks typeset
    through LaTeX (maths/circuitikz) and stacked between the prose."""
    import re
    segs: list[tuple[bool, str]] = []
    for i, part in enumerate(re.split(r"\$\$(.+?)\$\$", text, flags=re.S)):
        if i % 2:
            segs.append((True, part))
        else:
            # model slip-up guard: single-$ inline math in prose would be
            # inked as raw code by the stroke font — typeset it instead.
            # (only prose is scanned, so inner $ in circuitikz labels is safe)
            for j, sub in enumerate(
                    re.split(r"(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)", part)):
                segs.append((j % 2 == 1, sub))
    out = []
    for is_tex, part in segs:
        part = part.strip()
        if not part:
            continue
        if is_tex:  # tex block
            try:
                from tex import tex_to_strokes
                body = part if "\\begin" in part else f"${part}$"
                seg = tex_to_strokes(body, x=100, y=y + 20, max_w=1200)
            except Exception as e:
                print(f"[tex] render failed: {str(e)[:200]}", flush=True)
                # raw circuit code inked on paper = noise; short note instead
                fb = part if len(part) < 80 and "\\begin" not in part \
                    else "(monke drew a bad diagram - see log)"
                seg = render(fb, style, x=100, y=y)
        else:
            seg = render(part, style, x=100, y=y)
        if seg:
            out += seg
            y = max(py for s in seg for _, py in s) + 40
    return out


if __name__ == "__main__":
    run()
