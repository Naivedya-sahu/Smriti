"""Virtual tablet: the REAL daemon loop (monke.run) offline against a fake RM2.

Fake = page canvas, pen/touch event queues, lamp, the QML bits the daemon
uses, a scripted Navy and canned model replies, all on a virtual clock (a
six-turn session runs in seconds). Each scenario ends with the checks Navy
would make by eye: every prompt answered once, nothing inked over ink, #P
erased, nothing lost without a word.

    PYTHONUTF8=1 uv run python tests/sim.py            # all scenarios
    PYTHONUTF8=1 uv run python tests/sim.py scroll     # names containing 'scroll'

Pages + logs land in sessions/_runs/sim/<scenario>/ (gitignored).
ponytail: drawings are synthetic boxes (no pdflatex needed) and the plot
path (graph.py) is not played — add when a bug points there."""
# pyright: reportMissingImports=false

import contextlib
import json
import queue
import re
import sys
import time as real_time
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import brain  # noqa: E402
import capture  # noqa: E402
import harness  # noqa: E402
import jitter  # noqa: E402
import layers  # noqa: E402
import monke  # noqa: E402
import tablet  # noqa: E402
import vectors  # noqa: E402
from ink import Tinted, load_styles, render  # noqa: E402

W, H, OX = 1404, 1872, 702          # rm page x = screen x - OX; page y = screen y + scroll
OUT = ROOT / "sessions" / "_runs" / "sim"
RGB = {0: (0, 0, 0), 1: (120, 120, 120), 6: (0, 0, 255), 7: (255, 0, 0), "navy": (200, 20, 20)}
FLUSH = 10.0                        # .rm page file lags the screen ~10 s (measured)
STROKE_S = 0.25                     # Navy: ~4 strokes a second


class Stop(KeyboardInterrupt):
    pass


class Clock:
    """Stands in for monke's `time`: sleep() advances virtual time and lets
    Navy act (strokes released, scripted steps run) on the way."""

    def __init__(self, sim):
        self.now, self.sim = 1.7e9, sim

    def time(self):
        return self.now

    def sleep(self, d):
        end = self.now + max(d, 0.0)
        self.sim.due(end)
        self.now = max(self.now, end)

    def __getattr__(self, k):
        return getattr(real_time, k)


class Cap:
    """capture.Capture without ssh."""

    def __init__(self, host="rm2", device="/dev/input/event1"):
        self.q = queue.Queue()
        Sim.cur.queues[device[-6:]] = self.q

    def close(self):
        pass


class Sim:
    cur: "Sim" = None

    def __init__(self, name):
        Sim.cur = self
        self.name, self.clock = name, Clock(self)
        self.t0 = self.clock.now
        self.queues, self.ctl = {}, None
        self.ink = []                # {who, layer, color, w, pts (page), t, dead, batch, text}
        self.top = 0                 # scroll: page y at the top of the screen
        self.navy = {"color": "navy", "thick": 2, "layer": "Layer 1"}
        self.pen = dict(self.navy)   # tool state xochitl holds right now
        self.sel = []
        self.steps, self.i, self.armed, self.last_act = [], 0, None, self.clock.now
        self.pending = []            # Navy strokes not yet on the page: (t, stroke dict)
        self.unread, self.writes, self.replies = [], [], []
        self.readings = []           # canned reader fields for the next reads (SPP target, intent)
        self.prompts, self.laid, self.checks = [], [], []   # (role, messages); texts laid out; extra judges
        self.log, self.buf, self.batch, self.in_lamp = [], "", 0, False
        self.hover_until, self.mixed, self.gate, self.last_navy, self.notes = 0.0, 0, 0, 0.0, []
        self.eye = []                # eye_error(on) calls: the only failure sign Navy gets
        self.eye_reasons = []        # the short reason shown with each '!'
        self.fail_writes = 0         # brain failures to raise on the next writes
        self.turn = "split"
        self.allow = ()              # log patterns a scenario expects (judge skips them)
        self.found = []              # problems seen while running
        self.style = dict(load_styles()["print"], size=64)
        self._img = None

    # ── the script ────────────────────────────────────────────────────────
    def at(self, trigger, delay, fn):
        """After a log line containing `trigger` (None = after the previous
        step), wait `delay` s, then Navy does fn()."""
        self.steps.append((trigger, delay, fn))
        if len(self.steps) == 1 and trigger is None:
            self.armed = self.clock.now

    def tap(self):
        self.ctl.put("start")

    def hold(self):
        self.ctl.put("stop")

    def write(self, text, y=None, x=120, expect=True):
        """Navy writes under the lowest ink on screen (or at screen y)."""
        if y is None:
            y = self.floor() + 70
        strokes = render(text, self.style, x=x, y=y)
        low = max(py for s in strokes for _, py in s)
        if low > H - 30:
            self.found.append(f"SCRIPT: {text[:30]!r} runs off the screen (y {low}) — scroll first")
        rec = {"text": text, "expect": expect, "read": False, "ink": [], "t": self.clock.now,
               "b0": self.batch}
        self.writes.append(rec)
        t = max([self.clock.now] + [p[0] for p in self.pending])
        for k, s in enumerate(strokes):
            self.pending.append((t + (k + 1) * STROKE_S, s, rec))
        return rec

    def sketch(self, strokes):
        """Ink Navy drew by hand before this session (already in the page file)."""
        old = [self._put("navy", s, layer=self.navy["layer"], color="navy", w=4) for s in strokes]
        for d in old:
            d["t"] -= 100
        return old

    def hover(self, seconds):
        self.hover_until = self.clock.now + seconds

    def scroll(self, dy):
        """One-finger vertical drag: content moves up by dy (page scrolls down)."""
        self.top, self._img = max(0, self.top + dy), None
        y0, y1 = (1500, max(100, 1500 - dy)) if dy > 0 else (300, min(1800, 300 - dy))
        q = self.queues["event2"]
        for code, v in ((monke.ABS_MT_TRACKING_ID, 7), (monke.ABS_MT_POSITION_X, 380),
                        (monke.ABS_MT_POSITION_Y, int(y0 * 1023 / H)),
                        (monke.ABS_MT_POSITION_Y, int(y1 * 1023 / H)),
                        (monke.ABS_MT_TRACKING_ID, -1)):
            q.put((0, 0, monke.EV_ABS, code, v))

    def floor(self):
        from screen import ink_floor
        return ink_floor(self.screen())

    # ── virtual time ──────────────────────────────────────────────────────
    def due(self, end):
        c = self.clock
        while True:
            ts = self.pending[0][0] if self.pending else None
            tp = (self.armed + self.steps[self.i][1]
                  if self.i < len(self.steps) and self.armed is not None else None)
            nxt = min((t for t in (ts, tp) if t is not None), default=None)
            if nxt is None or nxt > end:
                break
            c.now = max(c.now, nxt)
            if ts is not None and ts == nxt:
                _, s, rec = self.pending.pop(0)
                self._navy_stroke(s, rec)
            else:
                fn = self.steps[self.i][2]
                self.i += 1
                self.armed = c.now if (self.i < len(self.steps)
                                       and self.steps[self.i][0] is None) else None
                fn()
            self.last_act = c.now
        if c.now < self.hover_until:               # a hovering pen floods event1
            capture.Capture.pen_last = min(end, self.hover_until)
            if "event1" in self.queues:
                self.queues["event1"].put((0, 0, capture.EV_ABS, capture.ABS_X, 5000))
        idle = end - max(self.last_act, self.log[-1][0] if self.log else 0)
        if self.i >= len(self.steps) and not self.pending and idle > 40:
            raise Stop
        if end - self.t0 > 1500:
            want = self.steps[self.i][0] if self.i < len(self.steps) else "?"
            self.found.append(f"STUCK: never saw {want!r} (step {self.i + 1}/{len(self.steps)})")
            raise Stop

    def write_log(self, s):                        # sys.stdout while the daemon runs
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.log.append((self.clock.now, line))
            if (self.i < len(self.steps) and self.armed is None
                    and self.steps[self.i][0] and self.steps[self.i][0] in line):
                self.armed = self.clock.now
        return len(s)

    # ── the tablet ────────────────────────────────────────────────────────
    def _events(self, s):
        """Screen stroke -> evdev events on event1 (inverse of capture.to_screen)."""
        q = self.queues.get("event1")
        capture.Capture.pen_last = self.clock.now
        if q is None:                              # no session: nobody listens
            return
        E = lambda t, c, v: q.put((0, 0, t, c, v))

        def pos(x, y):
            E(capture.EV_ABS, capture.ABS_X, round((H - y) * capture.WACOM_Y_MAX / H))
            E(capture.EV_ABS, capture.ABS_Y, round(x * capture.WACOM_X_MAX / W))
        pos(*s[0])
        E(capture.EV_ABS, capture.ABS_PRESSURE, 2000)
        E(capture.EV_KEY, capture.BTN_TOUCH, 1)
        for x, y in s:
            pos(x, y)
            E(capture.EV_SYN, 0, 0)
        E(capture.EV_KEY, capture.BTN_TOUCH, 0)

    def _put(self, who, s, **kw):
        d = {"who": who, "pts": [(x, y + self.top) for x, y in s], "t": self.clock.now,
             "dead": False, "batch": self.batch, **kw}
        self.ink.append(d)
        self._img = None
        return d

    def _navy_stroke(self, s, rec):
        self.mixed += self.in_lamp
        self.last_navy = self.clock.now
        d = self._put("navy", s, layer=self.pen["layer"], color="navy", w=4)
        rec["ink"].append(d)
        if rec not in self.unread:
            self.unread.append(rec)
        self._events(s)

    def lamp(self, cmds):
        self.batch += 1
        if self.clock.now - self.last_navy < 0.45 and "pen still active" not in self.log[-1][1]:
            self.gate += 1                         # a batch started on top of Navy's pen
        self.clock.sleep(0.25)                     # per call: persistent-shell round trip
        self.in_lamp = True
        cur, n, moves = None, 0, 0
        for ln in cmds.splitlines():
            p = ln.split()
            if p[:2] == ["pen", "down"]:
                cur = [(int(p[2]), int(p[3]))]
            elif p[:2] == ["pen", "move"] and cur is not None:
                cur.append((int(p[2]), int(p[3])))
                moves += 1
            elif p[:2] == ["pen", "up"] and cur is not None:
                if self.pen["layer"] not in (layers.MONKE, layers.GREET):
                    self.found.append(f"Monke inked on Navy's layer {self.pen['layer']!r}")
                self._put("monke", cur, layer=self.pen["layer"], color=self.pen["color"],
                          w=2 if self.pen["thick"] == 1 else 4)
                self._events(cur)
                cur, n = None, n + 1
                self.clock.sleep(0.021 + 0.0016 * moves)   # measured: 21 ms/stroke, 1.6 ms/move
                moves = 0
        self.in_lamp = False

    def run(self, host, script, stdin=b"", timeout=30):   # tablet.run
        if script == monke.LAMP_BIN:
            self.lamp(stdin.decode())
        return 0, b""

    @contextlib.contextmanager
    def on_monke(self, host, color=None, layer=None):
        before = dict(self.pen)
        self.pen.update(layer=layer or layers.MONKE, color=monke.MONKE_COLOR if color is None else color)
        try:
            yield 1
        finally:
            self.pen = before

    def drop(self, host, name):
        hit = [d for d in self.ink if d["layer"] == name and not d["dead"]]
        for d in hit:
            d["dead"] = True
        self._img = None
        return bool(hit)

    def qml(self, host, js, timeout=6):
        if m := re.search(r"activePen\.thickness\s*=\s*(\d+)", js):
            self.pen["thick"] = int(m[1])
            return m[1]
        if "activePen.thickness" in js:
            return str(self.pen["thick"])
        if m := re.search(r"activePen\.color\s*=\s*(\d+)", js):
            self.pen["color"] = int(m[1])
            return m[1]
        if "activeTool='selection'" in js:
            return "busy" if self.sel else "primary"
        if m := re.search(r"addSelectionRect\(Qt\.rect\(([-\d.]+), ([-\d.]+), ([-\d.]+), ([-\d.]+)\)", js):
            x, y, w, h = map(float, m.groups())    # rm page coords
            self.sel = [d for d in self.ink if not d["dead"] and d["layer"] == self.navy["layer"]
                        and all(x <= px - OX <= x + w and y <= py <= y + h for px, py in d["pts"])]
            return "1"
        if "deleteSelectedItems" in js:
            for d in self.sel:
                d["dead"] = True
            self.sel, self._img = [], None
            return "1"
        if "clearSelectedItems" in js:
            self.sel = []
            return "1"
        if "selectionItemCount" in js:
            return str(len(self.sel))
        return "1"

    def view(self, host):
        # like the device: top = -scroll, which monke's unscrolled formula
        # (y - top) maps wrong once the page is scrolled
        return {"doc": "sim", "name": "sim", "page": 0, "pages": 1, "pageId": "p1",
                "scale": 1, "left": 0, "right": -OX, "top": -self.top}

    def page(self, host, v):
        """The .rm file: rewritten whole once the pen has rested FLUSH s."""
        ts = sorted(d["t"] for d in self.ink) + [self.clock.now]
        upto = max([a for a, b in zip(ts, ts[1:]) if b - a >= FLUSH], default=-1)
        out = {"Layer 1": [], layers.MONKE: [], layers.GREET: []}
        for d in self.ink:
            if not d["dead"] and d["t"] <= upto:
                out.setdefault(d["layer"], []).append([(float(x - OX), float(y)) for x, y in d["pts"]])
        return out

    def screen(self):
        if self._img is None:
            self._img = self.render(self.top, H)
        return self._img.copy()

    def render(self, top, height, dots=True):
        img = Image.new("RGB", (W, height), (255, 255, 255))
        d = ImageDraw.Draw(img)
        if dots:                                   # the dot-grid template
            for y in range(21 - top % 42, height, 42):
                for x in range(21, W, 42):
                    d.rectangle((x, y, x + 1, y + 1), fill=(90, 90, 90))
        for k in self.ink:
            if not k["dead"] and len(k["pts"]) > 1:
                d.line([(x, y - top) for x, y in k["pts"]], fill=RGB[k["color"]], width=k["w"])
        return img

    # ── the models ────────────────────────────────────────────────────────
    def cloud_chat(self, model, ses, host, role, session=False, on_text=None):
        def call(messages):
            self.prompts.append((role, messages))
            if role == "greet":
                return "Monke awake."
            if role == "read":
                recs, self.unread = self.unread, []
                for r in recs:
                    r["read"] = True
                if not recs:
                    self.found.append("PHANTOM READ: a commit with no new writing from Navy "
                                      "(Monke read its own ink)")
                wrote = sum(len(r["ink"]) for r in recs)
                got = next((int(m[1]) for _, ln in reversed(self.log)
                            if (m := re.search(r"page committed [(](\d+) strokes", ln))), 0)
                if recs and got < 0.9 * wrote and "Selected for action" not in str(messages):
                    self.found.append(f"PROMPT CUT: Navy wrote {wrote} strokes, only {got} reached the "
                                      f"reader ({recs[0]['text'][:30]!r})")
                if got > wrote + 8:
                    self.found.append(f"ECHO LEAK: {got} strokes committed, Navy wrote {wrote}")
                raw = json.dumps({"kind": "text", "intent": "question",
                                  "text": " ".join(r["text"] for r in recs),
                                  **(self.readings.pop(0) if self.readings else {})})
                if on_text:
                    on_text(raw)
                self.clock.sleep(7)                # reader ~7 s on the device
                return raw
            if role == "turn":
                recs, self.unread = self.unread, []
                for r in recs:
                    r["read"] = True
                raw = json.dumps({"kind": "text", "intent": "question",
                                  "text": " ".join(r["text"] for r in recs)})
                reply = self.replies.pop(0) if self.replies else "Monke has no words."
                whole = raw + "\n\n" + reply
                self.clock.sleep(4)
                if on_text:
                    on_text(raw)
                    self.clock.sleep(1)
                    on_text(whole)
                return whole
            if role == "write":
                if self.fail_writes:
                    self.fail_writes -= 1
                    self.clock.sleep(4)
                    raise brain.BrainError("rate limit: try again in 5 h")
                text = self.replies.pop(0) if self.replies else "Monke has no words."
                self.clock.sleep(6)                # first token
                if on_text:
                    cum = ""
                    for ln in text.split("\n"):
                        cum += ln + "\n"
                        on_text(cum)
                        self.clock.sleep(1.5)
                return text
            return messages[-1]["content"]         # repair: unchanged
        return call

    def confirm(self, reading, focus, host):
        self.clock.sleep(5)                        # Navy taps confirm
        return reading

    # ── play + judge ──────────────────────────────────────────────────────
    def play(self, script):
        out = OUT / self.name
        out.mkdir(parents=True, exist_ok=True)
        for f in out.glob("**/*"):
            if f.is_file():
                f.unlink()
        noop = lambda *a, **k: None

        def control(ctl, state, port, token):
            self.ctl = ctl
            script(self)

        def drawing(kind, src):                    # a framed figure with one curve
            if "BROKEN" in src:
                raise RuntimeError("pdflatex failed: ! Undefined control sequence")
            w, h = (690, 380) if kind == "tex" and "tikzpicture" in src else (420, 150)
            box = [(100, 0), (100 + w, 0), (100 + w, h), (100, h), (100, 0)]
            curve = [(100 + x, int(h / 2 + h / 3 * np.sin(x / 40))) for x in range(0, w, 8)]
            col = 6 if "blue" in src else 7 if "red" in src else 0
            return [Tinted(box, 0), Tinted(curve, col)]
        sysout = sys.stdout
        capture.Capture.pen_last = 0.0
        monke.echo_reset()
        monke._TURN.clear()
        monke._TURN.update(view=None)
        monke._SETTINGS_SEEN.clear()
        with contextlib.ExitStack() as st:
            P = lambda obj, name, val: st.enter_context(patch.object(obj, name, val))
            P(monke, "time", self.clock)
            P(tablet, "run", self.run)
            P(monke, "Capture", Cap)
            P(monke, "grab", lambda *a, **k: self.screen())
            P(monke, "qml_eval", self.qml)
            P(monke, "_on_monke", self.on_monke)
            P(layers, "drop", self.drop)
            P(vectors, "view", self.view)
            P(vectors, "page", self.page)
            P(monke, "cloud_chat", self.cloud_chat)
            P(monke, "_confirm", self.confirm)
            P(monke, "eye_error", lambda host, on, reason="": (self.eye.append((self.clock.now, on)),
                                                      on and self.eye_reasons.append(reason)))
            P(monke, "_drawing", drawing)
            layout = monke._layout                 # every text on its way to the page
            P(monke, "_layout", lambda text, *a, **k: self.laid.append(text) or layout(text, *a, **k))
            P(monke, "scribe_ocr", lambda png: [])
            P(brain, "chat", lambda *a, **k: self.found.append("BRAIN called outside cloud_chat") or "")
            P(monke, "tablet_settings", lambda host: {
                "reader": "chatgpt:gpt-5.6-luna" if self.turn == "merged" else "sonnet",
                "writer": "chatgpt:gpt-5.6-luna" if self.turn == "merged" else "sonnet",
                "context": "session", "turn": self.turn})
            P(monke, "_control_server", control)
            # Simulated tablet transport: never resolve a real SSH route here.
            P(monke, "_resolve_link", lambda cfg, **_: cfg.get("capture", {}).get("host") or "rm2")
            for name in ("_detach_stdin", "ensure_xovi", "_point_tablet",
                         "publish_models", "publish_usage", "usage_add"):
                P(monke, name, noop)
            P(monke, "_SESSIONS", out)
            P(monke, "_FLOOR_FILE", out / "floor")
            P(monke, "_USAGE", out / "usage.json")
            P(monke, "_SETTINGS_FILE", out / "settings.json")
            P(monke, "_VEC_MASK", {})
            sys.stdout = type("Out", (), {"write": staticmethod(self.write_log),
                                          "flush": staticmethod(lambda: None)})()
            try:
                monke.run()
            except Exception as e:                 # the daemon died: the worst outcome
                import traceback
                self.found.append(f"DAEMON CRASHED: {type(e).__name__}: {e}")
                self.write_log(traceback.format_exc() + "\n")
            finally:
                sys.stdout = sysout
        (out / "log.txt").write_text(
            "\n".join(f"{t - self.t0:7.1f}  {ln}" for t, ln in self.log), encoding="utf-8")
        tall = max([H] + [y for k in self.ink for _, y in k["pts"]]) + 80
        self.render(0, tall).save(out / "page.png")
        return self.judge()

    def _mask(self, items, r, tall):
        img = Image.new("1", (W, tall), 0)
        d = ImageDraw.Draw(img)
        for k in items:
            if len(k["pts"]) > 1:
                d.line(k["pts"], fill=1, width=2 * r)
        return np.asarray(img, dtype=bool)

    def _on(self, k, mask):
        pts = [(x, y) for x, y in k["pts"] if 0 <= x < W and 0 <= y < mask.shape[0]]
        return sum(mask[y, x] for x, y in pts) / len(pts) if len(pts) >= 4 else 0.0

    def judge(self):
        out = list(self.found)
        for check in self.checks:
            out += check()
        if self.gate:
            out.append(f"PEN GATE: {self.gate} lamp batch(es) started within 0.5 s of Navy's pen")
        if self.mixed:          # known ceiling: a batch already in flight can't be stopped
            self.notes.append(f"{self.mixed} of Navy's strokes landed inside a lamp batch already "
                              "inking (device: lines can jump to his pen) — don't write while Monke inks")
        log = "\n".join(ln for _, ln in self.log)
        for pat, msg in ((r"Traceback", "a turn raised (traceback in the log)"),
                         (r"\[turn\] failed", "a turn failed"),
                         (r"local model instead", "fell back to the local model"),
                         (r"AI unreachable", "AI unreachable")):
            if pat in self.allow:
                continue
            if n := len(re.findall(pat, log)):
                out.append(f"{msg} ×{n}")
        for pat, msg in ((r"no room for the next part", "reply cut: no room for a part (rest only in the log)"),
                         (r"NO ROOM", "reply not inked: no room"),
                         (r"NOT inked", "reply withheld by the page guard"),
                         (r"\[prompt\] kept", "#P kept")):
            for t, ln in self.log:
                if re.search(pat, ln):
                    out.append(f"{msg}  [{t - self.t0:.0f}s: {ln.strip()[:90]}]")
        for t, ln in self.log:      # a turn that put nothing (or less) on the page must show '!'
            if re.search(r"nothing to ink|render failed|AI unreachable", ln):
                after = [on for te, on in self.eye if te >= t]
                if not after or not after[0]:
                    out.append(f"SILENT FAILURE: no '!' on the eye after: {ln.strip()[:70]}")
                break
        for w in self.writes:
            if w["expect"] and not w["read"]:
                out.append(f"PROMPT LOST: {w['text'][:50]!r} was never read")
            if w["text"].lstrip().startswith("#P") and w["read"] and any(not d["dead"] for d in w["ink"]):
                out.append(f"#P not erased: {w['text'][:40]!r}")
        if self.replies:
            out.append(f"{len(self.replies)} scripted repl(ies) never asked for")
        live = [k for k in self.ink if not k["dead"]]
        tall = max([H] + [y for k in live for _, y in k["pts"]]) + 10
        hits = []               # Monke's fault only when it inked AFTER Navy's writing was there
        for w in self.writes:   # (b0 + 1 = the batch in flight as he began: a race nobody wins)
            navy = self._mask([k for k in w["ink"] if not k["dead"]], 5, tall)
            hits += [k for k in live if k["who"] == "monke" and k["batch"] > w["b0"] + 1
                     and self._on(k, navy) > 0.3]
        if hits:
            ys = [y for k in hits for _, y in k["pts"]]
            out.append(f"INK COLLISION: {len(hits)} Monke strokes on Navy's writing (page y {min(ys)}-{max(ys)})")
        monk = [k for k in live if k["who"] == "monke"]
        over, seen = [], {}
        for b in sorted({k["batch"] for k in monk}):
            mine = [k for k in monk if k["batch"] == b]
            earlier = [k for k in monk if k["batch"] < b]
            if earlier:
                m = self._mask(earlier, 3, tall)
                over += [k for k in mine if tuple(k["pts"]) not in seen and self._on(k, m) > 0.5]
            seen.update({tuple(k["pts"]): 1 for k in mine})
        shape = {}                                 # same stroke, same x, another height = inked twice
        for k in monk:
            x0, y0 = k["pts"][0]
            shape.setdefault((x0, tuple((x, y - y0) for x, y in k["pts"])), set()).add(y0)
        twice = sum(1 for ys in shape.values() if len(ys) > 1)
        if twice > 30:      # a scroll mid-batch leaves a few orphans; a whole reply twice is the bug
            out.append(f"REPLY INKED TWICE: {twice} Monke strokes exist at two heights")
        if over:
            ys = [y for k in over for _, y in k["pts"]]
            out.append(f"REPLY OVER REPLY: {len(over)} Monke strokes on older Monke ink (page y {min(ys)}-{max(ys)})")
        return out


# ── scenarios: what Navy does in a session ─────────────────────────────────
GRAPH = "$$\\begin{tikzpicture}\\begin{axis}\\addplot[%s]{x};\\end{axis}\\end{tikzpicture}$$"
PROSE = "It maps a signal from time to frequency. Inverse brings it back."
PROSE2 = "Second answer. The curve below shows the same thing in red."
PROSE3 = "Third answer, written lower on the page than the others."


def basic(s):
    """Tap, greeting, one question, one prose + equation reply."""
    s.replies += [PROSE + "\n$$X(\\omega) = \\int x(t) e^{-j \\omega t} dt$$"]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("What is the Fourier transform?"))


def three_turns(s):
    """Three questions down one screen, each written after 'ready'."""
    s.replies += [PROSE, "Sinc in time is a box in frequency.", "Yes. Width and bandwidth trade off."]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("What is the Fourier transform?"))
    s.at("ready", 4, lambda: s.write("And of a sinc?"))
    s.at("ready", 4, lambda: s.write("Is that the uncertainty principle?"))


def quick_followup(s):
    """Navy writes the next question the moment the reply stops inking,
    before the daemon says 'ready'."""
    s.replies += [PROSE, "Sinc in time is a box in frequency."]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("What is the Fourier transform?"))
    s.at("monke (", 2.5, lambda: s.write("And of a sinc?"))


def write_during_reply(s):
    """Navy keeps writing under his prompt while the reply inks (run 16)."""
    s.replies += [PROSE + "\n" + GRAPH % "black" + "\n" + GRAPH % "blue", "Overlap drawn."]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Draw two graphs of x(t)."))
    s.at("first part inking", 0.2, lambda: s.write("y(t) should also overlap."))


def graphs_side_by_side(s):
    """Four graphs asked for: two rows of two, colours kept."""
    s.replies += ["Four graphs.\n" + "\n".join(GRAPH % c for c in ("black", "red", "blue", "black"))]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Create four graphs."))


def scroll_between_turns(s):
    """Fill the screen, scroll by hand, ask again (runs 13-16)."""
    s.replies += [PROSE + "\n" + GRAPH % "black" + "\n" + GRAPH % "red",
                  "Second answer after the scroll.\n" + GRAPH % "blue"]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Draw two graphs."))
    s.at("ready", 4, lambda: s.scroll(900))
    s.at(None, 3, lambda: s.write("Now draw one more."))


def scroll_right_after_reply(s):
    """Navy scrolls the instant the reply is done — before 'ready' — then writes."""
    s.replies += [PROSE + "\n" + GRAPH % "black", "Answer two.", "Answer three."]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Draw a graph."))
    s.at("monke (", 1.0, lambda: s.scroll(700))
    s.at("ready", 3, lambda: s.write("Explain it in one line."))
    s.at("ready", 4, lambda: s.write("And once more?"))


def scroll_during_reply(s):
    """Navy scrolls while the reply is still inking."""
    s.replies += [PROSE + "\n" + GRAPH % "black" + "\n" + GRAPH % "blue", "After the scroll."]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Draw two graphs."))
    s.at("first part inking", 1.0, lambda: s.scroll(500))
    s.at("ready", 4, lambda: s.write("What happened?"))


def scroll_between_parts(s):
    """Navy scrolls between two parts of a streamed reply."""
    s.replies += [PROSE + "\n" + GRAPH % "black" + "\n" + GRAPH % "blue" + "\nDone.", "After the scroll."]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Draw two graphs."))
    s.at("first part inking", 4.2, lambda: s.scroll(500))
    s.at("ready", 4, lambda: s.write("What happened?"))


def scroll_before_reply(s):
    """Navy writes at the bottom of the screen, then scrolls to make room
    while Monke is still reading."""
    s.replies += [PROSE + "\n" + GRAPH % "black"]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Draw a graph.", y=1560))
    s.at("committed", 2, lambda: s.scroll(900))


def hash_p(s):
    """#P prompts are erased once answered — also on a scrolled page."""
    s.replies += [PROSE, "Second.\n" + GRAPH % "black"]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("#P What is the Fourier transform?"))
    s.at("ready", 4, lambda: s.scroll(600))
    s.at(None, 3, lambda: s.write("#P Draw it."))


def page_fills_up(s):
    """Questions down to the bottom of the screen: the reply waits at the
    bottom, Navy scrolls, the reply continues (roadmap: scroll to complete)."""
    s.replies += [PROSE + "\n" + GRAPH % "black", PROSE2 + "\n" + GRAPH % "red", PROSE3 + "\n" + GRAPH % "blue"]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Graph one please."))
    s.at("ready", 4, lambda: s.write("Graph two please."))
    s.at("page bottom", 3, lambda: s.scroll(400))        # two swipes, like a hand does
    s.at(None, 1.5, lambda: s.scroll(500))
    s.at("ready", 4, lambda: s.write("Graph three please."))


def stop_and_resume(s):
    """Hold to stop, tap to start again, keep asking on the same page."""
    s.replies += [PROSE, "Welcome back answer."]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("What is the Fourier transform?"))
    s.at("ready", 4, s.hold)
    s.at("session off", 5, s.tap)
    s.at("watching", 3, lambda: s.write("And of a sinc?"))


def hover_while_reply(s):
    """Navy's pen hovers over the page for 12 s while the reply wants to ink."""
    s.replies += [PROSE + "\n" + GRAPH % "black"]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Draw a graph."))
    s.at("write with", 1, lambda: s.hover(12))


def writes_before_greeting_done(s):
    """Navy starts writing right after the tap, while the greeting inks."""
    s.replies += [PROSE]
    s.at(None, 1, s.tap)
    s.at("session start", 1.0, lambda: s.write("What is the Fourier transform?", y=600))


def long_prompt(s):
    """A three-line question, with a thinking pause in the middle."""
    s.replies += [PROSE]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Explain the Fourier transform and why a narrow pulse in time "
                                        "needs a wide band in frequency, with one example."))


def pause_mid_prompt(s):
    """Navy writes half a question, thinks for 6 s, writes the rest."""
    s.replies += [PROSE, PROSE2]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("What is the Fourier"))
    s.at(None, 11, lambda: s.write("transform of a sinc?"))


def empty_reply(s):
    """The model answers with nothing at all."""
    s.replies += ["", PROSE2]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("What is the Fourier transform?"))
    s.at("ready", 4, lambda: s.write("Hello?"))


def broken_drawing(s):
    """The only thing in the reply is a drawing that fails to compile."""
    s.replies += ["$$" + chr(92) + "begin{circuitikz} BROKEN " + chr(92) + "end{circuitikz}$$", PROSE2]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Draw an op-amp filter."))
    s.at("ready", 4, lambda: s.write("Hello?"))


def stop_during_reply(s):
    """Navy holds the eye (stop) while a reply is still inking, then starts again."""
    s.replies += [PROSE + chr(10) + GRAPH % "black" + chr(10) + GRAPH % "blue", PROSE2]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Draw two graphs."))
    s.at("first part inking", 1.5, s.hold)
    s.at("session off", 5, s.tap)
    s.at("watching", 3, lambda: s.write("And now?"))


def brain_fails(s):
    """The brain errors on the first answer (rate limit): the turn fails
    visibly — '!' on the eye with the reason, nothing inked — and the next
    question still gets an answer."""
    s.fail_writes = 1
    s.allow = (r"Traceback", r"\[turn\] failed")
    s.replies += [PROSE2]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("What is the Fourier transform?"))
    s.at("ready", 4, lambda: s.write("Hello?"))

    def check():
        out = []
        if not any("rate limit" in r for r in s.eye_reasons):
            out.append(f"EYE: no '!' with the brain's reason (got {s.eye_reasons!r})")
        if not any("[turn] failed" in ln and "rate limit" in ln for _, ln in s.log):
            out.append("the failed turn was not logged with its reason")
        if not s.eye or s.eye[-1][1]:
            out.append("EYE: '!' never cleared after the next answer")
        return out
    s.checks.append(check)


def refine_code_graph(s):
    """A jittered poly2 graph is beautified beside its source with no writer."""
    desc = "axes x 0..6 and y 0..2, one hump peaking at x 3"
    source = [[(round(150 + (x - 200) * .45), round(150 + (y - 300) * .45))
               for x, y in stroke] for stroke in jitter.graph("poly2", seed=42)]
    hand = s.sketch(source)
    before = [list(d["pts"]) for d in hand]
    s.readings += [{"intent": "refine", "target": "N1", "target_kind": "graph", "target_desc": desc}]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("#P neaten the graph above"))

    def check():
        out = []
        if any(role == "write" for role, _ in s.prompts):
            out.append("REFINE CODE: writer was called")
        ink = [k for k in s.ink if k["who"] == "monke" and k["layer"] == layers.MONKE and not k["dead"]]
        if len(ink) < 3:
            out.append(f"REFINE CODE: only {len(ink)} clean strokes")
        if ink and min(x for d in ink for x, _ in d["pts"]) <= max(x for d in hand for x, _ in d["pts"]):
            out.append("REFINE CODE: redraw is not beside the source")
        if not any("[refine] code: graph" in ln for _, ln in s.log):
            out.append("REFINE CODE: success metric missing")
        if any(d["dead"] for d in hand) or [list(d["pts"]) for d in hand] != before:
            out.append("NAVY INK TOUCHED: his hand-drawn graph was erased or moved")
        return out
    s.checks.append(check)


def refine_fallback(s):
    """An unrecognised scribble falls back to exactly one writer call."""
    source = [[(round(250 + (x - 700) * .45), round(300 + (y - 800) * .45))
               for x, y in stroke] for stroke in jitter.scribble(seed=7)]
    s.sketch(source)
    s.readings += [{"intent": "refine", "target": "N1", "target_kind": "sketch",
                    "target_desc": "an irregular freehand scribble"}]
    s.replies += [GRAPH % "black"]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("#P clean this sketch"))

    def check():
        writes = [m for role, m in s.prompts if role == "write"]
        out = [] if len(writes) == 1 else [f"REFINE FALLBACK: {len(writes)} writer calls, want 1"]
        if not any("[refine] fallback:" in ln for _, ln in s.log):
            out.append("REFINE FALLBACK: reason was not logged")
        return out
    s.checks.append(check)


def place_beside(s):
    """A writer @place directive puts prose beside N1 without overlap."""
    target = s.sketch([[(180, 300), (500, 300), (500, 500), (180, 500), (180, 300)]])
    s.readings += [{"target": "N1", "target_kind": "diagram"}]
    s.replies += ["@place beside N1\nPlaced answer."]
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Explain the box"))

    def check():
        ink = [k for k in s.ink if k["who"] == "monke" and k["layer"] == layers.MONKE and not k["dead"]]
        out = []
        if not ink or min(x for d in ink for x, _ in d["pts"]) <= max(x for d in target for x, _ in d["pts"]):
            out.append("PLACE BESIDE: reply is not right of N1")
        if not any("[place] beside N1" in ln for _, ln in s.log):
            out.append("PLACE BESIDE: resolver log missing")
        return out
    s.checks.append(check)


def merged_turn(s):
    """One merged model call yields both reading JSON and streamed reply."""
    s.turn = "merged"
    s.replies.append("Merged answer lands once.")
    s.at(None, 1, s.tap)
    s.at("watching", 3, lambda: s.write("Use one call"))

    def check():
        roles = [role for role, _ in s.prompts]
        out = ([] if roles.count("turn") == 1 and "read" not in roles and "write" not in roles
               else [f"MERGED ROUTE WRONG: {roles}"])
        if not any("[turn] merged: first-ink" in line for _, line in s.log):
            out.append("MERGED METRIC: first-ink log missing")
        return out
    s.checks.append(check)


def lasso_ask(s):
    """A focus ask outside a session answers below the selected page box."""
    s.replies.append("Selected ink explained.")
    box = (120, 300, 900, 520)
    s.at(None, 1, lambda: s.write("Selected question", y=360))
    s.at(None, 5, lambda: s.ctl.put(("focus", "ask", box)))

    def check():
        ys = [y for d in s.ink if d["who"] == "monke" and not d["dead"] for _, y in d["pts"]]
        return [] if ys and min(ys) > box[3] else ["LASSO ASK: reply was not below selection"]
    s.checks.append(check)


SCENARIOS = [basic, three_turns, quick_followup, write_during_reply, graphs_side_by_side,
             scroll_between_turns, scroll_right_after_reply, scroll_during_reply, scroll_between_parts,
             scroll_before_reply, hash_p,
             page_fills_up, stop_and_resume, hover_while_reply, writes_before_greeting_done,
             long_prompt, pause_mid_prompt, empty_reply, broken_drawing, stop_during_reply,
             refine_code_graph, refine_fallback, place_beside,
             brain_fails, merged_turn, lasso_ask]


def main(argv):
    pick = [f for f in SCENARIOS if not argv or any(a in f.__name__ for a in argv)]
    bad = 0
    for f in pick:
        t = real_time.time()
        s = Sim(f.__name__)
        probs = s.play(f)
        bad += bool(probs)
        print(f"{'FAIL' if probs else 'ok  '}  {f.__name__:28s} {real_time.time() - t:5.1f}s  "
              f"({s.clock.now - s.t0:.0f}s on the tablet)  — {f.__doc__.strip().splitlines()[0]}")
        for p in probs:
            print(f"        {p}")
        for p in s.notes:
            print(f"        note: {p}")
    print(f"{len(pick) - bad}/{len(pick)} scenarios clean; pages + logs: {OUT.relative_to(ROOT)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
