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
import json
import queue
import re
import shlex
import subprocess
import time
import tomllib
from dataclasses import replace
from pathlib import Path

import numpy as np

from capture import Capture, StrokeBuilder, render_strokes, CANVAS_W, CANVAS_H
from screen import grab, ink_floor, ink_mask, free_bands, page_diff
from ink import Tinted, load_styles, render
from oracle import image_msg
from scribe import ocr as scribe_ocr
from write import decimate, to_lamp
import brain
import harness
import plan_schema
import plan_stream
import protocol
import rebase
import regions
import render_plan
import selection
import skill
import tablet
import paths
from deps import ssh_argv
from turn_state import SettleGate, TurnState

# Shipped defaults (repo config.toml in dev, bundle copy when installed). Tests
# patch this; _load_cfg() adds the per-user overlay in the installed app.
REPO_CFG = paths.DEFAULT_CFG


def _load_cfg() -> dict:
    """Effective config: REPO_CFG + user overlay (installed app / SMRITI_HOME).
    The installed app is wireless-only: USB endpoints are dropped there."""
    import link
    with open(REPO_CFG, "rb") as f:
        cfg = tomllib.load(f)
    if paths.overlay_active():
        cfg = paths.deep_merge(cfg, paths.load_user())
    if paths.FROZEN:
        cfg = link.wireless(cfg)
    return cfg


def _state_dir() -> Path:
    """Laptop-side state: ~/.config/smriti in dev, data_dir()/state installed."""
    return paths.data_dir() / "state" if paths.overlay_active() else Path.home() / ".config" / "smriti"

# The prompt lives in skill/smriti/ (SKILL.md + references/protocol.md +
# kinds/*.md), composed by skill.py; this name = every kind's rules.
MONKE_SYSTEM = skill.reply_system(None)

MARKER_X, MARKER_Y = 1352, 1826

# multitouch event codes (pt_mt, /dev/input/event2)
ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 53, 54, 57
TOUCH_X_MAX, TOUCH_Y_MAX = 767.0, 1023.0
EV_ABS = 3


LAMP_BIN = __import__("os").environ.get("SMRITI_LAMP", "/home/root/.vellum/bin/lamp")


def _pen_quiet(quiet: float = 0.5, cap: float = 20.0) -> bool:
    """Wait until Navy's pen is quiet (no event1 event for `quiet` s). Lamp
    and his pen share event1: a hovering pen during lamp ink = xochitl drew
    lines to his pen (run 12 zigzag + strike lines). False = gave up at cap."""
    from capture import Capture
    end = time.time() + cap
    while time.time() - Capture.pen_last < quiet:
        if time.time() > end:
            return False
        time.sleep(0.05)
    return True


def lamp(cmds: str, host: str) -> None:
    """Replay lamp commands over the tablet's persistent shell (an ssh spawn
    per call cost ~0.4 s; device 2026-09-30). Raises on failure. Waits for a
    quiet pen first — ponytail: checked per batch only, a pen that comes
    near mid-batch still mixes; split batches if that shows up."""
    if not _pen_quiet():
        print("[lamp] pen still active after 20 s — inking anyway", flush=True)
    t0 = time.time()           # pendump shadow tag: host injection window
    try:
        rc, _ = tablet.run(host, LAMP_BIN, cmds.encode("ascii"),
                           timeout=60 + cmds.count("\n") / 100)
    except (TimeoutError, ConnectionError, OSError) as e:
        raise RuntimeError(f"lamp: {e}") from e
    if rc:
        raise RuntimeError(f"lamp exited {rc}")
    # Observability only: never let tagging block or alter live inking.
    __import__("lampmark").mark(t0, time.time(), cmds.count(" down "), f"rc={rc}")


# Content safety (Navy 2026-09-26): every Smriti stroke and eraser pass goes
# on the page's "Monke" layer — his handwriting lives on other layers, which
# the (layer-scoped) eraser and a bad reply can't reach; one clear undoes
# Monke. No verified layer switch = no ink. [monke] safe_layer = false only
# for tablets without the eye patch.
SAFE_LAYER = True
VERIFY = True          # [monke] verify: screenshot-check every ink, redraw misses


def _on_monke(host: str, color: int | None = None, layer: str | None = None):
    import contextlib
    import layers
    if not SAFE_LAYER:
        return contextlib.nullcontext()
    # Monke writes in ITS colour (black by default), never inheriting Navy's
    # current pen — a greeting came out in his red (device, 2026-09-26)
    return layers.on_layer(host, layer or layers.MONKE,
                           color=MONKE_COLOR if color is None else color)


# Echo mask: where Smriti itself inked on the current page. Its ink comes
# back on the pen stream with ssh lag and can outlive the drain, then ride
# along with Navy's next strokes (device session 2026-09-26: a circuit turn
# was read as Monke's previous equation). Strokes mostly ON this mask are
# Monke's own and get dropped at commit.
_ECHO = None
_ECHO_N = 0        # marked strokes whose echo has not come back yet


def echo_reset() -> None:
    global _ECHO, _ECHO_N
    _ECHO, _ECHO_N = None, 0


def _echo_mark(strokes, clear: bool = False) -> None:
    global _ECHO, _ECHO_N
    from PIL import Image, ImageDraw
    if _ECHO is None:
        _ECHO = Image.new("1", (CANVAS_W, CANVAS_H), 0)
    d = ImageDraw.Draw(_ECHO)
    if clear:                                 # erased area is free again
        xs = [x for s in strokes for x, _ in s]
        ys = [y for s in strokes for _, y in s]
        if xs:
            d.rectangle((min(xs) - 14, min(ys) - 14, max(xs) + 14, max(ys) + 14), fill=0)
        return
    for s in strokes:
        if len(s) > 1:
            d.line(s, fill=1, width=14)
            _ECHO_N += 1


def drop_echo(strokes: list) -> list:
    """Navy's strokes only: drop those with >= 60% of points on Monke ink.
    An echo comes back once: when all but a residue has been dropped, the
    marks are spent. Kept for good, they ate Navy's writing wherever Monke's
    ink had been on screen — after a scroll the daemon missed, 14 of 32
    prompt strokes (sim 2026-10-04); also any note written onto a reply."""
    global _ECHO_N
    if _ECHO is None:
        return strokes
    px = _ECHO.load()
    keep = []
    for s in strokes:
        pts = [(x, y) for x, y in s if 0 <= x < CANVAS_W and 0 <= y < CANVAS_H]
        on = sum(1 for x, y in pts if px[x, y])
        if not pts or on < 0.6 * len(pts):
            keep.append(s)
    _ECHO_N -= len(strokes) - len(keep)
    if _ECHO_N <= ECHO_RESIDUE:
        echo_reset()
    return keep


ECHO_RESIDUE = 8   # ponytail: echo left after drop_echo was 0-6 strokes (runs to 12); a
                   # real prompt under 9 strokes written DURING a reply is still lost


def _echo_only(strokes: list, baseline, now) -> bool:
    """True = these strokes are Monke's echo, not Navy. page_diff alone lost
    Navy's writing done while a reply inked (the post-reply baseline already
    holds it, run 12): it may veto only a small residue."""
    if not strokes:
        return True
    return (len(strokes) <= ECHO_RESIDUE and baseline is not None and now is not None
            and page_diff(baseline, now) is None)


class Drawn(list):
    """A stroke from a DRAWING (LaTeX, circuit, plot, svg) — inked with the
    thin pen. Figures are traced at their own scale, so at the handwriting
    pen width (4 px) their small labels and ticks turned to blobs ("0.5"
    lost its point); thin = 2 px keeps them legible (device, 2026-09-26).
    color = xochitl pen colour from the render (run 14: blue inked black)."""
    def __init__(self, pts=(), color: int | None = None):
        super().__init__(pts)
        self.color = getattr(pts, "color", 0) if color is None else color


MONKE_COLOR = 0          # xochitl pen enum: 0 black (Monke's own ink colour)
PEN_THIN = 1            # xochitl activePen.thickness: 1 = 2 px, 2 = 4 px (measured)


def _ink(reply: list, pressure: int, step: int, host: str,
         color: int | None = None, thin: bool | None = None,
         layer: str | None = None) -> bool:
    """Put the reply strokes on the page: lamp live stylus injection (real,
    saved notebook ink) on the Monke layer. Document-write mode was archived
    — see docs/DOCWRITE-DEFERRED.md for why. Drawing strokes (Drawn, or all
    when thin=True) use the thin pen; prose keeps Navy's width. False = not
    inked."""
    _set_stage("inking")
    if thin is None:
        drawn = [s for s in reply if isinstance(s, Drawn)]
        prose = [s for s in reply if not isinstance(s, Drawn)]
    else:
        drawn, prose = (reply, []) if thin else ([], reply)
    try:
        with _on_monke(host, color, layer):
            w0 = None
            if prose:
                lamp(to_lamp(prose, pressure, step), host)
            if drawn:
                if SAFE_LAYER:          # on_layer's exit restores Navy's width
                    if prose and VERIFY:
                        w0 = qml_eval(host, "documentViewTools.activePen.thickness")
                    qml_eval(host, f"documentViewTools.activePen.thickness={PEN_THIN}")
                lamp(to_lamp(drawn, pressure, step), host)
            if VERIFY:
                # ONE e-ink wait + screenshot for both (two cost ~2 s more):
                # drawing first, thin pen still selected; then prose width
                time.sleep(1.0)
                shot = _shot()
                if drawn:
                    _verify_ink(drawn, host, pressure, step, shot=shot)
                if prose:
                    if w0 is not None and w0.strip().isdigit():
                        qml_eval(host, f"documentViewTools.activePen.thickness={w0.strip()}")
                    _verify_ink(prose, host, pressure, step, shot=shot)
        _echo_mark(reply)
        return True
    except RuntimeError as e:
        print(f"[ink] {e} — NOT inking (content safety)", flush=True)
        return False


STREAM = True          # [monke] stream: ink a Claude reply line by line as it arrives
SCROLL_WAIT = 20       # [monke] scroll_wait: s a reply waits at the page bottom for Navy's scroll (0 = off)
PAGE_HEIGHT = 0        # [monke] page_height: page px a reply may run down to (0 = no end; a device page scrolled past 12000)
VERIFY_JITTER = 3      # px: a measured view shift this small is detection jitter, not a scroll
REPLY_TOP = 150        # a part Navy scrolled past goes on at this screen row (xochitl's top UI row ends at 100)


def _band_busy(shot, strokes: list, mine: list, pad: int = 10, min_px: int = 40) -> bool:
    """New ink (not Monke's own) inside the box this unit will cover. Run 16:
    Navy kept writing under his prompt while the reply streamed; line 1
    was inked over it."""
    import numpy as np
    from PIL import Image as _I, ImageDraw as _D
    from screen import stroke_mask
    xs = [x for s in strokes for x, _ in s]
    ys = [y for s in strokes for _, y in s]
    x0, y0 = max(0, min(xs) - pad), max(0, min(ys) - pad)
    x1, y1 = min(CANVAS_W, max(xs) + pad), min(CANVAS_H, max(ys) + pad)
    ink = stroke_mask(shot)[y0:y1, x0:x1]
    if mine:
        own = _I.new("1", (CANVAS_W, CANVAS_H), 0)
        d = _D.Draw(own)
        for s in mine:
            if len(s) > 1:
                d.line(s, fill=1, width=12)
        ink = ink & ~np.asarray(own, dtype=bool)[y0:y1, x0:x1]
    return int(ink.sum()) >= min_px


BESIDE_GAP = 40


def _navy_ink(shot, vt, mine: list, cell: int = 16, min_px: int = 4) -> list:
    """Ink that is not this reply's own, page coordinates, for rebase.rebase:
    the screenshot's ink (minus `mine`) as one box per 16 px cell, plus the
    page file's strokes on Navy's layers when x maps (vt.x_trusted: a
    registered offset or an unscrolled page; the .rm lags ~10 s, the
    screenshot doesn't). Run 16: Navy kept writing under his prompt while
    the reply streamed; line 1 was inked over it."""
    from PIL import Image as _I, ImageDraw as _D
    from screen import stroke_mask
    ink = stroke_mask(shot)
    if mine:
        own = _I.new("1", (CANVAS_W, CANVAS_H), 0)
        d = _D.Draw(own)
        for s in mine:
            if len(s) > 1:
                d.line(s, fill=1, width=12)
        ink = ink & ~np.asarray(own, dtype=bool)
    out = []
    ys, xs = np.nonzero(ink)
    if len(ys):
        k = (ys // cell) * (CANVAS_W // cell + 1) + xs // cell
        m = int(k.max()) + 1
        x0, y0 = np.full(m, CANVAS_W), np.full(m, CANVAS_H)
        x1, y1 = np.full(m, -1), np.full(m, -1)
        np.minimum.at(x0, k, xs), np.minimum.at(y0, k, ys)
        np.maximum.at(x1, k, xs), np.maximum.at(y1, k, ys)
        out = [[vt.screen_to_page(x0[c], y0[c]), vt.screen_to_page(x1[c], y1[c])]
               for c in np.nonzero(np.bincount(k) >= min_px)[0]]
    layers = shot.info.get("layers") if vt.x_trusted else None
    return out + [s for name, ss in (layers or {}).items() if name not in rebase.OWN for s in ss]


def _seen(shot):
    """Where the ink is on screen now: pixels only (no page-file overlay,
    which lags and maps by the view fields)."""
    from screen import stroke_mask
    img = shot.copy()
    img.info.pop("vectors", None)
    img.info.pop("vectors_mask", None)
    return stroke_mask(img)


def _beside(row, strokes: list) -> list | None:
    """Drawing strokes moved to the right of the previous drawing (row =
    its right x, top y) when they fit the page width, else None. Run 14:
    ~700 px graphs stacked on a 1404 px page ran out of room."""
    if row is None or not strokes or not all(isinstance(s, Drawn) for s in strokes):
        return None
    x0 = min(px for s in strokes for px, _ in s)
    y0 = min(py for s in strokes for _, py in s)
    w = max(px for s in strokes for px, _ in s) - x0
    left = row[0] + BESIDE_GAP
    k = min(1.0, (CANVAS_W - BESIDE_GAP - left) / max(w, 1))
    if k < 0.7:            # ponytail: two 7.5 cm graphs need k ~0.72; smaller = unreadable ticks
        return None
    return [Drawn([(round(left + (px - x0) * k), round(row[1] + (py - y0) * k)) for px, py in s],
                  s.color)
            for s in strokes]


def _reply_top(floor: int, strokes: list, focus_box, shot) -> int:
    """First reply row. The real screen's lowest ink beats the carried floor:
    a manual vertical scroll is not detected and left it stale (run 13:
    reply at y 1390 under a prompt ending at 830, graphs out of room)."""
    if shot is not None:
        floor = ink_floor(shot)
    return max(floor, max((py for s in strokes for _, py in s), default=0),
               focus_box[3] if focus_box else 0) + 50


def _directive_place(strokes, directive, regions, occ, floor, bottom):
    """Apply one writer placement hint. Caller still runs live page guards."""
    import place
    box = harness.strokes_box(strokes)
    if not box:
        return None, None
    d = directive
    if d.get("op") == "refine":
        d = {"op": "place", "how": "beside", "id": d.get("id")}
    spot = place.resolve(d, regions, occ,
                         (box[2] - box[0] + 1, box[3] - box[1] + 1), floor, bottom)
    return (place.shift(strokes, spot.x, spot.y, spot.k), spot) if spot else (None, None)


class _Streamer:
    """Ink a streamed reply unit by unit (a finished line, or a closed $$ /
    <svg> block) while the model is still writing: Navy 2026-09-30, 'should
    operate live'. One layer session for all units; verify once at the end.
    Before the first unit: page guard, then the greeting fades. Each unit
    is anchored in page coordinates (rebase.py): Navy scrolled -> it inks
    at its page spot in the view now; his new ink in its band -> it goes
    under that ink; past the screen bottom -> wait SCROLL_WAIT s for his
    scroll, then the rest stays in the log; never scales."""

    def __init__(self, question, style, y, bottom, host, step, greet, log, fix,
                 regions=None, occ=None, floor=0, ses=None):
        self.q, self.style, self.y, self.bottom = question, style, y, bottom
        self.host, self.step, self.greet, self.log, self.fix = host, step, greet, log, fix
        self.used, self.inked, self.ctx, self.w0, self.thin = 0, [], None, None, False
        self.started = self.blocked = self.stopped = False
        self.partial = False     # pending unit has page-anchored strokes still to lamp
        self.row = None          # (right x, top y) of a drawing with room beside it
        self.moved = False       # re-anchored after a scroll: the old-spot page guard is void
        self.body = False        # past the reply's leading SPP directive lines
        self.regions, self.occ, self.floor = regions or {}, occ, floor
        self.directive, self.column_x = None, None
        # ChatGPT plan mode: the reply is a smriti_plan JSON; each block inks
        # as it closes (plan_stream). None = not known yet, False = text grammar
        self.ses, self.plan, self.scan = ses, None, None
        self.taken0 = set(_graphs(ses)) if ses is not None else set()
        # page <-> screen as of the question's frame (a registered #P offset wins)
        self.v = _TURN.get("view") or {}
        self.vt = rebase.ViewTransform.from_view(self.v, _TURN.get("offset"))
        self.anchors = rebase.Plan(self.v.get("pageId"))   # the reply's parts, page coordinates

    def feed(self, text: str) -> None:
        if self.stopped:
            return
        try:
            if self.plan is None and text.strip():
                self.plan = plan_schema.is_plan(text)
                if self.plan:
                    self.scan = plan_stream.Scanner(self.taken0)
            if self.plan:
                blocks = self.scan.feed(text[self.used:])
                self.used = len(text)
                for b in blocks:
                    if not self.body:            # the plan's "place" closed before block 0
                        self.body = True
                        if self.scan.place:
                            found, _ = protocol.parse_directives("@place " + self.scan.place)
                            self.directive = next((d for d in found if d.get("op") == "place"),
                                                  None)
                    self._block(b)
                    if self.stopped:
                        break
                return
            units, used = harness.stream_units(text[self.used:])
            self.used += used
            for u in units:
                if not self.body and u.startswith("@"):
                    found, _ = protocol.parse_directives(u.strip())
                    if self.directive is None:
                        self.directive = next((d for d in found
                                               if d.get("op") in ("place", "refine")), None)
                    continue             # SPP directive (@target/@place/@refine): never inked
                self.body = True
                self._unit(u)
                if self.stopped:
                    break
        except Exception as e:           # never break the model stream
            print(f"[stream] {str(e)[:120]} — stopped; rest in the log", flush=True)
            self.stopped = True

    def _start(self) -> bool:
        if self.q and not self.moved and not _same_page(self.q):
            print("[page] question no longer on screen — reply NOT inked, kept "
                  "in the log (content safety)", flush=True)
            self.blocked = self.stopped = True
            return False
        if self.greet:
            fade_greeting(self.greet, self.host)
            self.greet = None
        _set_stage("inking")
        self.ctx = _on_monke(self.host)
        self.ctx.__enter__()
        self.w0 = qml_eval(self.host, "documentViewTools.activePen.thickness") if SAFE_LAYER else None
        self.started = True
        print("[stream] first part inking while the reply is still written", flush=True)
        return True

    def _pen(self, thin: bool) -> None:
        if SAFE_LAYER and thin != self.thin:
            w = PEN_THIN if thin else (self.w0 or "").strip()
            if str(w).isdigit():
                qml_eval(self.host, f"documentViewTools.activePen.thickness={w}")
            self.thin = thin

    def _unit(self, u: str) -> None:
        u = harness.to_ascii(harness.clean_reply(u)).strip()
        if "$$" in u and self.fix:
            u = harness.repair_latex(u, self.fix)
        if u:
            self._put(lambda y, log: _layout(u, self.style, y, log=log))

    def _block(self, b) -> None:
        """One plan Block: rendered ONCE at y 0, then placed like a text
        unit. No model repair: a block that fails is skipped and logged
        (never inked as source). A plots graph joins ses.graphs once inked."""
        base, g = _block_layout(b, self.style, 0, log=self.log)
        if not base:
            return
        put = self._put(lambda y, log: _at(base, y))
        if put and g is not None and self.ses is not None:
            g.x += min(px for s in put for px, _ in s) - min(px for s in base for px, _ in s)
            g.y += min(py for s in put for _, py in s) - min(py for s in base for _, py in s)
            self.ses.graph = _graphs(self.ses)[g.label or b.label] = g
            print(f"[plot] {g.label or b.label} from the plan ({len(g.curves)} curves)",
                  flush=True)

    def _put(self, lay) -> list | None:
        """Place + ink one unit. lay(y, log) -> its strokes with top at y
        (called again when the spot moves). Returns the inked strokes."""
        if self.stopped:
            return None
        quiet = lambda *a: None                               # noqa: E731
        todo = self.anchors.pending()
        resuming = bool(todo and isinstance(todo[0][1].tag, dict)
                        and "remaining" in todo[0][1].tag)
        if resuming:
            i, u = todo[0]
            strokes = self._from_page(u.tag["remaining"])
        else:
            strokes = lay(self.y, self.log)
        if not strokes:
            return None
        if not resuming and self.column_x is not None:
            import place
            strokes = place.shift(strokes, self.column_x,
                                  min(py for s in strokes for _, py in s))
        elif not resuming and self.directive and self.occ is not None:
            moved, spot = _directive_place(strokes, self.directive, self.regions,
                                           self.occ, self.floor, self.bottom)
            if moved is not None and spot is not None:
                strokes, self.column_x = moved, spot.x
                print(f"[place] {spot.why} scale={spot.k:g}", flush=True)
            else:
                print("[place] directive had no safe spot — normal placement", flush=True)
            self.directive = None
        beside = None if resuming else _beside(self.row, strokes)
        if beside:
            strokes = beside
        shot = _shot()                    # ~0.5 s per unit: where the view is, what is in the way
        ink = []
        if shot is not None:
            ox, oy, y = self.vt.ox, self.vt.oy, self.y
            if not self._follow(shot):
                return None
            if (self.vt.ox, self.vt.oy) != (ox, oy):
                # sim 2026-10-04: a scroll between parts put the next part at
                # the old screen spot; same page spot, new screen spot now
                strokes = self._view_delta(strokes, ox, oy)
            elif self.y != y:             # re-anchored under the ink now on screen
                strokes, beside = lay(self.y, quiet), None
                if not strokes:
                    return None
            ink = _navy_ink(shot, self.vt, self.inked)
            v = shot.info.get("view")     # read by the page-file overlay just now
            if v and v.get("pageId") != self.anchors.page_id:
                self._stop_page()
                return None
        if not resuming:
            i = len(self.anchors.units)
            self.anchors.units += rebase.Plan.from_screen(self.anchors.page_id, [_box(strokes)],
                                                          self.vt).units
        strokes, moved = self._rebase(strokes, ink)
        if moved:
            beside = None
            print(f"[stream] part moved to y {_box(strokes)[1]} — new ink in the way, "
                  "or scrolled past its spot", flush=True)
        c = rebase.continuation(self.anchors, self.vt, PAGE_HEIGHT or float("inf"),
                                CANVAS_H - self.bottom, screen_top=REPLY_TOP)
        if c.decision == "wait_for_scroll":
            # injected swipes/drags do NOT scroll xochitl (run 13) — Navy scrolls
            strokes, beside = self._await_scroll(strokes), None
            c = rebase.continuation(self.anchors, self.vt, PAGE_HEIGHT or float("inf"),
                                    CANVAS_H - self.bottom, screen_top=REPLY_TOP)
        if self.stopped:
            return None
        if c.decision != "ink_now" or not strokes:
            print(f"[stream] no room for the next part ({c.reason or 'no scroll came'}) "
                  "— rest in the log", flush=True)
            if not resuming or c.decision != "wait_for_scroll":
                self.stopped = True
            return None
        if not self.started and not self._start():
            return None
        pressure = self.style.get("pressure", 2400)
        grouped = {}
        for s in strokes:
            grouped.setdefault((isinstance(s, Drawn), getattr(s, "color", MONKE_COLOR)), []).append(s)
        batches = [(thin, c, grouped[thin, c]) for thin in (False, True)
                   for c in sorted(c for t, c in grouped if t == thin)]
        kept, dropped = [], []
        for k, (thin, c, batch) in enumerate(batches):
            self._pen(thin)
            if c != MONKE_COLOR and SAFE_LAYER:
                pen_color(self.host, c)
            if k:                         # sim page_fills_up: a scroll between the frame and
                shot = _shot()            # the curve inked the curve 500 px under its frame
                oy, ox = self.vt.oy, self.vt.ox
                if shot is not None and not self._follow(shot):
                    return None
                if (self.vt.oy, self.vt.ox) != (oy, ox):
                    batches = [(t, colour, self._view_delta(ss, ox, oy))
                               for t, colour, ss in batches]
                    batch = batches[k][2]
                    kept = self._view_delta(kept, ox, oy)
            visible = [s for s in batch if all(0 <= y < CANVAS_H for _, y in s)]
            kept += visible
            dropped += [s for s in batch if s not in visible]
            if visible:
                lamp(to_lamp(visible, pressure, self.step), self.host)
                _echo_mark(visible)       # its pen echo arrives at these screen spots
            if c != MONKE_COLOR and SAFE_LAYER:
                pen_color(self.host, MONKE_COLOR)
        strokes = [s for _, _, batch in batches for s in batch]
        if dropped:                        # a scroll between batches pushed them off the screen
            print(f"[stream] {len(dropped)} strokes off the screen after a scroll — "
                  f"not inked, part {i + 1} only partly inked", flush=True)
            # Retry only page-anchored missing strokes: already-lamped strokes
            # remain echo-tracked and must never be inked twice.
            remaining = self._to_page(dropped)
            u = self.anchors.units[i]
            u.x, u.y, u.w, u.h = _box(remaining)
            u.tag = {"remaining": remaining}
            self.partial = True
        else:
            self.anchors.mark_done(i)
            self.partial = False
        self.inked += kept
        low = max(py for s in strokes for _, py in s) + 40
        drawing = all(isinstance(s, Drawn) for s in strokes)
        self.row = (None if beside or not drawing else
                    (max(px for s in strokes for px, _ in s), min(py for s in strokes for _, py in s)))
        self.y = max(self.y, low) if beside else low
        return None if dropped else strokes

    def _view_delta(self, strokes, ox, oy):
        """Move screen strokes by this transform's page-offset change."""
        return _at(strokes, round((self.vt.oy - oy) * self.vt.scale),
                   round((self.vt.ox - ox) * self.vt.scale))

    def _to_page(self, strokes):
        return [type(s)([self.vt.screen_to_page(x, y) for x, y in s], getattr(s, "color", 0))
                if isinstance(s, (Drawn, Tinted)) else
                [self.vt.screen_to_page(x, y) for x, y in s] for s in strokes]

    def _from_page(self, strokes):
        return [type(s)(self.vt.stroke_to_screen(s), getattr(s, "color", 0))
                if isinstance(s, (Drawn, Tinted)) else self.vt.stroke_to_screen(s) for s in strokes]

    def _rebase(self, strokes: list, ink: list) -> tuple[list, bool]:
        """The pending part at its spot in the view now: rebase.rebase pulls
        it into view when Navy scrolled past it and puts it under his new
        ink. (strokes moved there, moved?)"""
        r = rebase.rebase(self.anchors, self.vt, self.vt, ink, bottom=self.bottom, top=REPLY_TOP)
        if r.abort or not r.units:
            return strokes, False
        self.anchors = r.plan
        x, y = r.units[0].screen[:2]
        bx, by = _box(strokes)[:2]
        return (_at(strokes, y - by, x - bx), True) if (x, y) != (bx, by) else (strokes, False)

    def _follow(self, shot) -> bool:
        """Track the view under the reply: where did the ink Monke expects on
        screen (the reply so far, else the question) go? A scroll moves vt
        and every screen spot kept here; offset unknown (a long scroll, too
        little ink left on screen) -> the view fields, else the reply goes
        on under the ink now on screen. Another page -> stop (never ink onto
        another page). False = stopped."""
        import vectors
        ref = self.inked or self.q
        if not ref:
            return True
        from screen import UI_BOXES
        pts, mask = [p for s in ref for p in s], _seen(shot)
        seen = rebase.detect_view_moved(pts, mask, blind=UI_BOXES)
        if not seen[0]:
            return True
        v = vectors.view(self.host)
        if rebase.page_turn(self.anchors, v).abort:
            return self._stop_page()
        d = rebase.view_shift(seen, self.v, v, pts, mask, blind=UI_BOXES)
        self.moved, self.v = True, v          # (each part's pen echo was marked at its old spot)
        if d is None:
            # offset unknown: x no longer maps (no page-file ink), and the old
            # screen spots are stale (every _await_scroll poll re-anchored again)
            self.vt = replace(self.vt, x_trusted=False)
            self.inked, self.q, self.row, self.y = [], [], None, ink_floor(shot) + 50
            todo = self.anchors.pending()
            if todo:                           # the waiting part goes there too
                dy = self.vt.screen_to_page(0, self.y)[1] - todo[0][1].y
                for _, u in todo:
                    u.y += dy
            print("[stream] Navy scrolled — the reply goes on under the ink now on screen",
                  flush=True)
            return True
        dy, dx = d
        self.vt = replace(self.vt, ox=self.vt.ox + dx / self.vt.scale,
                          oy=self.vt.oy + dy / self.vt.scale)
        self.inked, self.q = _at(self.inked, dy, dx), _at(self.q or [], dy, dx)
        self.y += dy
        if self.row:
            self.row = (self.row[0] + dx, self.row[1] + dy)
        print(f"[stream] Navy scrolled {-dy:+d} px — the reply goes on at its page spot", flush=True)
        return True

    def _stop_page(self) -> bool:
        print("[stream] the page changed during the reply — rest in the log "
              "(content safety)", flush=True)
        self.stopped = True
        self.blocked = not self.started
        return False

    def _await_scroll(self, strokes: list) -> list | None:
        """Past the screen bottom: give Navy SCROLL_WAIT s to scroll (roadmap:
        scroll to complete a reply; run 12 'no room' with the page below
        empty). The part keeps its page spot and inks once the view has
        settled with it on screen. None = no scroll came, or still no room."""
        if not SCROLL_WAIT:
            return None
        print(f"[stream] page bottom — scroll down and the reply goes on (waiting {SCROLL_WAIT} s)",
              flush=True)
        end, last, still = time.time() + SCROLL_WAIT, None, 0
        while time.time() < end:
            time.sleep(1.0)
            shot = _shot()
            if shot is None:
                continue
            ox, oy = self.vt.ox, self.vt.oy
            if not self._follow(shot):
                return None
            strokes = self._view_delta(strokes, ox, oy)
            here = (self.vt.ox, self.vt.oy, self.y)
            # sim page_fills_up: a hand scrolls in swipes ~1.5 s apart; inking
            # after 1 s still split a graph (frame before, curve after swipe 2)
            still = still + 1 if here == last else 0
            if still < 2:                      # still scrolling: wait for the view to settle
                last = here
                continue
            strokes, _ = self._rebase(strokes, _navy_ink(shot, self.vt, self.inked))
            c = rebase.continuation(self.anchors, self.vt, PAGE_HEIGHT or float("inf"),
                                    CANVAS_H - self.bottom, screen_top=REPLY_TOP)
            if c.decision == "ink_now":
                return strokes
            if c.decision != "wait_for_scroll":
                return None
        return None

    def finish(self, text: str) -> int:
        """Flush the tail, verify once, restore Navy's pen. Returns the floor."""
        try:
            if not self.stopped and self.plan:
                for p in self.scan.close():
                    print(f"[plan] {p}", flush=True)
                if not self.scan.saw_blocks:     # JSON but no plan: render_plan reads it
                    for b in render_plan.normalize(text, self.taken0):
                        self._block(b)
                        if self.stopped:
                            break
            elif not self.stopped:
                self.feed(text + "\n")
            if self.started and VERIFY:
                pressure = self.style.get("pressure", 2400)
                time.sleep(1.0)                  # one e-ink wait + one frame
                shot = _shot()
                if shot is not None and not self._follow(shot):
                    shot = None                  # another page: nothing to check here
                for thin in (True, False):       # current pen first: fewer switches
                    part = [s for s in self.inked if isinstance(s, Drawn) == thin]
                    if part:
                        self._pen(thin)
                        _verify_ink(part, self.host, pressure, self.step, shot=shot)
        finally:
            if self.ctx is not None:
                self.ctx.__exit__(None, None, None)
        return self.y - 40 if self.inked else self.y


def _erase_cmds(strokes: list) -> str:
    cmds = []
    for s in strokes:
        if not s:
            continue
        # bends only: each lamp 'erase move' costs ~44 ms whatever its length
        # (only pen moves are distance-scaled) -> 1748 points took 77 s
        pts = decimate(s, 10**9) if len(s) > 1 else [s[0], (s[0][0] + 2, s[0][1] + 2)]
        cmds.append(f"erase down {pts[0][0]} {pts[0][1]}")
        cmds += [f"erase move {x} {y}" for x, y in pts[1:]]
        cmds.append("erase up")
    return "\n".join(cmds) + "\n" if cmds else ""


def fade_greeting(greet: list, host: str) -> None:
    """The greeting lives on its own layer: dropping it is exact and ~2 s.
    Without the eye patch (no layers) fall back to tracing its strokes."""
    import layers
    _echo_mark(greet, clear=True)
    if not SAFE_LAYER:
        return erase_strokes(greet, host)
    if not layers.drop(host, layers.GREET):
        print("[greet] no greeting layer on this page — nothing to fade", flush=True)


def erase_strokes(strokes: list, host: str) -> None:
    """Erase EXACTLY these strokes: the eraser traces every point of each one
    (on the Monke layer), then a screenshot check erases whatever survived
    (sampling every 3rd point left dots/curls of a greeting behind). A
    bounding-box sweep also wiped other Monke ink inside the box — it cut a
    fresh graph in half (device, 2026-09-26)."""
    import numpy as np
    cmds = _erase_cmds(strokes)
    if not cmds:
        return
    _echo_mark(strokes, clear=True)
    try:
        with _on_monke(host):
            # thick eraser for Monke's own erase passes (medium left curls
            # of 4 px cursive behind); layer-scoped, so Navy's ink is safe.
            # Navy's eraser width is restored below.
            was = qml_eval(host, "var e=documentViewTools.eraserPen, t=e.thickness;"
                                 " e.thickness=3; t") if SAFE_LAYER else None
            lamp(cmds, host)
            for attempt in (1, 2):             # recheck: curls/ends survive
                time.sleep(1.0)
                shot = _shot()
                if shot is None:
                    break
                dark = np.asarray(shot.convert("L").resize((CANVAS_W, CANVAS_H))) < 160
                # any stroke with >= 3 points still dark (fragments count too;
                # a 30% threshold left small curls on the page)
                left = [s for s in strokes
                        if sum(1 for x, y in s if 0 <= x < CANVAS_W and 0 <= y < CANVAS_H
                               and dark[y, x]) >= 3]
                if not left:
                    break
                print(f"[erase] pass {attempt}: {len(left)}/{len(strokes)} strokes left — "
                      "erasing again", flush=True)
                lamp(_erase_cmds(left), host)
            if was is not None and was.strip().isdigit():
                qml_eval(host, f"documentViewTools.eraserPen.thickness={was.strip()}")
    except RuntimeError as e:
        print(f"[erase] {e} — NOT erasing (content safety)", flush=True)


def _missing(strokes: list, shot) -> tuple[list, int]:
    """(strokes mostly missing on screen, strokes checked). Both counts use
    the same strokes: the log once said '21/18 missing' (device 2026-09-29)."""
    import numpy as np
    dark = np.asarray(shot.convert("L").resize((CANVAS_W, CANVAS_H))) < 160
    d = dark.copy()
    for dy in (-2, 0, 2):                            # tolerate a 2 px offset
        for dx in (-2, 0, 2):
            d |= np.roll(np.roll(dark, dy, 0), dx, 1)
    missing, checked = [], 0
    for s in strokes:
        pts = [(x, y) for x, y in s[::2] if 0 <= x < CANVAS_W and 0 <= y < CANVAS_H]
        if len(pts) < 3:
            continue
        checked += 1
        if sum(d[y, x] for x, y in pts) / len(pts) < 0.6:
            missing.append(s)
    return missing, checked


def _verify_ink(strokes: list, host: str, pressure: int, step: int,
                shot=None) -> None:
    """Render check (Navy 2026-09-26: 'the model is not aware of what got
    rendered'): Smriti knows exactly what it meant to draw — screenshot and
    compare. Strokes mostly missing on screen are inked again (once), with
    the pen currently selected. shot = one frame shared by several checks.
    Deterministic, no model, ~2 s."""
    if shot is None:
        time.sleep(1.0)                              # e-ink refresh
        shot = _shot()
    if shot is None or not strokes:
        return
    missing, checked = _missing(strokes, shot)
    if missing:
        # sim 2026-10-04: a scroll during the e-ink wait = 94/94 'missing', and
        # the whole reply was inked a second time at the old screen spot
        from screen import UI_BOXES
        pts, mask = [p for s in strokes for p in s], _seen(shot)
        d = rebase.view_shift(rebase.detect_view_moved(pts, mask, blind=UI_BOXES), None, None,
                              pts, mask, blind=UI_BOXES)
        # sim page_fills_up: a scroll between colour batches of one graph left
        # the frame at the old page spot; redrawing it at the shifted spot
        # made a duplicate frame + an orphan. Any move = nothing redrawn;
        # <= VERIFY_JITTER px is detection jitter: a real miss, redrawn.
        if d is None or max(abs(d[0]), abs(d[1])) > VERIFY_JITTER:
            print(f"[verify] {len(missing)}/{checked} strokes not where they were inked — "
                  "the view moved, nothing redrawn", flush=True)
            return
    if missing:
        print(f"[verify] {len(missing)}/{checked} strokes missing on screen — redrawing",
              flush=True)
        lamp(to_lamp(missing, pressure, step), host)
    else:
        print(f"[verify] render ok ({checked} strokes on screen)", flush=True)


def erase_box(strokes: list, host: str, pad: int = 12, sweep: int = 14) -> None:
    """Wipe the bounding box of the given strokes with a serpentine eraser
    pass. sweep ≈ eraser contact width; lower it if stripes survive."""
    xs = [x for s in strokes for x, _ in s]
    ys = [y for s in strokes for _, y in s]
    if not xs:
        return
    _echo_mark(strokes, clear=True)
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
    try:
        with _on_monke(host):              # eraser is layer-scoped: only
            lamp("\n".join(cmds) + "\n", host)   # Monke's own ink can go
    except RuntimeError as e:
        print(f"[erase] {e} — NOT erasing (content safety)", flush=True)


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
    """Fast wide horizontal swipe, or a vertical drag (a scroll: run 13, the
    view moved unseen) => 'pageturn'; the handler tells turn from scroll by
    the page id.
    Session start/stop is the eye's job alone (Navy 2026-09-30: a palm near
    the bottom-right corner hit the old raw corner zone and toggled sessions)."""

    def __init__(self):
        self.tx = self.ty = 0
        self.down_at: float | None = None
        self.down_sx: float | None = None
        self.down_sy: float | None = None

    def _sy(self) -> float:
        return self.ty * CANVAS_H / TOUCH_Y_MAX

    def _sx(self) -> float:
        return self.tx * CANVAS_W / TOUCH_X_MAX

    def feed(self, ev) -> str | None:
        _, _, etype, code, value = ev
        if etype != EV_ABS:
            return None
        if code == ABS_MT_POSITION_X:
            self.tx = value
            if self.down_at is not None and self.down_sx is None:
                self.down_sx = self._sx()   # tracking id comes BEFORE the position
        elif code == ABS_MT_POSITION_Y:
            self.ty = value
            if self.down_at is not None and self.down_sy is None:
                self.down_sy = self._sy()
        elif code == ABS_MT_TRACKING_ID:
            if value >= 0:
                self.down_at, self.down_sx, self.down_sy = time.time(), None, None
                return None
            if self.down_at is None:
                return None
            held, self.down_at = time.time() - self.down_at, None
            dx = abs(self._sx() - self.down_sx) if self.down_sx is not None else 0
            dy = abs(self._sy() - self.down_sy) if self.down_sy is not None else 0
            if held < 0.6 and dx > 400:
                return "pageturn"
            if held < 4 and dy > 250 and dy > 2 * dx:     # ponytail: one finger; a
                return "pageturn"                         # 2-finger pinch/pan is not tracked
        return None


def _shot(cfg=None):
    """One healed screenshot (corrupt frames auto-restart gms), or None."""
    if cfg is None:
        cfg = _load_cfg()
    s = cfg.get("screen", {})
    url = (__import__("os").environ.get("SMRITI_SCREEN_URL")
           or s.get("url", "https://10.11.99.1:2001"))
    host = cfg.get("capture", {}).get("host", "rm2")
    img = grab(url, host, s.get("user", "admin"), s.get("password", "password"),
               s.get("template", "dots"))
    if img is not None and cfg.get("screen", {}).get("vectors", True):
        _attach_vectors(img, host)
    return img


_VEC_MASK: dict = {}         # last (layers, view) -> mask, or None when unusable


def _attach_vectors(img, host: str) -> None:
    """Give a frame the page's own strokes (.rm, per layer) as a lazy extra
    ink mask: fetched only when placement asks (screen.stroke_mask), used
    only if the mapping lands on the frame's ink (a stale .rm is skipped)."""
    import vectors

    def thunk(pix):
        v = vectors.view(host)
        layers = vectors.page(host, v) if v else None
        if not layers:
            return None
        key = (id(layers), v.get("right"), v.get("top"), v.get("scale"))
        if _VEC_MASK.get("key") != key:            # same page file + view: reuse
            ok = vectors.agree(layers, v, pix) >= 0.8
            _VEC_MASK.update(key=key, mask=vectors.mask(layers, v) if ok else None)
            if not ok:
                print("[vectors] page strokes don't match the screen (page file not "
                      "flushed yet?) — pixels only", flush=True)
        img.info["view"], img.info["layers"] = v, layers
        return _VEC_MASK["mask"]
    img.info["vectors"] = thunk


def _screen_floor(cfg=None) -> int | None:
    """Occupied-ink floor from the actual visible screen, or None if the
    goMarkableStream service is unreachable."""
    img = _shot(cfg)
    return ink_floor(img) if img is not None else None


_FLOOR_FILE = _state_dir() / "floor"
_SESSIONS = (paths.data_dir() / "sessions" if paths.overlay_active()
             else Path(__file__).resolve().parents[1] / "sessions")


def _notebook(host: str) -> tuple[str, str]:
    """(uuid8, slug) of the notebook currently open in xochitl — one ssh
    grep over the metadata store, newest lastOpened DocumentType wins.
    ('default', 'default') when the tablet is unreachable, so sessions
    still work; they just all share one bucket."""
    try:
        out = tablet.run(host, "grep -HE '\"(lastOpened|visibleName|type)\"' "
                         "/home/root/.local/share/remarkable/xochitl/*.metadata",
                         timeout=10)[1].decode(errors="replace")
    except Exception:
        out = ""
    docs: dict[str, dict] = {}
    for ln in out.splitlines():
        path, _, rest = ln.partition(":")
        m = re.search(r'"(\w+)"\s*:\s*"([^"]*)"', rest)
        if m:
            docs.setdefault(Path(path).stem, {})[m.group(1)] = m.group(2)
    best = max((d for d in docs.items()
                if d[1].get("type") == "DocumentType" and d[1].get("lastOpened")),
               key=lambda d: int(d[1]["lastOpened"]), default=None)
    if not best:
        return "default", "default"
    uid, meta = best
    slug = re.sub(r"[^a-z0-9]+", "-", meta.get("visibleName", "notebook")
                  .lower()).strip("-")[:24] or "notebook"
    return uid[:8], slug


class NotebookSession:
    """Per-notebook conversation state under sessions/<slug>-<uuid8>/:
    state.json (claude session id, floor), end.png (page as Smriti last saw
    it — the B-branch baseline), transcript.md + blocks/ (KB/Elxanima
    export: raw tex/circuitikz/svg exactly as the model emitted them)."""

    def __init__(self, host: str):
        uid, slug = _notebook(host)
        self.name = f"{slug}-{uid}"
        self.dir = _SESSIONS / self.name
        (self.dir / "blocks").mkdir(parents=True, exist_ok=True)
        try:
            st = json.loads((self.dir / "state.json").read_text())
        except (OSError, ValueError):
            st = {}
        self.sid: str = st.get("claude_session_id", "")
        self.floor: int = st.get("floor", 0)
        self._nblock = st.get("nblock", 0)
        self.pages: dict = st.get("pages", {})     # "47" -> Navy's questions there

    def save(self) -> None:
        (self.dir / "state.json").write_text(json.dumps(
            {"claude_session_id": self.sid, "floor": self.floor,
             "nblock": self._nblock, "pages": self.pages,
             "updated": time.strftime("%Y-%m-%d %H:%M:%S")}))

    def save_end(self, shot) -> None:
        if shot is not None:
            shot.save(self.dir / "end.png")

    def end_shot(self):
        try:
            from PIL import Image
            return Image.open(self.dir / "end.png").convert("L")
        except OSError:
            return None

    def log_turn(self, who: str, text: str) -> None:
        v = _TURN.get("view") or {}
        pg = int(v["page"]) + 1 if "page" in v else None
        if pg and who == "Navy":                   # notebook map for notebook_context
            self.pages.setdefault(str(pg), []).append(" ".join(text.split())[:60])
            self.save()
        f = self.dir / "transcript.md"
        if not f.exists():
            f.write_text(f"---\nnotebook: {self.name}\n"
                         f"created: {time.strftime('%Y-%m-%d %H:%M')}\n---\n",
                         encoding="utf-8")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"\n## {who} ({'p%d, ' % pg if pg else ''}{time.strftime('%H:%M')})\n"
                     f"{text.strip()}\n")

    def save_train(self, focus, model_reading: dict, final: dict | None,
                   profile: str, model: str, overview=None, request: dict | None = None,
                   action: str | None = None) -> None:
        """Personal-model dataset (Navy 2026-09-26): every confirm-window
        answer is a labelled example — the focus crop Smriti read, what the
        model said, and what Navy confirmed/edited (or discarded). Saved as
        sessions/<nb>/train/<ts>.png + .json; nothing leaves the laptop."""
        d = self.dir / "train"
        d.mkdir(exist_ok=True)
        stem = time.strftime("%Y%m%d-%H%M%S")
        try:
            focus.save(d / f"{stem}.png")
            images = [f"{stem}.png"]
            if overview is not None and (request or {}).get("n_images", 1) > 1:
                overview.save(d / f"{stem}-overview.png")
                images.append(f"{stem}-overview.png")
            label = ("discarded" if final is None else
                     "edited" if harness.describe(final) != harness.describe(model_reading)
                     else "confirmed")
            # the EXACT inference request (system + user text + images, in
            # order) so a training row equals what the model saw
            (d / f"{stem}.json").write_text(json.dumps({
                "images": images, "label": label, "profile": profile,
                "action": action, "model": model, "request": request or {},
                "model_reading": model_reading, "target": final}, indent=1),
                encoding="utf-8")
            print(f"[train] example saved ({label}) -> {self.name}/train/{stem}", flush=True)
        except OSError as e:
            print(f"[train] not saved ({e})", flush=True)

    def log_block(self, kind: str, code: str) -> None:
        self._nblock += 1
        ext = "svg" if kind == "svg" else "tex"
        stem = {"svg": "sketch", "tex": "eq"}.get(kind, kind)
        if "circuitikz" in code:
            stem = "circuit"
        name = f"{stem}-{self._nblock:03d}.{ext}"
        (self.dir / "blocks" / name).write_text(code.strip() + "\n",
                                                encoding="utf-8")
        self.log_turn("block", f"[[blocks/{name}]]")


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


def _auto_focus_box() -> tuple[int, int, int, int] | None:
    """Locate the native selection marquee's solid grey fill in a screenshot."""
    shot = _shot()
    if shot is None:
        return None
    a = np.asarray(shot.convert("RGB"))
    grey = (np.ptp(a, axis=2) <= 5) & (a[..., 0] >= 175) & (a[..., 0] <= 215)
    ys = np.flatnonzero(grey.sum(axis=1) >= 30)
    if len(ys) < 8:
        return None
    y0, y1 = int(ys[0]), int(ys[-1] + 1)
    xs = np.flatnonzero(grey[y0:y1].sum(axis=0) >= max(5, (y1 - y0) // 2))
    if len(xs) < 8:
        return None
    return int(xs[0]), y0, int(xs[-1] + 1), y1


def _restore_focus_tool(host: str) -> None:
    """A lasso action must not turn injected reply strokes into a new lasso."""
    tool = qml_eval(host, "String(documentViewTools.activeTool)")
    if tool == "selection":
        qml_eval(host, "documentViewTools.activeTool = 'primary'; 'primary'")
        print("[focus] tool restored", flush=True)


def _control_server(ctl: queue.Queue, state: dict, port: int = 7333,
                    token: str = "") -> None:
    """Tiny HTTP control plane: GET /start, /stop, /status. Lets the tablet
    (smriti-eye CLI) or any curl drive sessions without touch gestures.
    token (SMRITI_CONTROL_TOKEN env): when set, requests must carry it as
    ?token=... or X-Smriti-Token header — a /start now spends AI quota, so
    the port is no longer a free-for-all. Unset = open (trusted network)."""
    import http.server
    import threading
    import urllib.parse

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            p, _, q = self.path.strip("/").partition("?")
            if token:
                sent = (urllib.parse.parse_qs(q).get("token", [""])[0]
                        or self.headers.get("X-Smriti-Token", ""))
                if sent != token:
                    self.send_error(403)
                    return
            if p in ("start", "stop"):
                ctl.put(p)
                body = f"{p} queued\n"
            elif p == "focus":
                # selection menu: /focus?action=ask&box=x0,y0,x1,y1&space=page|scene
                qs = urllib.parse.parse_qs(q)
                act = qs.get("action", [""])[0]
                raw_box = qs.get("box", [""])[0]
                space = qs.get("space", ["page"])[0]
                try:
                    box = _auto_focus_box() if raw_box == "auto" else tuple(
                        int(float(v)) for v in raw_box.split(","))
                    assert box is not None and len(box) == 4 and box[2] > box[0] and box[3] > box[1]
                    assert act in harness.ACTIONS and space in ("page", "scene")
                    if space == "scene":
                        v = _TURN.get("view") or {}
                        assert "ox" in v and "oy" in v
                        box = (box[0] + int(v["ox"]), box[1] + int(v["oy"]),
                               box[2] + int(v["ox"]), box[3] + int(v["oy"]))
                except (ValueError, AssertionError):
                    self.send_error(400, "need action=" +
                                    "|".join(harness.ACTIONS) + "&box=x0,y0,x1,y1|auto&space=page|scene")
                    return
                ctl.put(("focus", act, box))
                body = f"focus {act} queued\n"
            elif p == "status":
                turn = state.get("turn")
                body = (turn.status() if turn is not None else "idle") + "\n"
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

    http.server.ThreadingHTTPServer.allow_reuse_address = True  # survive fast restarts
    try:
        class Srv(http.server.ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                # the tablet's bash /dev/tcp status probe may hang up early —
                # a reset is noise, not an error worth a traceback
                import sys
                if not isinstance(sys.exc_info()[1], (ConnectionResetError,
                                                      BrokenPipeError)):
                    super().handle_error(request, client_address)
        srv = Srv(("", port), H)
    except OSError as e:
        print(f"[control] port {port} unavailable ({e}) — HTTP control off",
              flush=True)
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[control] http://<this-host>:{port}/start|stop|status", flush=True)


def _reverse_tunnel(host: str, port: int, on_reconnect=None) -> None:
    """Keep `ssh -R 127.0.0.1:port:127.0.0.1:port` open to the tablet so the
    eye can always call this daemon at its own 127.0.0.1. The rM2 runs
    tailscaled with --tun userspace-networking: no tailscale0, so tablet
    programs cannot open sockets to 100.x peers (2026-10-11: every eye tap
    went nowhere). The tunnel rides the same SSH link capture already uses,
    so it works over tailnet, Wi-Fi or hotspot alike. Reconnects forever;
    dies with the daemon. on_reconnect() runs after every re-open: a tablet
    reboot drops the xovi eye patch (tmpfs) — reload it and re-point."""
    import atexit
    import threading
    from deps import ssh_argv
    argv = [*ssh_argv(host, "-N", "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
                      "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3",
                      "-R", f"127.0.0.1:{port}:127.0.0.1:{port}")]
    cur: list = []
    atexit.register(lambda: cur and cur[0].poll() is None and cur[0].kill())

    def loop():
        first = True
        while True:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE)
            cur[:] = [p]
            if not first and on_reconnect:
                time.sleep(2)
                if p.poll() is None:
                    try:
                        on_reconnect()
                    except Exception as e:                  # noqa: BLE001
                        print(f"[tunnel] re-setup failed: {e}", flush=True)
            first = False
            err = p.communicate()[1].decode(errors="replace").strip().splitlines()[-1:] or [""]
            print(f"[tunnel] eye tunnel closed (rc={p.returncode}) {err[0][:160]} — reconnecting",
                  flush=True)
            time.sleep(5)
    threading.Thread(target=loop, daemon=True, name="eye-tunnel").start()
    print(f"[tunnel] eye -> tablet 127.0.0.1:{port} -> this daemon", flush=True)


def _point_tablet(host: str, port: int, token: str, self_ip: str = "") -> None:
    """Tell the tablet where THIS daemon is: its smriti-eye config gets the
    address the tablet sees us from ($SSH_CLIENT) — USB DHCP hands out
    10.11.99.x addresses that vary, so a hard-coded IP goes stale. Skipped
    when the tablet reaches us over the tailnet (userspace tailscale: it sees
    a proxy address) — then self_ip (this laptop's own tailnet IP) is written
    instead. 2026-10-11: without it the eye kept the stale USB 10.11.99.6 and
    every tap went nowhere."""
    tok = f"SMRITI_TOKEN={token}\n" if token else ""
    pick = (f"ip={self_ip}; " if self_ip else
            "ip=${SSH_CLIENT%% *}; ip=${ip#::ffff:}; case $ip in 100.*|127.*|'') exit 0;; esac; ")
    cmd = (pick +
           "mkdir -p /home/root/.config && printf 'SMRITI_DAEMON=%s:" + str(port)
           + "\\n" + tok.replace("\n", "\\n") + "' $ip > /home/root/.config/smriti-eye"
           " && echo $ip")
    try:
        ip = tablet.run(host, cmd, timeout=10)[1].decode().strip()
        if ip:
            print(f"[control] tablet eye -> {ip}:{port}", flush=True)
    except Exception:
        pass


def ensure_xovi(host: str) -> bool:
    """Eye patch loaded in the running xochitl, else run xovi/start (its
    systemd drop-ins live on tmpfs: every reboot drops them). That restarts
    xochitl, so the notebook must be reopened. False = link down/failed."""
    try:
        if tablet.run(host, "tr '\\0' '\\n' < /proc/$(pidof xochitl)/environ "
                            "| grep -q xovi.so", timeout=8)[0] == 0:
            return True
        print("[tablet] eye patch not loaded (reboot?) — running xovi/start; "
              "reopen the notebook when xochitl is back", flush=True)
        return tablet.run(host, "/home/root/xovi/start >/dev/null 2>&1", timeout=90)[0] == 0
    except OSError:
        return False


def _detach_stdin() -> None:
    """Point this process's stdin at /dev/null so every ssh it spawns does
    too (what `ssh -n` does). Started from a PowerShell console, the pen and
    touch ssh streams inherited the console as stdin and every later
    one-shot ssh hung to its timeout: settings 'unreachable', confirm stuck
    on 'waiting for Navy' (device 2026-09-30). Ctrl-C is a console signal,
    not stdin, so it still stops the daemon."""
    import os
    fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(fd, 0)
    os.close(fd)
    if os.name == "nt":
        # dup2 closed the old fd 0 — and with it the OS handle Windows still
        # reports as STD_INPUT_HANDLE. subprocess (stdin=None) inherits that
        # handle: WinError 6 in the frozen exe (2026-10-11). Re-register.
        import ctypes
        import msvcrt
        ctypes.windll.kernel32.SetStdHandle(-10, msvcrt.get_osfhandle(0))


def link_test() -> bool:
    """Read-only report for every configured route (USB dropped when installed)."""
    import link
    try:
        cfg = _load_cfg()
    except (OSError, ValueError) as e:
        print(f"FAIL config: {e}")
        return False
    rows = link.report(cfg)
    for endpoint, ok in rows:
        print(f"{'PASS' if ok else 'FAIL'} {endpoint.name}: {endpoint.host} -> {endpoint.screen_url}")
    import deps
    if not rows:
        print("no endpoints: smriti link set-tailnet <ip> / smriti link set-wifi <ip>")
    elif not all(ok for _, ok in rows) and deps.smriti_ssh() and not deps.key_path().exists():
        print(f"hint: no Smriti SSH key yet ({deps.key_path()}): run  smriti pair <tablet-ip>")
    return any(ok for _, ok in rows)


def _resolve_link(cfg: dict, wait: float = 0) -> str:
    """Choose one configured rM2 path and bind this daemon process to it.
    wait > 0: keep retrying that many seconds (laptop and tablet boot
    together; the tablet's tailnet comes up ~1 min after power-on)."""
    import link
    deadline = time.time() + wait
    told = False
    tries = 0
    while (endpoint := link.resolve(cfg)) is None:
        names = ", ".join(ep.name for ep in link.endpoints(cfg)) or "none"
        if time.time() >= deadline:
            raise RuntimeError(f"no rM2 link reachable (tried: {names})")
        if not told:
            print(f"[link] waiting for the tablet ({names}) — wake it / check Wi-Fi", flush=True)
            told = True
        if tries % 6 == 0 and cfg.get("capture", {}).get("scan", True):   # ~1/min
            try:
                import paths
                import scan
                ip = scan.find_tablet()
                if ip and cfg["capture"].get("wifi_ip") != ip:
                    print(f"[link] found the tablet on local Wi-Fi: {ip}", flush=True)
                    cfg.setdefault("capture", {})["wifi_ip"] = ip
                    if paths.overlay_active():
                        paths.set_user("capture", "wifi_ip", ip)
                    continue
            except Exception as e:                       # noqa: BLE001 — scan is best effort
                print(f"[link] local scan failed: {e}", flush=True)
        tries += 1
        time.sleep(10)
    print(f"[link] {endpoint.name}: {endpoint.host} -> {endpoint.screen_url}", flush=True)
    return endpoint.host


def run() -> None:
    global _TURN_STATE, _STATUS_HOST
    selection.reset_cache()                     # probe results last one daemon session
    _TURN.clear()
    _TURN.update(view=None)
    _PAGE.clear()
    _PAGE.update(id=None)
    _detach_stdin()
    cfg = _load_cfg()
    host = _resolve_link(cfg, wait=float(cfg.get("capture", {}).get("link_wait_seconds", 24 * 3600)))
    _STATUS_HOST = host
    idle = cfg["capture"].get("idle_seconds", 2.8)
    settle = SettleGate(idle, cfg["capture"].get("settle_seconds", 3.0))
    _TURN_STATE = TurnState(_state_dir() / "turn-state")
    _set_stage("idle")
    m = cfg.get("monke", {})
    style = dict(load_styles()[m.get("reply_style", "cursive")])
    if "reply_size" in m:                       # font sizing override
        style["size"] = m["reply_size"]
    if "reply_line_height" in m:                # line spacing override
        style["line_height"] = m["reply_line_height"]
    step = m.get("waypoint_step", 0)
    bottom = m.get("page_bottom", 1780)
    max_turns = m.get("history_turns", 6)
    fade = m.get("fade", False)
    fade_hold = m.get("fade_hold", 10)
    greet_fade = m.get("greet_fade", True)   # erase greeting once answered
    confirm = m.get("confirm", False)        # eye popup confirm/discard for
                                             # drawings + selections
    input_fade = m.get("input_fade", False)  # also erase the user's input at
                                             # reply time — frees space, reply
                                             # lands where the question was
    global MARKER_INK, SAFE_LAYER, VERIFY, STREAM, SCROLL_WAIT, PAGE_HEIGHT
    SCROLL_WAIT = m.get("scroll_wait", SCROLL_WAIT)
    PAGE_HEIGHT = m.get("page_height", PAGE_HEIGHT)
    MARKER_INK = m.get("marker_ink", False)
    SAFE_LAYER = m.get("safe_layer", True)
    VERIFY = m.get("verify", True)
    STREAM = m.get("stream", True)
    if SAFE_LAYER and input_fade:
        # erasing Navy's question would mean erasing on HIS layer — the
        # Monke-layer eraser can't reach it, and the reply would then land
        # on top of his still-visible ink. Content safety wins.
        print("[monke] input_fade off: safe_layer never touches your ink",
              flush=True)
        input_fade = False
    cfg_ai = brain.load_config()
    ses: NotebookSession | None = None    # created per session (per notebook)

    # pen stream (event1) is opened only while a session is ON: a hovering
    # pen floods evdev even when idle, and that all rides the ssh link.
    pen: Capture | None = None
    touch = Capture(host, "/dev/input/event2")
    sb = StrokeBuilder()
    tg = TouchGesture()
    ctl: queue.Queue[str] = queue.Queue()
    state = {"watching": False, "turn": _TURN_STATE}
    port = cfg.get("control", {}).get("port", 7333)
    token = __import__("os").environ.get("SMRITI_CONTROL_TOKEN", "")
    _control_server(ctl, state, port, token)
    if not _have_tex():
        # fresh machine 2026-10-04: every $$ block failed and was skipped in
        # silence — Navy saw prose only, with no hint why
        print("[tex] pdflatex NOT found — maths, graphs and circuits will NOT ink. "
              "Install MiKTeX (miktex.org), or set SMRITI_PDFLATEX.", flush=True)
    ensure_xovi(host)                       # no manual xovi/start after a reboot
    CLAUDE_CFG.update(cfg.get("claude", {}))
    wireless = not host.split("@")[-1].startswith("10.11.99.") and host != "rm2"
    if wireless:                            # USB keeps the direct $SSH_CLIENT address
        _reverse_tunnel(host, port, on_reconnect=lambda: (
            ensure_xovi(host), _point_tablet(host, port, token, "127.0.0.1"),
            print("[tunnel] tablet back: eye patch + pointer refreshed", flush=True)))
    _point_tablet(host, port, token, "127.0.0.1" if wireless else "")
    publish_models(host, cfg_ai)            # the app's model picker
    last_models = time.time()
    publish_usage(host)                     # the app's Claude counter
    history: list[dict] = []
    strokes: list[list[tuple[int, int]]] = []
    greet_ink: list = []      # greeting strokes, erased at first reply/stop
    baseline = None           # screenshot taken after Smriti's last write —
                              # commits with no visual change vs this are
                              # our own echo, never user input
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
                        # stream dropped (tablet slept / ssh reaped) — a
                        # drop must NOT kill the daemon; reconnect and go on
                        print("pen stream ended — reconnecting", flush=True)
                        pen.close()
                        time.sleep(2)        # backoff if tablet is offline
                        pen, sb = Capture(host), StrokeBuilder()
                        strokes = []
                        settle.reset()
                        break
                    last_pen = time.time()
                    settle.note_event(last_pen)
                    if not paused and (s := sb.feed(ev)) is not None:
                        strokes.append(s)
            cmds = _pull(ctl)         # smriti-eye / curl commands
            touch_evs = _pull(touch.q)
            if touch_evs and touch_evs[-1] is None:
                print("touch stream ended — reconnecting", flush=True)
                touch.close()
                time.sleep(2)                # backoff if tablet is offline
                touch, tg = Capture(host, "/dev/input/event2"), TouchGesture()
                touch_evs = touch_evs[:-1]
            for ev in touch_evs:
                if ev is None:
                    continue
                if (g := tg.feed(ev)) is not None:
                    cmds.append(g)
            for g in cmds:
                if isinstance(g, tuple) and g[0] == "focus":
                    # lasso selection -> Monke action; works in or out of a
                    # session (selection = the primary content, no pen needed)
                    _, act, box = g
                    _restore_focus_tool(host)
                    if ses is None:
                        ses = NotebookSession(host)
                    floor = _reply([], style, step, bottom, host, history,
                                   floor, ses=ses, cfg_ai=cfg_ai,
                                   shot=_shot(cfg), focus_box=box, action=act,
                                   confirm=confirm)
                    _save_floor(floor)
                    del history[:-2 * max_turns]
                    ses.floor = floor
                    ses.save()
                    if pen is not None:
                        _drain(touch.q)
                        baseline = _shot(cfg)
                    continue
                if g in ("tap", "start") and paused:
                    ses, floor, greet_ink, pen, sb, baseline = _start_session(
                        host, cfg, style, step, bottom, history, cfg_ai, touch)
                    paused, strokes = False, []
                    state["watching"] = True
                    settle.reset()
                    _set_stage("looking")
                elif g in ("hold", "stop") and not paused:
                    if greet_fade and greet_ink:
                        fade_greeting(greet_ink, host)
                        greet_ink = []
                    if ses is not None:
                        # end.png AFTER the greeting fade = honest baseline
                        ses.save_end(_shot(cfg))
                        ses.floor = floor
                        ses.save()
                    paused, strokes = True, []
                    state["watching"] = False
                    settle.reset()
                    _set_stage("idle")
                    eye_state(host, "idle")
                    baseline = None
                    if pen is not None:
                        pen.close()
                        pen = None
                    print("session off", flush=True)
                    marker("pause", host)
                    _drain(touch.q)
                elif g == "pageturn" and not paused:
                    time.sleep(1.0)          # let xochitl repaint
                    # a scroll needs the same screen-space reset; only the label differs
                    turned, _PAGE["id"] = _page_turned(host, _PAGE["id"])
                    baseline = _shot(cfg)
                    floor = ink_floor(baseline) if baseline is not None else 0
                    _save_floor(floor)
                    if ses is not None:
                        ses.floor = floor
                        ses.save_end(baseline)
                        ses.save()
                    strokes = drop_echo(strokes)   # before the reset: a scroll right after a
                    echo_reset()                   # reply would turn its pending echo into input
                    print(f"{'page turned' if turned else 'scrolled (same page)'} — floor {floor}",
                          flush=True)
            if not paused and pen is not None and strokes:
                now_time = time.time()
                was_waiting = settle.deadline is not None
                if not settle.poll(now_time, sb.touching):
                    if settle.deadline is not None and not was_waiting:
                        # idle reached: spend Monke's echo NOW, before settling.
                        # Held through the settle wait, the echo merged with
                        # Navy's next prompt and the live mask ate its strokes
                        # (sim scroll_right_after_reply: 32 -> 18).
                        strokes = drop_echo(strokes)
                        if _echo_only(strokes, baseline, _shot(cfg)):
                            strokes = []
                            settle.reset()
                            _set_stage("looking")
                            continue
                        _set_stage("settling")
                    time.sleep(0.05)
                    continue
                n0 = len(strokes)
                strokes = drop_echo(strokes)
                if len(strokes) < n0:
                    print(f"[echo] dropped {n0 - len(strokes)} of {n0} strokes "
                          "(on Monke's own ink)", flush=True)
                now = _shot(cfg)
                if _echo_only(strokes, baseline, now):
                    # nothing visibly changed since Smriti last wrote —
                    # these strokes are our own echo, not user input
                    print("no page change vs baseline — echo ignored",
                          flush=True)
                    strokes = []
                    settle.reset()
                    _set_stage("looking")
                    _drain(touch.q)
                else:
                    try:
                        _TURN.update(prompt=None, inked=False, failed=set(), why="")
                        floor = _reply(strokes, style, step, bottom, host, history,
                                       floor, ses=ses, cfg_ai=cfg_ai,
                                       fade=fade, fade_hold=fade_hold,
                                       greet=greet_ink if greet_fade else None,
                                       shot=now, input_fade=input_fade,
                                       confirm=confirm)
                        # '!' stays when the turn left nothing, or less than asked, on the
                        # page (a read failure set it and this line cleared it at once)
                        eye_error(host, bool(_TURN.get("failed")), _TURN.get("why", ""))
                        _erase_answered_prompt(host)
                    except Exception as e:
                        _set_stage("error", str(e))
                        eye_error(host, True, str(e))
                        # one bad turn must never end the session/daemon
                        import traceback
                        traceback.print_exc()
                        print(f"[turn] failed ({str(e)[:120]}) — still watching",
                              flush=True)
                    greet_ink = []
                    _save_floor(floor)
                    del history[:-2 * max_turns]
                    strokes = []
                    settle.reset()
                    marker("watch", host)
                    _drain(touch.q)   # pen: our echo goes by drop_echo, never by time
                    baseline = _shot(cfg)    # page WITH our reply = baseline
                    ses.floor = floor
                    ses.save_end(baseline)   # crash-safe: B-branch baseline
                    ses.save()               # fresh after every turn
                    # echo drained + baseline taken: pen input is safe again
                    # (writing before this gets eaten as Monke's own echo)
                    print("ready", flush=True)
                    _set_stage("looking")
            if time.time() - last_models > 60:
                # Claude login state can change under a running daemon;
                # ssh only when the list changed
                last_models = time.time()
                publish_models(host, cfg_ai)
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
        _set_stage("idle")
        eye_state(host, "idle")         # daemon gone: eye must not say watching
        print("monke sleeps.", flush=True)


DRAWING_PROFILES = ("graph", "circuit", "diagram", "handwriting")


def _writer_chat(route, ses, host, role):
    """messages -> text through the app's writer (escalation wins) — LaTeX
    repair uses it too."""
    return cloud_chat(route["escalate"] or route["writer"], ses, host, role)


def _write(prompt, ses, extra, route, reading, profile, host, on_text=None) -> str:
    """Stage-2 reply through the writer picked in the app (default: the
    [brain] model). Escalation (one turn, a bigger model) wins when armed.
    Context (Navy 2026-09-26): drawings / graphs / interpretation = stateless;
    ask / infer / think = full notebook session — 'auto' decides from the
    reading. Only a Claude writer keeps a session; ChatGPT is stateless."""
    model = route["escalate"] or route["writer"]
    kind = harness.reply_kind(reading, profile)      # one kind module per turn
    system = skill.reply_system(kind) + (f"\n\nNavy's standing instructions (Smriti app):\n{extra}"
                                         if extra else "")
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    session = route["context"] == "session" or (
        route["context"] == "auto" and profile not in DRAWING_PROFILES
        and reading["kind"] in ("text", "math", "mixed")
        and reading["intent"] in ("question", "solve"))
    print(f"[brain] write with {model} ({'session' if session else 'stateless'})", flush=True)
    text = cloud_chat(model, ses, host, "write", session, on_text)(msgs)
    if route["escalate"]:                  # one-shot: disarm in the app's file
        st = tablet_settings(host)
        st["escalate_next"] = False
        try:
            tablet.run(host, f"cat > {SETTINGS}", json.dumps(st).encode(), timeout=10)
        except OSError:
            pass
    return text


def _session_context(shot, ses: NotebookSession) -> tuple[str, list[bytes]]:
    """Session-start context decision tree (grilled + approved):
      A blank page                     -> nothing extra
      B page matches last end.png     -> nothing extra (context is in the
                                          resumed claude session already)
      C unknown ink on the page       -> OCR the changed region (C1); OCR
                                          empty/down -> attach the cropped
                                          region as ONE image (C2)
    Returns (context_text, images)."""
    if shot is None:
        return "", []
    if ink_floor(shot) == 0:
        return "The page is blank.", []
    end = ses.end_shot()
    box = page_diff(end, shot) if end is not None else None
    if end is not None and box is None:
        return "", []                     # B: unchanged since last session
    region = shot.crop(box) if box else shot
    buf = io.BytesIO()
    region.save(buf, format="PNG")
    png = buf.getvalue()
    lines = scribe_ocr(png)
    if lines and any(l.strip() for l in lines):
        return ("Ink already on the page (machine-transcribed): "
                + " / ".join(lines), [])
    return ("The attached photo shows ink already on the page — context, "
            "not a question.", [png])


def _page_context(strokes, shot=None, hide=None) -> tuple[bytes, "object"]:
    """Vision input: real page screenshot with the fresh strokes overlaid in
    red — model sees full context (old replies, diagrams, documents) AND
    exactly what is new. Falls back to plain stroke render if the
    goMarkableStream service is unreachable. Returns (png_bytes, shot);
    shot is the raw RGB screenshot (or None) so callers can also run
    workarea analysis (free_bands) on it without a second grab. hide =
    Monke's greeting: blanked in the vision copy only (run 10: 'Navy back.'
    was read into Navy's question)."""
    from PIL import ImageDraw
    if shot is None:
        try:
            shot = _shot()
        except Exception:
            shot = None
    if shot is None:
        img = render_strokes(strokes, crop=True) if strokes \
            else render_strokes([], crop=False)
    else:
        img = shot.convert("RGB")
        d = ImageDraw.Draw(img)
        for s_ in hide or []:
            if len(s_) > 1:
                d.line(s_, fill=(255, 255, 255), width=14)
        for s_ in strokes:
            if len(s_) > 1:
                d.line(s_, fill=(220, 0, 0), width=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), shot


# Reply sizing: prefer the style's full size; shrink toward this floor only
# when the reply would otherwise overflow. Below ~0.6 handwriting gets hard to
# read, so we page-turn instead of shrinking further.
# Navy 2026-09-26: long replies must FIT the screen — the whole reply
# (prose AND typeset/drawn blocks) scales together, down to a legibility
# floor; below 0.6 the prose also switches to the thin pen so small
# handwriting doesn't blob.
_FIT_SCALES = (1.0, 0.85, 0.72, 0.6, 0.5, 0.42)
_MIN_SIZE = 24
_THIN_BELOW = 0.6


def _place(text: str, style, y: int, shot, bottom: int,
           log=None) -> list | None:
    """Fit the reply into real free space, scaling it down only as needed.

    Candidate regions = the space below the current ink (y..bottom) plus every
    free band the workarea parser finds on the screenshot, largest first. For
    each size (full → floor) we take the first region the reply fits in. This
    keeps replies at full size when there's room, shrinks them to fill a tight
    gap instead of dropping to a page-turn, and only returns None (page-turn)
    when even the smallest size fits nowhere on this page."""
    regions = [(y, bottom - y)]
    if shot is not None:
        for y0, y1 in free_bands(shot):
            # clip, don't drop, bands that start in the top margin — on a
            # blank-topped page the ONLY free band starts at 0 (device run
            # 2026-09-26 squeezed the greeting into the bottom edge)
            y0 = max(y0, 100)
            regions.append((y0 + 10, min(y1, bottom) - y0 - 10))
    regions = sorted((r for r in regions if r[1] > 60), key=lambda r: -r[1])
    base = int(style.get("size", 64))
    noop = lambda *a: None
    for scale in _FIT_SCALES:
        st = dict(style)
        st["size"] = max(_MIN_SIZE, round(base * scale))
        st["_scale"] = scale                      # drawings shrink with prose
        for ry, rh in regions:
            reply = _layout(text, st, ry, noop)   # probe: don't log blocks yet
            if not reply:
                continue
            h = max(py for s in reply for _, py in s) - ry
            if h <= rh:
                # winner — relayout once with the real logger so each $$/svg
                # block's source is recorded exactly once
                return _layout(text, st, ry, log)
    return None


def _greet(style, step, bottom, host, history, floor,
           ses: NotebookSession, shot, cfg_ai) -> tuple[int, list]:
    """Session-start greeting: context per the A/B/C tree (text-only when
    the page is blank/known, OCR or one cropped image when unknown ink is
    found), short Monke line inked below the existing ink. Returns
    (new floor, greeting strokes) — erased later when greet_fade is on."""
    marker("busy", host)
    ctx, images = _session_context(shot, ses)
    prompt = ("Navy just tapped the eye: a diary session starts now in his "
              f"notebook '{ses.name}'. " + (ctx + " " if ctx else "") +
              "Greet him in ONE short Monke line (under 12 words), plain "
              "words only — no maths, no $$, no drawing.")
    # the app's writer, like every turn (a Claude alias is counted + capped)
    route = apply_settings(cfg_ai, tablet_settings(host))[2]
    try:
        msgs = [{"role": "system", "content": MONKE_SYSTEM},
                image_msg(images[0], prompt) if images
                else {"role": "user", "content": prompt}]
        text = cloud_chat(route["writer"], ses, host, "greet")(msgs)
    except Exception as e:
        print(f"[greet] AI unreachable: {str(e)[:120]}", flush=True)
        eye_error(host, True, str(e))
        return floor, []
    text = harness.greeting_line(harness.to_ascii(harness.clean_reply(text)))
    if not text:
        return floor, []
    history += [{"role": "user", "content": prompt},
                {"role": "assistant", "content": text}]
    ses.log_turn("Monke", text)
    print(f"monke greets: {text}", flush=True)
    y = floor + 50 if floor else 150
    reply = _place(text, style, min(y, bottom - 100), shot, bottom,
                   log=ses.log_block)
    if not reply:
        print("no room for greeting — page full, greeting in log only", flush=True)
        return floor, []
    import layers
    _ink(reply, style.get("pressure", 2400), step, host, layer=layers.GREET)
    return max(floor, max(py for s in reply for _, py in s)), reply


def qml_eval(host: str, js: str, timeout: float = 6) -> str | None:
    """Run JS inside xochitl through the eye patch's qml-cmd channel (the
    eye's 1s timer picks it up; result lands in qml-out). None when the eye
    patch isn't loaded / no notebook is open."""
    d = "/home/root/.smriti"
    cid = int(time.time() * 1000) % 10**9
    body = json.dumps({"id": cid, "cmd": "eval", "js": js})
    try:
        _, raw = tablet.run(
            host,
            f"rm -f {d}/qml-out && cat > {d}/qml-cmd.tmp && mv {d}/qml-cmd.tmp {d}/qml-cmd;"
            # 0.1 s steps: 0.5 s cost ~0.25 s per call on every layer/pen op
            f" i=0; while [ $i -lt {int(timeout * 10)} ]; do"
            # wait for OUR id: a late result of an older (timed-out) command
            # must not end the wait as a false None
            f" [ \"$(head -n 1 {d}/qml-out 2>/dev/null)\" = \"{cid}\" ] && break;"
            f" sleep 0.1; i=$((i+1)); done;"
            # spent command leaves: the eye keeps the last id in memory only,
            # so after a xochitl restart a left-over qml-cmd would REPLAY
            # (layer switch/clear included); a timed-out one can't run late
            f" rm -f {d}/qml-cmd; cat {d}/qml-out 2>/dev/null",
            body.encode(), timeout=timeout + 10)
    except Exception:
        return None
    head, _, out = raw.decode(errors="replace").partition("\n")
    return out if head.strip() == str(cid) else None


SETTINGS = "/home/root/.smriti/settings.json"   # written by the Smriti app


def _start_session(host, cfg, style, step, bottom, history, cfg_ai, touch):
    """Session start: notebook-keyed session, scan, greet, watch. The pen
    stream opens BEFORE the greeting (Navy 2026-09-30: a line written right
    after the greeting, while the eye update took 14 s, was never seen);
    pen events are never drained (run 9: _drain waits for 1.2 s of quiet,
    so Navy writing kept it busy up to 15 s and his line was discarded) —
    the greeting's own lamp strokes go by drop_echo; the eye update runs last."""
    ses =NotebookSession(host)
    echo_reset()
    shot = _shot(cfg)
    floor = (ink_floor(shot) if shot is not None
             else (ses.floor or _load_floor()))
    print(f"session start [{ses.name}] — ink floor {floor}"
          + (" (screenshot)" if shot is not None else " (persisted)")
          + (f", resuming {ses.sid[:8]}" if ses.sid else ""),
          flush=True)
    pen, sb = Capture(host), StrokeBuilder()
    floor, greet_ink = _greet(style, step, bottom, host,
                              history, floor, ses, shot, cfg_ai)
    ses.floor = floor
    ses.save()
    _save_floor(floor)
    marker("watch", host)
    baseline = _shot(cfg)                 # page WITH greeting = baseline
    _drain(touch.q)
    print("watching", flush=True)
    eye_state(host, "watching")           # eye updates now, not in 120 s
    return ses, floor, greet_ink, pen, sb, baseline


def eye_state(host: str, s: str) -> None:
    """Push the session state to the tablet eye immediately (the bridge's own
    re-sync runs every 120 s and used to leave a stale 'watching')."""
    try:
        tablet.run(host, f"echo {s} > /home/root/.smriti/state", timeout=8)
    except Exception:
        pass


_EYE = {"error": False}
_TURN_STATE: TurnState | None = None
_STATUS_HOST: str | None = None


def _set_stage(stage: str, reason: str = "") -> str:
    """Publish the canonical host stage; errors may carry one short reason."""
    status = ("error " + _why(reason) if stage == "error" else stage) if _TURN_STATE is None \
        else _TURN_STATE.set(stage, _why(reason))
    # AppLoad reads this tmpfs heartbeat; tablet clock avoids host/tablet clock
    # skew. Best-effort only: status UI must never block a turn.
    try:
        if _STATUS_HOST is None:
            return status
        tablet.run(_STATUS_HOST, "printf '%s %s\\n' \"$(date +%s)\" "
                   + shlex.quote(status.split()[0]) + " > /tmp/smriti-stage", timeout=3)
    except Exception:
        pass
    return status


def _why(reason) -> str:
    """One short line for the eye: no newlines, ~60 chars."""
    return " ".join(str(reason).split())[:60]


def eye_error(host: str, on: bool, reason: str = "") -> None:
    """A model call failed: the eye shows it (Navy 2026-09-30: eye only, no
    ink note) until the next turn that gets an answer. The state file reads
    'watching error <short reason>' — the eye keys on 'error' and the reason
    rides along; it goes over stdin, never into the shell line."""
    if _EYE["error"] != on:
        _EYE["error"] = on
        if not on:
            eye_state(host, "watching")
            return
        try:
            tablet.run(host, "cat > /home/root/.smriti/state",
                       f"watching error {_why(reason)}".strip().encode(), timeout=8)
        except Exception:
            pass


_SETTINGS_SEEN: dict = {}     # host -> last settings actually read
_SETTINGS_FILE = _state_dir() / "tablet-settings.json"


def _settings_known(host: str) -> dict:
    """Last settings read from this tablet: memory, else the copy on disk (a
    freshly started daemon has nothing in memory)."""
    if host not in _SETTINGS_SEEN:
        try:
            _SETTINGS_SEEN[host] = json.loads(_SETTINGS_FILE.read_text()).get(host, {})
        except (OSError, ValueError, AttributeError):
            _SETTINGS_SEEN[host] = {}
    st = _SETTINGS_SEEN[host]
    print(f"[settings] tablet unreachable — using the last known app choice "
          f"({st.get('reader') or 'sonnet'} / {st.get('writer') or 'sonnet'})"
          if st else "[settings] tablet unreachable and no app choice known — "
          "sonnet", flush=True)
    return st


def tablet_settings(host: str) -> dict:
    """{model, profile, prompt} chosen in the Smriti app; {} when unset.
    Tablet unreachable (ssh error/timeout) = last known settings (memory,
    then disk), said out loud, never a silent {}: a USB reset as the daemon
    started sent the greeting to the wrong model although the app said sonnet
    (device 2026-09-30)."""
    try:
        out = tablet.run(host, f"cat {SETTINGS} 2>/dev/null", timeout=8)[1]
        st = json.loads(out) if out.strip() else {}
        st = st if isinstance(st, dict) else {}
    except OSError:                              # link down / timed out
        return _settings_known(host)
    except ValueError:
        st = {}
    if _SETTINGS_SEEN.get(host) != st:
        _SETTINGS_SEEN[host] = st
        try:
            _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _SETTINGS_FILE.write_text(json.dumps({host: st}))
        except OSError:
            pass
    return st


_MODELS_BODY = ""


API_PROVIDERS = ("chatgpt", "openai", "anthropic", "local")   # brain HTTP routes; claude = CLI


def publish_models(host: str, cfg_ai: dict) -> list[str]:
    """Put the app's model picker on the tablet as ~/.smriti/models.json.
    Same JSON shape the QML reads: "models" (the old LM Studio list) stays
    empty; "cloud" holds the choices, each marked ready when this Windows
    user has signed that provider in to Smriti."""
    ids, ready = model_choices(cfg_ai)
    body = json.dumps({"models": [],
                       "cloud": [{"id": i, "ready": ready.get(i, False)} for i in ids],
                       "default": brain.default_spec(cfg_ai)})
    global _MODELS_BODY
    if body == _MODELS_BODY:                  # unchanged: no ssh round trip
        return ids
    _MODELS_BODY = body
    try:
        if tablet.run(host, "mkdir -p /home/root/.smriti && cat > "
                      "/home/root/.smriti/models.json", body.encode(), timeout=10)[0]:
            raise OSError("models.json not written")
        print(f"[models] {len(ids)} on tablet: {', '.join(ids)}", flush=True)
    except Exception:
        pass
    return ids


def model_choices(cfg_ai: dict) -> tuple[list[str], dict[str, bool]]:
    """Picker ids + readiness from Smriti's own per-user sign-ins."""
    try:
        import auth
        signed = {p: ok == "yes" for p, ok, _, _ in auth.status_rows()}
    except Exception:
        signed = {}
    signed["local"] = True                       # key optional; endpoint decides
    cfg = {**brain.DEFAULTS, **cfg_ai}
    ids, ready = [], {}

    def add(spec, provider):
        if spec not in ready:
            ids.append(spec)
            ready[spec] = bool(signed.get(provider))
    add(brain.default_spec(cfg_ai), cfg.get("provider", "openai"))
    for m in brain.CHATGPT_MODELS:
        add(f"chatgpt:{m}", "chatgpt")
    add(f"openai:{cfg.get('openai_model') or 'gpt-5.5'}", "openai")
    add(f"anthropic:{cfg.get('anthropic_model') or 'claude-sonnet-4-5'}", "anthropic")
    if cfg.get("local_model"):
        add(f"local:{cfg['local_model']}", "local")
    for a in CLAUDE_CFG.get("models", []):
        add(a, "claude")
    return ids, ready


def apply_settings(cfg_ai: dict, st: dict) -> tuple[str, str, dict]:
    """(profile, extra system prompt, route). route = {reader, writer,
    context, escalate}: each a brain spec — "<provider>:<id>" or a claude alias.
    Empty / unknown / the old "local" -> the [brain] default."""
    profile = st.get("profile") if st.get("profile") in harness.PROFILES else "auto"
    default = brain.default_spec(cfg_ai)

    def pick(v):
        head, sep, tail = v.partition(":") if isinstance(v, str) else ("", "", "")
        ok = isinstance(v, str) and (v in brain.CLAUDE_ALIASES
                                     or (sep and tail and head in API_PROVIDERS))
        return v if ok else default
    turn = __import__("os").environ.get("SMRITI_TURN", st.get("turn", cfg_ai.get("turn", "split")))
    turn = turn if turn in ("split", "merged") else "split"
    route = {"reader": pick(st.get("reader")), "writer": pick(st.get("writer")),
             "context": st.get("context") if st.get("context") in ("stateless", "session") else "auto",
             "escalate": pick(st.get("escalate")) if st.get("escalate_next") else "", "turn": turn}
    return profile, str(st.get("prompt", "") or "").strip()[:1500], route


def merged_fallback(route: dict, action: str | None) -> str:
    if route.get("turn") != "merged":
        return "disabled"
    if action:
        return "selection action"
    if route.get("escalate"):
        return "escalation armed"
    if route.get("reader") != route.get("writer"):
        return "different reader/writer"
    if brain.resolve(route.get("reader"), brain.load_config())[0] == "claude":
        return "Claude route"
    return ""


# ── Claude budget: every cloud call is counted; caps are hard ────────────────
CLAUDE_CFG: dict = {}
_USAGE = _state_dir() / "usage.json"


def _usage() -> dict:
    today = time.strftime("%Y-%m-%d")
    try:
        u = json.loads(_USAGE.read_text())
    except (OSError, ValueError):
        u = {}
    return u if u.get("date") == today else {"date": today, "calls": 0, "in": 0,
                                             "out": 0, "cost_usd": 0.0}


def doctor(host: str | None = None, print_: bool = True) -> list[tuple[str, bool, str]]:
    """Every link from pen to reply, each with its fix: `monke.py doctor`.

    With no explicit host, inspect the configured capture endpoint. This keeps
    doctor on the same USB or tailnet path the daemon will use.
    """
    if host is None:
        try:
            cfg = _load_cfg()
            host = _resolve_link(cfg)
        except (OSError, ValueError, RuntimeError):
            host = "rm2"
    import socket
    probe = ("echo eye=$(journalctl -u xochitl -b --no-pager 2>/dev/null | grep -c 'Loading file smriti-eye');"
             " echo armed=$(cat /home/root/.smriti/armed 2>/dev/null);"
             " echo state=$(cat /home/root/.smriti/state 2>/dev/null);"
             " echo points=$(sed -n 's/^SMRITI_DAEMON=//p' /home/root/.config/smriti-eye 2>/dev/null);"
             " echo client=${SSH_CLIENT%% *};"
             " echo rss=$(grep VmRSS /proc/$(pidof xochitl)/status 2>/dev/null | tr -s ' ' | cut -d' ' -f2)")
    t = {}
    try:
        r = subprocess.run([*ssh_argv(host, "-o", "ConnectTimeout=6"), probe],
                           capture_output=True, timeout=12)
        if r.returncode == 0:
            t = dict(l.split("=", 1) for l in r.stdout.decode(errors="replace").splitlines() if "=" in l)
    except (subprocess.SubprocessError, OSError):
        pass
    try:
        socket.create_connection(("127.0.0.1", 7333), timeout=1).close()
        daemon = True
    except OSError:
        daemon = False
    st = _SETTINGS_SEEN.get(host) or (tablet_settings(host) if t else {})
    bc = brain.load_config()
    brain_row = _brain_row(bc)
    rss = int(t.get("rss") or 0)
    link = bool(t)
    rows = [
        ("tablet link (ssh)", link, "wake the tablet; check Wi-Fi / tailnet, then: smriti link-test"
         " (first time on this PC: smriti pair <tablet-ip>)"),
        ("eye patch loaded", link and int(t.get("eye") or 0) > 0,
         "restart the daemon (it runs xovi/start itself), then reopen the notebook"),
        ("bridge armed", t.get("armed", "").startswith("1"), "ssh rm2 'echo 1 > /home/root/.smriti/armed'"),
        ("daemon running here", daemon, "tray icon -> Start   (or: smriti run)"),
        ("eye points at this laptop", link and (
            t.get("points", "").split(":")[0] == "127.0.0.1" and daemon      # wireless: ssh -R tunnel
            or t.get("points", "").split(":")[0] == t.get("client", "").removeprefix("::ffff:")),
         "restart the daemon: it re-points the eye at start"),
        ("notebook open (QML answers)", link and qml_eval(host, "1") == "1",
         "open a notebook on the tablet (the eye lives in the document view)"),
        ("screenshot", link and _shot() is not None, "open a notebook; fw < 3.24 needs goMarkableStream"),
        (f"app route: read={st.get('reader') or 'sonnet'} write={st.get('writer') or 'sonnet'}",
         bool(st.get("writer")), "pick a Reader/Writer in the Smriti app on the tablet"),
        *([brain_row] if brain_row else []),
        ("pdflatex (maths, graphs, circuits)", _have_tex(),
         "smriti deps (bundled TinyTeX) or: smriti deps set pdflatex <path-to-pdflatex.exe>"),
        (f"xochitl memory {rss // 1024} MB", link and 0 < rss < 500 * 1024,
         "stop the daemon; ssh rm2 systemctl restart xochitl (reopen the notebook)"),
    ]
    if print_:
        print(f"session state on the tablet: {t.get('state') or '?'}")
        for name, ok, fix in rows:
            print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        fix: {fix}"))
    return rows


def _brain_row(bc: dict) -> tuple[str, bool, str] | None:
    """Provider-aware sign-in check via `auth status` (other module, lazy).
    No auth module yet -> no row (Hermes is no longer a requirement)."""
    try:
        import auth
    except ImportError:
        return None
    import contextlib
    import io
    import re
    import shutil
    buf, active, ok = io.StringIO(), "?", False
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = auth.cli(["status"])
        m = re.search(r"provider=(\w+)", buf.getvalue())
        active = m.group(1) if m else "?"
        rows = {r[0]: r[1] for r in auth.status_rows()} if hasattr(auth, "status_rows") else {}
        via_cli = (active == "chatgpt" and bc.get("transport") in ("cli", "http")
                   and shutil.which(shlex.split(bc.get("hermes_bin") or "hermes")[0]) is not None)
        ok = rc == 0 and (active == "local" or via_cli or rows.get(active) == "yes")
        if via_cli:
            active += " (via Hermes CLI)"
    except Exception:                       # noqa: BLE001 — doctor never crashes
        ok = False
    return (f"brain sign-in: {active}", ok,
            "smriti auth status, then: smriti auth login chatgpt|claude (or auth key set ...)")


def _have_tex() -> bool:
    from tex import have_pdflatex
    return have_pdflatex()


def _cfg_claude() -> dict:
    try:
        return _load_cfg().get("claude", {})
    except (OSError, ValueError):
        return {}


# Claude budget, counted from daemon start (Navy 2026-09-30): the requested
# model (sonnet) until sonnet_pct of the Pro 5 h window, haiku for haiku_pct
# more, then the turn fails. The CLI gives no percent — only status and
# reset time — so spend is API-equivalent $ over window_usd.
# ponytail: window_usd is a calibration knob (usage-card % delta vs logged $).
BUDGET = {"spent": 0.0, "resets_at": 0, "status": "allowed"}


def claude_tier(model: str) -> str | None:
    """Claude model to call now, or None = no Claude call (the turn fails
    visibly). Budget tiers apply to Claude only; ChatGPT is uncapped."""
    if BUDGET["resets_at"] and time.time() > BUDGET["resets_at"]:
        BUDGET.update(spent=0.0, resets_at=0, status="allowed")   # new 5 h window
    if BUDGET["status"] == "rejected":               # window spent: stop
        return None
    pct = 100 * BUDGET["spent"] / float(CLAUDE_CFG.get("window_usd", 10.0))
    if BUDGET["status"] != "allowed":                # allowed_warning: cheapest Claude
        return "haiku" if pct < CLAUDE_CFG.get("sonnet_pct", 32) + CLAUDE_CFG.get("haiku_pct", 22) else None
    if pct < CLAUDE_CFG.get("sonnet_pct", 32):
        return model
    if pct < CLAUDE_CFG.get("sonnet_pct", 32) + CLAUDE_CFG.get("haiku_pct", 22):
        return "haiku"
    return None


def budget_pct() -> int:
    return round(100 * BUDGET["spent"] / float(CLAUDE_CFG.get("window_usd", 10.0)))


def _spend(rec: dict) -> None:
    from oracle import RATE
    BUDGET["spent"] += float(rec.get("cost_usd", 0) or 0)
    if RATE.get("status"):
        BUDGET["status"] = RATE["status"]
    if RATE.get("resetsAt"):
        BUDGET["resets_at"] = float(RATE["resetsAt"])


def usage_add(ses, host: str, rec: dict, role: str) -> None:
    """Ledger: daily totals (~/.config/smriti/usage.json), per-notebook CSV,
    and the tablet's ~/.smriti/usage.json (the app's counter)."""
    _spend(rec)
    u = _usage()
    u["calls"] += 1
    u["in"] += rec.get("in", 0)
    u["out"] += rec.get("out", 0)
    u["cost_usd"] = round(u["cost_usd"] + rec.get("cost_usd", 0.0), 4)
    _USAGE.parent.mkdir(parents=True, exist_ok=True)
    _USAGE.write_text(json.dumps(u))
    if ses is not None:
        ses.claude_calls = getattr(ses, "claude_calls", 0) + 1
        with open(ses.dir / "usage.csv", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{role},{rec.get('model')},"
                    f"{rec.get('in', 0)},{rec.get('out', 0)},{rec.get('cost_usd', 0)}\n")
    publish_usage(host, ses)
    print(f"[{rec.get('provider', 'claude')}] {role} {rec.get('model')}: {rec.get('in', 0)} in / "
          f"{rec.get('out', 0)} out ${rec.get('cost_usd', 0):.3f} — budget {budget_pct()}% "
          f"(today {u['calls']} calls)",
          flush=True)


def publish_usage(host: str, ses=None, note: str = "") -> None:
    u = _usage()
    body = json.dumps({"today": u["calls"], "session": getattr(ses, "claude_calls", 0),
                       "pct": budget_pct(), "tier": claude_tier("sonnet") or "stopped",
                       "tokens_in": u["in"], "tokens_out": u["out"], "note": note})
    try:
        tablet.run(host, "cat > /home/root/.smriti/usage.json", body.encode(), timeout=10)
    except Exception:
        pass


def cloud_chat(model: str, ses, host: str, role: str,
               session: bool = False, on_text=None):
    """messages -> text through the brain for the tablet's `model` setting:
    "<provider>:<id>" through brain with Smriti's own per-user sign-in
    (chatgpt / openai / anthropic / local), or a Claude Code alias (counted,
    capped by claude_tier). Any failure RAISES — the turn fails visibly.
    session=True resumes the notebook's Claude session (context grows per turn
    — Navy's choice for ask/infer/think turns); ChatGPT is stateless.
    on_text(text so far) -> truthy stops the stream early."""
    from oracle import LAST_USAGE, _claude_compat, claude_chat as _cc
    provider, name = brain.resolve(model, brain.load_config())

    def call_api(messages):
        acc = []

        def feed(chunk):
            acc.append(chunk)
            return on_text("".join(acc))
        try:
            return brain.chat(messages, model=f"{provider}:{name}", role=role,
                              on_text=feed if on_text else None)
        finally:
            if brain.LAST.get("provider") in API_PROVIDERS:
                usage_add(ses, host, brain.last_usage(), role)

    def call(messages):
        if provider in API_PROVIDERS:
            return call_api(messages)
        tier = claude_tier(name)
        if tier is None:
            why = f"budget {budget_pct()}% / {BUDGET['status']}"
            publish_usage(host, ses, note=f"{why}: stopped")
            raise RuntimeError(f"Claude stopped ({why})")
        if tier != name:
            print(f"[claude] budget {budget_pct()}% — {tier} instead of {name}", flush=True)
        cfg = dict(CLAUDE_CFG, model=tier)
        if on_text:
            cfg["_on_text"] = on_text        # stream: ink as it is written
        LAST_USAGE.clear()
        try:
            if session:
                system = next((m["content"] for m in messages if m["role"] == "system"), None)
                user = next(m for m in reversed(messages) if m["role"] == "user")
                text, sid = _cc(user["content"] if isinstance(user["content"], str)
                                else " ".join(p.get("text", "") for p in user["content"]),
                                system=system, resume=getattr(ses, "sid", "") or None,
                                cfg=cfg, persist=True)
                if sid:
                    ses.sid = sid
            else:
                text = _claude_compat(messages, cfg)
            return text
        finally:
            if LAST_USAGE:
                usage_add(ses, host, dict(LAST_USAGE, provider="claude"), role)
    return call


# xochitl fineliner colour enum (device-mapped 2026-09-26, fw 3.27.1.0 —
# verified in a PDF export): 0 black, 1 gray, 2 white, 6 blue, 7 red
def pen_color(host: str, color: int) -> bool:
    """Switch the active pen colour; lamp-injected ink then saves in it."""
    out = qml_eval(host, f"documentViewTools.activePen.color = {int(color)};"
                         " documentViewTools.activePen.color")
    return out is not None and out.strip() == str(int(color))


def _graph_on_screen(g, shot, frac: float = 0.5) -> bool:
    """The graph itself is on the visible page: ink along its own stroke
    points, not just anywhere in its box (run 12: the 'add y = ...'
    question written where the old graph sat counted as the graph)."""
    import graph
    from screen import stroke_mask
    ink = graph._dilate(stroke_mask(shot), 4)        # pixels + page vectors
    pts = [p for s in graph.strokes(g) for p in s][::3]
    hit = sum(1 for x, y in pts if 0 <= y < ink.shape[0] and 0 <= x < ink.shape[1] and ink[y, x])
    return hit >= frac * len(pts)


def _graphs(ses) -> dict:
    """Label -> Graph for this notebook session (G1, G2, ... in draw order)."""
    if getattr(ses, "graphs", None) is None:
        ses.graphs = {}
    return ses.graphs


def notebook_context(ses, n: int = 5) -> str:
    """Other pages of this notebook Navy asked about (last n), so the reader
    knows 'the circuit from before' lives elsewhere."""
    cur = str(int((_TURN.get("view") or {}).get("page", -2)) + 1)
    pages = [(k, qs) for k, qs in (getattr(ses, "pages", None) or {}).items() if k != cur]
    if not pages:
        return ""
    return "Earlier pages: " + " | ".join(
        f"p{k}: " + "; ".join(qs[:2]) for k, qs in sorted(pages, key=lambda t: int(t[0]))[-n:]) + "."


def page_log(ses, max_chars: int = 2500) -> str:
    """This page's turns from the session transcript, for the writer (run 10:
    on a scrolled page 'summarize this page' answered 'MOSFET…' — the writer
    never saw the page). Drawings collapse to [drawing]; newest kept."""
    turns = []
    for who, body in _page_turns(ses):
        body = re.sub(r"\$\$\s*\\begin\{.*?\$\$|<svg.*?</svg>", "[drawing]", body, flags=re.S)
        turns.append(f"{who}: {' '.join(body.split())}")
    out = "\n".join(turns)[-max_chars:]
    return f"This page so far (session log, oldest first):\n{out}" if out else ""


def _page_turns(ses) -> list[tuple[str, str]]:
    """(who, body) of this page's turns in the session transcript, oldest first."""
    cur = f"p{int((_TURN.get('view') or {}).get('page', -2)) + 1}"
    try:
        md = (ses.dir / "transcript.md").read_text(encoding="utf-8")
    except (OSError, AttributeError):
        return []
    return [(who, body) for who, pg, body in
            re.findall(r"^## (Navy|Monke) \((p\d+), [^)]*\)\n(.*?)(?=^## |\Z)", md, flags=re.M | re.S)
            if pg == cur]


def _monke_regions(ses, shot, view=None) -> list[dict]:
    """Monke's own regions for the <page> map (protocol.page_map). Where = the
    Monke layer of the page file: ownership comes from the layer. What = the
    session: graphs keep their label and curves (ses.graphs); the other
    groups take this page's logged replies top to bottom.
    ponytail: the session keeps no reply <-> strokes record, so bodies are
    paired by order and only when the counts agree; else the box goes bare.
    Record the inked box per turn in log_turn when a wrong body shows up."""
    import graph
    import layers
    import vectors
    from workspace import _near, _pen_boxes
    out = [dict(r) for r in (getattr(ses, "refined", None) or {}).values()]
    for label, g in (getattr(ses, "graphs", None) or {}).items():
        if _graph_on_screen(g, shot):
            x0, y0, x1, y1 = harness.strokes_box(graph.strokes(g))
            out.append({"id": label, "type": "graph", "box": (x0, y0, x1 + 1, y1 + 1),
                        "text": ", ".join(f"y = {c}" for c in g.curves) + f", x {g.xmin:g}..{g.xmax:g}"})
    v = view if view is not None else shot.info.get("view")
    ink = (shot.info.get("layers") or {}).get(layers.MONKE) or []
    boxes = sorted((b for b, _ in _pen_boxes([vectors.to_screen(s, v) for s in ink] if v else [], 60)
                    if not any(_near(b, m["box"], 0) for m in out)), key=lambda b: (b[1], b[0]))
    replies = [body for who, body in _page_turns(ses) if who == "Monke"]
    for i, b in enumerate(boxes):
        r = {"box": b}
        if len(replies) == len(boxes):
            kind, text = protocol.describe_reply(replies[i])
            r.update(turn=i + 1, type=kind, text=text)
        out.append(r)
    return out


def _spp_directives(text: str, page: str, semantic=None) -> str:
    """The reply without its leading SPP directive lines, which are logged,
    never inked."""
    found, body = protocol.parse_directives(text)
    if found:
        known = set(re.findall(r'<region id="(\w+)"', page or ""))
        known |= set(regions.boxes(semantic or []))
        say = lambda d: (d.get("raw") or " ".join(filter(None, (
            d["op"], d.get("how"), d.get("id"), d.get("as") and f"as {d['as']}")))) + (
            f" (unknown id {d['id']}, ignored)" if d.get("id") and d["id"] not in known else "")
        print("[spp] directives: " + "; ".join(say(d) for d in found), flush=True)
    return body


def graphs_context(ses) -> str:
    """One line for the reader/writer: which graphs exist and their curves."""
    gs = getattr(ses, "graphs", None) or {}
    if not gs:
        return ""
    return ("Graphs in this notebook: " + "; ".join(
        f"{k}: " + ", ".join(f"y{i + 1} = {c}" for i, c in enumerate(g.curves))
        for k, g in gs.items()) + ". To draw on one, the text says 'add y = ... to G<n>'.")


def _refine(reading: dict, page: str, shot, ses, host: str, step: int,
            floor: int, bottom: int, occ=None, view=None) -> int | None:
    """Beautify target vectors without a writer call; None means model fallback."""
    import beautify
    import place
    from screen import stroke_mask

    t0 = time.time()
    rid = reading.get("target") or "F"
    kind = reading.get("target_kind") or reading.get("kind") or "diagram"
    kind = "diagram" if kind in ("sketch", "mixed", "text") else kind
    regions = protocol.boxes(page)
    target = regions.get(rid)
    if target is None:
        print(f"[refine] fallback: target {rid} missing", flush=True)
        return None
    page_layers = shot.info.get("layers") if shot is not None else None
    source = protocol.region_strokes(view, page_layers, target)
    if not source:
        print(f"[refine] fallback: no vectors in {rid}", flush=True)
        return None
    assert shot is not None
    if not _same_page(source):
        print("[refine] fallback: target no longer on screen", flush=True)
        return None
    scene = beautify.beautify(source, kind)
    if scene is None or scene.explained < .6:
        print("[refine] fallback: geometry confidence below 0.60", flush=True)
        return None
    clean = scene.strokes()
    box = harness.strokes_box(clean)
    if not clean or not box:
        print("[refine] fallback: beautifier produced no strokes", flush=True)
        return None
    if occ is None:
        occ = place.occupancy(stroke_mask(shot))
    spot = place.resolve({"op": "place", "how": "beside", "id": rid}, regions, occ,
                         (box[2] - box[0] + 1, box[3] - box[1] + 1), floor, bottom)
    if spot is None:
        print("[refine] fallback: no safe placement", flush=True)
        return None
    clean = place.shift(clean, spot.x, spot.y, spot.k)
    if not _ink(clean, 2600, step, host, thin=True):
        print("[refine] fallback: ink failed", flush=True)
        return None
    out_box = harness.strokes_box(clean)
    refined = getattr(ses, "refined", None)
    if not isinstance(refined, dict):
        refined = ses.refined = {}
    label = f"R{len(refined) + 1}"
    refined[label] = {"id": label, "box": out_box, "type": scene.kind,
                      "text": scene.describe()}
    ses.log_turn("Monke", f"[refine {rid} as {kind}: {len(scene.prims)} prims]")
    ms = int((time.time() - t0) * 1000)
    print(f"[refine] code: {kind} {len(scene.prims)} prims "
          f"explained={scene.explained:.2f} {ms}ms", flush=True)
    _TURN["inked"] = True
    return max(py for stroke in clean for _, py in stroke)


def _plot(reading: dict, ses, host: str, y: int, bottom: int, step: int,
          shot=None) -> int | None:
    """Deterministic plotting for a confirmed 'y = ...' reading: new graph,
    or ('add ...') only the new curve on the notebook's last graph, each
    curve in its own pen colour + dash. An overlay whose graph is not on
    the visible page (page turned) redraws the whole graph here. Returns
    new floor, or None when the reading isn't a plottable equation (caller
    falls back to the model)."""
    import graph
    for src in (reading.get("text", ""), reading.get("latex", "")):
        label, src = graph.target(src)
        p = graph.parse(src)
        if p:
            break
    else:
        return None
    expr, a, b, overlay = p
    graphs = _graphs(ses)
    g = graphs.get(label) if label else getattr(ses, "graph", None)
    if label and g is None:
        print(f"[plot] no graph {label} in this notebook — a new one", flush=True)
    overlay = overlay and g is not None             # no graph yet: a new one
    exprs = [expr]
    if overlay and g is not None and shot is not None and not _graph_on_screen(g, shot):
        # run 2026-09-30 turn 06: a lone red curve, no axes, on a new page
        print("[plot] last graph not on this page — redrawing it whole", flush=True)
        exprs, a, b, overlay = g.curves + [expr], g.xmin, g.xmax, False
    if overlay and g is not None:
        g.curves.append(expr)
        batches = [(graph.strokes(g, only_new=True), g.color(len(g.curves) - 1))]
    else:
        # place like replies do: below the ink OR in any real free band,
        # shrinking the figure (to a floor) before giving up — "below the
        # lowest ink" alone refused a plot on a half-empty page (device run)
        old_label = g.label if (g is not None and len(exprs) > 1) else ""
        g = graph.Graph.new(exprs[0], a, b, y=0)
        g.label = old_label or f"G{len(graphs) + 1}"
        h0 = max(py for s in graph.strokes(g) for _, py in s)
        regions = [(y, bottom - y)]
        if shot is not None:
            regions += [(max(y0, 100) + 10, min(y1, bottom) - max(y0, 100) - 10)
                        for y0, y1 in free_bands(shot)]
        regions.sort(key=lambda r: -r[1])
        spot = next(((ry, k) for k in (1.0, 0.8, 0.65) for ry, rh in regions
                     if h0 * k <= rh), None)
        if spot is None:
            print("[plot] no room on this page — model reply instead", flush=True)
            return None
        g.y, g.max_w = spot[0], int(g.max_w * spot[1])
        batches = [(graph.strokes(g), g.color(0))]
        for e in exprs[1:]:                 # redraw: later curves in their colours
            g.curves.append(e)
            batches.append((graph.strokes(g, only_new=True), g.color(len(g.curves) - 1)))
    ses.graph = graphs[g.label] = g
    for st_i, c in batches:
        if not _ink(st_i, 2600, step, host, color=c, thin=True):   # Monke layer, colour, thin
            return None
    st = [s for st_i, _ in batches for s in st_i]
    name = graph.COLOR_NAMES.get(batches[-1][1], "black")
    ses.log_turn("Monke", f"[plot y{len(g.curves)} = {expr} ({name})]")
    print(f"[plot] {g.label} y{len(g.curves)} = {expr} [{g.xmin:g},{g.xmax:g}] "
          f"{'overlay' if overlay else 'new'} in {name}", flush=True)
    return max(py for s in st for _, py in s)


def _confirm(reading: dict, focus, host: str, timeout: float = 180) -> dict | None:
    """Confirm window (xovi eye popup) protocol, all files in ~/.smriti on
    the tablet: daemon writes confirm.png (the focus crop = what was
    selected/read) + confirm.json (the reading); the popup shows both with
    an editable field and writes confirm-reply: 'confirm' or 'discard' on
    line 1, the (possibly edited) reading on the lines after. No answer in
    `timeout` s = discard — never ink on a guess. Returns the reading
    (edited text replaces it) or None."""
    import shlex
    d = "/home/root/.smriti"
    body = json.dumps({**reading, "id": int(time.time()),
                       "shown": harness.describe(reading)})
    try:
        # JSON via stdin, never the command line: Windows argv parsing halves
        # backslashes, so LaTeX (\frac, \cdot) became invalid JSON and the
        # popup silently never showed (device session 2026-09-26)
        for script, data in ((f"mkdir -p {d} && rm -f {d}/confirm-reply && "
                              f"cat > {d}/confirm.png", harness.png(focus)),
                             (f"cat > {d}/confirm.json.tmp && "
                              f"mv {d}/confirm.json.tmp {d}/confirm.json", body.encode())):
            if tablet.run(host, script, data, timeout=20)[0]:
                raise OSError(f"tablet write failed: {script[:40]}")
    except Exception as e:
        print(f"[confirm] can't reach tablet ({e}) — discarded", flush=True)
        return None
    print("[confirm] waiting for Navy on the tablet…", flush=True)
    out, t0 = "", time.time()
    while time.time() - t0 < timeout:
        try:
            out = tablet.run(host, f"cat {d}/confirm-reply 2>/dev/null",
                             timeout=10)[1].decode(errors="replace")
        except Exception:
            out = ""
        if out.strip():
            break
        time.sleep(0.5)                   # a poll is ~0.2 s on the channel
    try:
        tablet.run(host, f"rm -f {d}/confirm.json {d}/confirm.png {d}/confirm-reply",
                   timeout=10)
    except OSError:
        pass
    verdict, _, edited = out.partition("\n")
    if verdict.strip() != "confirm":
        print(f"[confirm] {verdict.strip() or 'timeout'} — nothing inked", flush=True)
        return None
    edited = edited.strip()
    if edited and edited != harness.describe(reading):
        reading = harness.apply_edit(reading, edited)
        print(f"[confirm] edited: {edited}", flush=True)
    return reading


_TURN: dict = {"view": None}   # live view when this turn's question was read


def _fail(key: str, line: str) -> None:
    """This turn left nothing, or less than asked, on the page: say it once
    (the fit probe lays a reply out at many sizes — one broken drawing
    printed 14 lines) and let the eye show '!' (no note on Navy's page)."""
    seen = _TURN.get("failed")
    if not isinstance(seen, set):
        seen = _TURN["failed"] = set()
    if key not in seen:
        seen.add(key)
        _TURN["why"] = _TURN.get("why") or line.split("] ", 1)[-1]   # the eye's reason
        print(line, flush=True)


def _erase_answered_prompt(host: str) -> None:
    """#P strokes go only once the reply actually inked (run 7: the floor
    is no proof — a bad page-turn floor of 1849 never moved)."""
    if _TURN.get("prompt") and _TURN.get("inked"):
        erase_prompt(_TURN["prompt"], host)


def _bbox(st) -> tuple[int, int, int, int]:
    xs, ys = [x for x, _ in st], [y for _, y in st]
    return min(xs), min(ys), max(xs), max(ys)


def _prompt_in_box(prompt: list, layers: dict, v: dict, box, tol: int = 6) -> str:
    """'' when the page's own strokes (.rm, Navy's layers) lying wholly in
    `box` are exactly the prompt's strokes, matched by bbox within tol px;
    else why not. The vectors prove what a delete would take."""
    import vectors
    x0, y0, x1, y1 = box
    inside = [_bbox(vectors.to_screen(s, v)) for name, ss in layers.items() if name != "Monke"
              for s in ss]
    inside = [b for b in inside if b[0] >= x0 and b[1] >= y0 and b[2] <= x1 and b[3] <= y1]
    want = [_bbox(s) for s in prompt]
    free = list(want)
    for b in inside:
        hit = next((w for w in free if all(abs(p - q) <= tol for p, q in zip(b, w))), None)
        if hit is None:
            return f"a non-prompt stroke lies in the box at {b}"
        free.remove(hit)
    return f"{len(free)} prompt strokes not in the page file yet" if free else ""


def _prompt_offset(prompt: list, layers: dict, tol: int = 6):
    """(ox, oy) with screen = page + offset, from the prompt's own strokes:
    each was captured from the pen (screen px) and sits in the page file
    (.rm) — pair them by bounding box. Exact, and needs no screenshot: the
    view fields map wrong on a scrolled page (run 12), and matching page to
    screenshot fails right after a reply, when the file lacks the screen's
    newest ink (sim hash_p). None = not all of them in the file yet."""
    want = [_bbox(s) for s in prompt]
    page = [_bbox(s) for name, ss in layers.items() if name != "Monke" for s in ss]
    size = lambda b: (b[2] - b[0], b[3] - b[1])
    a = max(want, key=lambda b: sum(size(b)))            # biggest stroke: fewest look-alikes
    for b in page:
        if any(abs(p - q) > 2 * tol for p, q in zip(size(a), size(b))):
            continue
        ox, oy = a[0] - b[0], a[1] - b[1]
        pairs = []
        for w in want:
            hit = next((p for p in page if all(abs(w[i] - p[i] - (ox, oy)[i % 2]) <= 2 * tol
                                               for i in range(4))), None)
            if hit is None:
                break
            pairs.append((w[0] - hit[0], w[1] - hit[1]))
        else:                                            # every stroke found: median = centred
            return (sorted(p[0] for p in pairs)[len(pairs) // 2],
                    sorted(p[1] for p in pairs)[len(pairs) // 2])
    return None


def _turn_offset(strokes, layers):
    """This turn's registered (ox, oy) from the question's own strokes, or
    None (no strokes, or not all in the page file yet: it lags ~10 s). On
    a scrolled page x maps only with it (rebase.ViewTransform.x_trusted)."""
    return _prompt_offset(strokes, layers) if strokes and layers else None


def _turn_start(shot, strokes, host: str) -> None:
    """This turn's live view and registered #P offset, from the question's frame."""
    view = shot.info.get("view") if shot is not None else None
    pid = view.get("pageId") if view else None
    if _TURN.get("regions_page") != pid:
        _TURN.update(regions=[], regions_page=pid, region_turn=0)
    offset = _turn_offset(strokes, shot.info.get("layers")) if shot is not None else None
    region_view = dict(view) if view else None
    if region_view and offset:
        region_view["ox"], region_view["oy"] = offset
    _TURN.update(view=view, region_view=region_view, host=host, offset=offset)


def erase_prompt(strokes: list, host: str, pad: int = 10, wait: float = 15.0) -> bool:
    """Delete a #P prompt's own strokes once answered (Navy 2026-09-30, like
    the greeting fade) — only strokes registered as the prompt. One box
    round the prompt: xochitl's selection takes strokes wholly inside it, so
    every prompt stroke is in. Before selecting, the page's vector strokes
    (.rm, flushed ~10 s after the last stroke) must show exactly the prompt
    strokes in that box — any other stroke = nothing deleted. Then the live
    selection count must equal n too. Undoable on the tablet. Skipped when
    the view is unreadable or Navy already has a selection. (Per-stroke boxes don't work:
    add-mode 1 toggles, overlapping handwriting boxes deselect each other.)"""
    import vectors
    if not strokes:
        return False
    x0, y0, x1, y1 = _bbox([p for st in strokes for p in st])
    box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    why = "view unknown"
    for _ in range(int(wait / 3) + 1):
        v = vectors.view(host)
        if not v:
            break
        layers = vectors.page(host, v) or {}
        o = _prompt_offset(strokes, layers)
        if o is None:
            why = f"{len(strokes)} prompt strokes not in the page file yet"
        else:
            v["ox"], v["oy"] = o
            why = _prompt_in_box(strokes, layers, v, box)
            if not why or "not in the page file" not in why:
                break
        time.sleep(3)                               # .rm lags ~10 s: wait for it
    if why:
        print(f"[prompt] kept: {why}", flush=True)
        return False
    n = len(strokes)
    tool = qml_eval(host, "sceneController.selectionItemCount > 0 ? 'busy' : "
                          "(function(){var a=documentViewTools.activeTool;"
                          "documentViewTools.activeTool='selection';return a;})()")
    if not tool or tool == "busy" or tool.startswith("ERR"):
        print(f"[prompt] kept: selection busy/unreadable ({tool})", flush=True)
        return False
    time.sleep(0.3)                                 # tool switch lands a tick later
    qml_eval(host, f"sceneController.addSelectionRect(Qt.rect({box[0] - v['ox']}, "
                   f"{box[1] - v['oy']}, {box[2] - box[0]}, {box[3] - box[1]}), 0); 1")
    time.sleep(0.3)                                 # so does the count
    k = qml_eval(host, "String(sceneController.selectionItemCount)")
    ok = k == str(n)
    rect = (f"Qt.rect({box[0] - v['ox']}, {box[1] - v['oy']}, "
            f"{box[2] - box[0]}, {box[3] - box[1]})")
    if ok:
        # each step a tick apart (xochitl applies QML calls a tick late);
        # re-selecting the box proves the strokes are gone
        # (false) = plain delete; (true) left the strokes (device probe) —
        # likely cut-to-clipboard, which would also clobber Navy's clipboard
        qml_eval(host, "sceneController.deleteSelectedItems(false); 1")
        time.sleep(0.3)
        qml_eval(host, f"sceneController.addSelectionRect({rect}, 0); 1")
        time.sleep(0.3)
        left = qml_eval(host, "String(sceneController.selectionItemCount)")
        ok = left == "0"
        if not ok:
            print(f"[prompt] delete did not take ({left} still in the box)", flush=True)
    qml_eval(host, "sceneController.clearSelectedItems(); 1")
    time.sleep(0.3)
    qml_eval(host, f"documentViewTools.activeTool = {json.dumps(tool)}; 1")
    print(f"[prompt] {'erased' if ok else 'kept'} ({k} selected, {n} prompt strokes)", flush=True)
    return ok


_PAGE: dict = {"id": None}       # page id at the last page-turn gesture


def _page_turned(host: str, last: str | None) -> tuple[bool, str | None]:
    """A 'pageturn' swipe is a turn only if the page id changed (runs 9-10:
    vertical scrolls were logged as page turns). Unreadable view = turn."""
    import vectors
    v = vectors.view(host)
    pid = v.get("pageId") if v else None
    return (pid is None or pid != last), pid


def _same_view(v0: dict) -> bool | None:
    """Live-view guard: same page id and same scroll position -> True; other
    page or scrolled -> False (the reply is placed in screen coordinates:
    after a scroll it would land on other ink); None = view unreadable."""
    import vectors
    v = vectors.view(_TURN.get("host") or "rm2")
    if not v:
        return None
    if v["pageId"] != v0["pageId"]:
        print("[page] question's page is not open — reply NOT inked", flush=True)
        return False
    if (v.get("top"), v.get("right")) != (v0.get("top"), v0.get("right")):
        print("[page] scrolled since the question — reply NOT inked, kept in the log",
              flush=True)
        return False
    return True


def _same_page(strokes: list, min_frac: float = 0.5) -> bool:
    """Is the page this turn answers still on screen? The live view decides
    when known (page id + viewport, _same_view); else >= min_frac of the
    question's points dark (±2 px) on a fresh screenshot. Swipes during a
    turn are not seen by the daemon: Navy turned the page while a confirm
    was open and the reply, placed by the old page, inked onto the new one
    (device 2026-09-30). No frame = no proof = False."""
    if _TURN.get("view"):
        same = _same_view(_TURN["view"])
        if same is not None:
            return same
    import numpy as np
    shot = _shot()
    if shot is None:
        return False
    # any non-paper channel: gray pen and yellow highlighter are ink too
    dark = np.asarray(shot.convert("RGB").resize((CANVAS_W, CANVAS_H))).min(axis=2) < 200
    d = dark.copy()
    for dy in (-2, 0, 2):
        for dx in (-2, 0, 2):
            d |= np.roll(np.roll(dark, dy, 0), dx, 1)
    pts = [(x, y) for s in strokes for x, y in s
           if 0 <= x < CANVAS_W and 0 <= y < CANVAS_H]
    return bool(pts) and sum(d[y, x] for x, y in pts) >= min_frac * len(pts)


def _reply(strokes, style, step, bottom, host, history, floor,
           ses: NotebookSession = None, cfg_ai: dict = None,
           fade=False, fade_hold=10, greet=None, shot=None,
           input_fade=False, focus_box=None, action=None,
           confirm=False) -> int:
    """Returns the new ink floor (max y written this session).
    focus_box/action: a selection-menu request (lasso box in page px + one of
    harness.ACTIONS) — the box, not new strokes, is the primary content."""
    marker("busy", host)
    print(f"page committed ({len(strokes)} strokes"
          + (f", {action} on {focus_box}" if action else "") + ") -> reading…",
          flush=True)
    t0 = time.time()
    # the Smriti app's choices: reader / writer model, profile, custom prompt
    profile, extra, route = apply_settings(cfg_ai, tablet_settings(host))
    print(f"[app] read={route['reader']} write={route['escalate'] or route['writer']} "
          f"profile={profile} context={route['context']}"
          + (" +prompt" if extra else "") + (" ESCALATED" if route["escalate"] else ""),
          flush=True)
    # optional line-OCR hint (scribe) — never trusted over the image
    text_in = ""
    if strokes:
        buf = io.BytesIO()
        render_strokes(strokes, crop=True).save(buf, format="PNG")
        lines = scribe_ocr(buf.getvalue())
        text_in = " ".join(l for l in (lines or []) if l.strip()).strip()
    # placement facts from the real (validated) page
    if shot is None:
        shot = _shot()
    avail = 1872
    if shot is not None:
        bands = free_bands(shot)
        avail = max((y1 - y0 for y0, y1 in bands), default=200)
    lines = max(1, avail // 90)
    # Navy scrolls by hand (injected scrolls don't work); what doesn't fit
    # stays in the log. Small drawings sit two per row.
    room = (f"About {avail}px of empty space is visible below the writing "
            f"(~{lines} handwritten lines). Keep prose short (at most 4 lines); "
            f"up to 3 small equations or drawings when asked for (they are "
            f"placed two per row). What does not fit stays in the log. Never "
            f"refuse or say the page is full. ")
    rules = ("Maths, circuits and graphs only inside $$...$$ (typeset onto "
             "the paper, never show LaTeX source in prose); freeform sketches "
             "as one <svg> block.")
    # STAGE 1 — read: focus layer (new strokes / selection, cleaned, scaled
    # up) + overview layer (whole screen) -> structured reading
    from PIL import Image
    png, shot = _page_context(strokes, shot, hide=greet)
    ctx = " ".join(m["content"] for m in history[-4:]
                   if isinstance(m.get("content"), str))
    shot_info = getattr(shot, "info", {}) if shot is not None else {}
    if text_in:
        ctx += f" (line-OCR guess of the new writing: {text_in})"
    if graphs_context(ses):
        ctx += " " + graphs_context(ses)
    occ, free = None, []
    if shot is not None:                           # notebook -> page -> screen
        assert isinstance(shot, Image.Image)
        import place
        import vectors
        from screen import stroke_mask
        mask = stroke_mask(shot)                   # resolves the frame's vectors
        occ = place.occupancy(mask)
        free = place.free_rects(occ)
        ctx += " " + vectors.context(shot.info.get("view"), shot.info.get("layers"))
    _turn_start(shot, strokes, host)
    region_view = _TURN.get("region_view")
    # Navy's live lasso selection: read with this turn's registered offset
    selected = selection.read_selection(qml_eval, host, view=region_view)
    selection_pick = selection.precedence(selected, None)
    from_selection = False
    if selected is not None:
        print(f"[selection] {selected.count} items box={selected.screen_box or selected.page_box} "
              f"via {selected.source}", flush=True)
        if focus_box is None and selected.screen_box:   # explicit /focus still wins
            focus_box, from_selection = selected.screen_box, True
    if notebook_context(ses):
        ctx += " " + notebook_context(ses)
    # SPP: who inked what, where — so 'this graph', 'above', an arrow resolve
    # to a region id (skill/smriti/references/protocol.md). Semantic regions
    # use page coordinates and persist only while this page stays open.
    monke_regions = _monke_regions(ses, shot, region_view) if shot is not None else []
    page_map = "" if shot is None else protocol.page_map(
        region_view, shot.info.get("layers"), strokes,
        monke_regions, free_bands(shot), focus_box, free_rects=free)
    semantic, prompt_page, region_block = [], None, ""
    if region_view:                                # the selection is a region, not the prompt
        prompt_screen = (from_selection and harness.strokes_box(strokes)) or focus_box \
            or harness.strokes_box(strokes)
        prompt_page = regions.to_page_box(prompt_screen, region_view) if prompt_screen else None
        monke_page = [dict(m, box=regions.to_page_box(m["box"], region_view))
                      for m in monke_regions if m.get("box")]
        _TURN["region_turn"] = _TURN.get("region_turn", 0) + 1
        semantic = regions.build(regions.navy_strokes(shot.info.get("layers") if shot else None), monke_page,
                                 prior=_TURN.get("regions"), turn=_TURN["region_turn"],
                                 exclude_box=prompt_page)
        _TURN["regions"] = semantic
        region_block = regions.context_block(semantic, focus=prompt_page, budget_chars=699)
    page = page_map + ("\n" + region_block if region_block else "")
    page_regions = protocol.boxes(page_map)
    if region_view:
        page_regions.update({rid: regions.to_screen_box(box, region_view)
                             for rid, box in regions.boxes(semantic).items()})
    marks = None
    if page and cfg_ai.get("som", True):
        marks = ({k: v for k, v in page_regions.items() if not k.startswith("E")
                  and v[0] < CANVAS_W and v[1] < CANVAS_H and v[2] > 0 and v[3] > 0}, free)
    focus, overview = harness.layers(Image.open(io.BytesIO(png)),
                                     focus_box or harness.strokes_box(strokes), marks=marks)
    selection_block = ""
    if selected is not None:                       # focus = the selection crop, cleaned + scaled
        selection_block = selection.grounding_block(
            selected, selection.strokes_in(selected, shot_info.get("layers") or {}),
            harness.png(focus) if from_selection else None)
        page += "\n" + selection_block
    merged_used = not merged_fallback(route, action)
    merged_reply, merged_streamer = "", None
    first_ink = [None]
    if route.get("turn") == "merged" and not merged_used:
        print(f"[turn] merged -> split: {merged_fallback(route, action)}", flush=True)
    _set_stage("reading")
    try:
        if merged_used:
            y0 = _reply_top(floor, strokes, focus_box, shot)
            if STREAM and not input_fade:
                merged_streamer = _Streamer(strokes, style, y0, bottom, host, step,
                                            greet, ses.log_block,
                                            _writer_chat(route, ses, host, "repair"),
                                            page_regions, occ, floor)
            reading_seen = {}
            def stop_merged(r):
                reading_seen.update(r)
                return render_plan.route(r) != "diagram" and (
                    r["kind"] == "graph" or r["intent"] in ("plot", "refine")
                    or profile == "graph")

            def ink_merged(tail):
                if merged_streamer is None or (confirm and harness.needs_confirm(reading_seen, action)):
                    return False
                if render_plan.route(reading_seen) == "diagram":
                    return False             # held: the visual gate checks the whole reply
                stopped = merged_streamer.feed(tail)
                if first_ink[0] is None and merged_streamer.started:
                    first_ink[0] = time.time()
                    print(f"[turn] merged: first-ink {first_ink[0] - t0:.1f}s", flush=True)
                return stopped

            def merged_chat(messages, on_text=None):
                return cloud_chat(route["reader"], ses, host, "turn",
                                  on_text=on_text)(messages)
            reading, merged_reply = harness.read_and_reply(
                focus, overview, merged_chat, action=action, context=ctx, profile=profile,
                page=page, room=room, rules=rules, page_log=page_log(ses), extra=extra,
                on_reply=ink_merged, stop_after=stop_merged)
        else:
            # streamed; stops the moment the reading's JSON is complete (the
            # reader used to keep going with a whole solution: 1078 tokens, 24 s)
            read_chat = cloud_chat(route["reader"], ses, host, "read",
                                   on_text=harness.json_complete)
            reading = harness.read(focus, overview, read_chat, action, ctx, profile, page)
    except Exception as e:
        _set_stage("error", str(e))
        _fail("read", f"[read] AI unreachable: {str(e)[:120]}")
        return floor
    print(f"read ({time.time() - t0:.1f}s): {reading['kind']} | "
          f"{harness.describe(reading)}", flush=True)
    rid, conf, why = regions.resolve(reading.get("text", ""), semantic,
                                     last_prompt_bbox=prompt_page)
    if rid and conf >= .7 and reading.get("target", "F") in ("", "F"):
        target = next(r for r in semantic if r.id == rid)
        reading = dict(reading, target=rid, target_kind=target.kind,
                       target_desc=target.desc)
        print(f"[regions] target {rid} {target.kind} ({conf:.2f}: {why})", flush=True)
    elif why.startswith("ambiguous"):
        print(f"[regions] ambiguous ({conf:.2f}: {why})", flush=True)
    if selection_pick["winner"] == "selection":
        reading = dict(reading, target="selection", target_kind="", target_desc=selection_pick["reason"])
    if reading.get("target", "").startswith("R"):
        semantic = regions.hint(semantic, reading["target"], reading.get("target_kind"),
                                reading.get("target_desc"))
        _TURN["regions"] = semantic
        region_block = regions.context_block(semantic, focus=reading["target"], budget_chars=699)
        page = page_map + "\n" + region_block
    is_prompt, reading = harness.strip_prompt(reading)
    if (is_prompt and re.search(r"\b(refine|neaten|clean)\b", reading.get("text", ""), re.I)):
        reading = dict(reading, intent="refine")
    _TURN["prompt"] = strokes if is_prompt else None
    if confirm and harness.needs_confirm(reading, action):
        _set_stage("confirming")
        model_reading = dict(reading)
        reading = _confirm(reading, focus, host)
        ses.save_train(focus, model_reading, reading, profile,
                       route["reader"],
                       overview=overview, request=dict(harness.LAST_REQUEST),
                       action=action)
        if reading is None:
            ses.log_turn("Navy", "(discarded at confirm)")
            return floor
        if merged_used and reading != model_reading:
            print("[turn] merged -> split: confirm edited", flush=True)
            merged_used = False
        edited_prompt, reading = harness.strip_prompt(reading)
        if edited_prompt:
            _TURN["prompt"] = strokes
    _set_stage("thinking")
    # bug #4: a flow / algorithm / block / phasor ask is the DIAGRAM route —
    # not the math graph route, not prose. Its reply is held off the streamer
    # so a prose-only answer is swapped for a drawing before anything inks.
    diagram = render_plan.route(reading) == "diagram"
    if diagram:
        print("[diagram] visual request -> sketch writer + visual gate", flush=True)
        reading = dict(reading, kind="sketch")
    # Refine is geometry first: no writer model when code explains enough ink.
    if reading.get("intent") == "refine":
        if greet:
            fade_greeting(greet, host)
            greet = None
            shot = _shot()
            if shot is not None:
                import place
                from screen import stroke_mask
                occ = place.occupancy(stroke_mask(shot))
        ys = _refine(reading, page, shot, ses, host, step, floor, bottom, occ, region_view)
        if ys is not None:
            ses.log_turn("Navy", harness.describe(reading))
            return max(floor, ys)
        if merged_used:
            print("[turn] merged -> split: refine code fallback", flush=True)
            merged_used = False
    # plots are Python's job, not the model's: 'y = ...' readings are drawn
    # deterministically (new graph or overlay on the last one, in colour)
    if not diagram and (reading["kind"] == "graph" or action == "plot"
                        or reading["intent"] == "plot" or profile == "graph"):
        if greet:
            # BEFORE plotting: erasing the greeting box afterwards wiped the
            # part of the new graph that landed there (device, 2026-09-26)
            fade_greeting(greet, host)
            greet = None
            shot = _shot()                        # freed space is real now
        if strokes and not _same_page(strokes):
            print("[page] question no longer on screen — plot NOT inked "
                  "(content safety)", flush=True)
            return floor
        ys = _plot(reading, ses, host, _reply_top(floor, strokes, focus_box, shot),
                   bottom, step, shot)                # the carried floor is stale after a scroll
        if ys is not None:
            ses.log_turn("Navy", harness.describe(reading))
            _TURN["inked"] = True
            return max(floor, ys)
    # STAGE 2 — respond from the confirmed reading (text-only, cheap), then
    # compile-check every $$ block and let the model repair a failing one
    pl = page_log(ses) if ses is not None else ""   # 'this page', 'continue', 'above'
    ask = harness.build_writer_prompt(reading, action, room, rules, profile,
                                      page=page, page_log=pl)
    prompt = harness.build_writer_prompt(reading, action, room, rules, profile,
                                         page_log=pl)
    if diagram:
        ask += "\n" + render_plan.DIAGRAM_ASK
    # live: the writer streams, and each finished line inks as it lands
    # (input_fade keeps the whole-reply path)
    streamer = merged_streamer if merged_used else None
    y0 = _reply_top(floor, strokes, focus_box, shot)
    # little room below the question is the streamer's job too: it waits at
    # the page bottom for Navy's scroll (the whole-reply path printed NO ROOM)
    if not merged_used and STREAM and not input_fade and not diagram:
        streamer = _Streamer(strokes, style, y0, bottom, host, step,
                             greet, ses.log_block,
                             _writer_chat(route, ses, host, "repair"),
                             page_regions, occ, floor, ses=ses)
    if merged_used:
        raw = merged_reply
        if streamer and confirm and harness.needs_confirm(reading, action) and raw:
            streamer.feed(raw)
            if streamer.started:
                print(f"[turn] merged: first-ink {time.time() - t0:.1f}s", flush=True)
    else:
        raw = _write(ask, ses, extra, route, reading, profile, host,
                     on_text=streamer.feed if streamer else None)
    # ChatGPT plan mode: a JSON plan is logged, kept in history and (whole-
    # reply path) inked in the text grammar; the streamer inked it from JSON
    planned = plan_schema.is_plan(raw)
    shown = plan_schema.as_text(raw, streamer.taken0 if streamer else _graphs(ses)) \
        if planned else raw
    # SPP directive lines are for the daemon: off the reply BEFORE it inks
    # (the streamer skipped them unit by unit; `raw` keeps its offsets valid)
    directives, _ = protocol.parse_directives(shown)
    if region_view and (target := regions.target(shown, semantic)):
        page_regions[target[0]] = regions.to_screen_box(target[1], region_view)
    text = _spp_directives(shown, page, semantic)
    # A short merged reply may have no newline. Keep its streamer alive so
    # finish() flushes that final unit and records the actual first ink.
    if streamer and not diagram and (streamer.started or streamer.blocked or (merged_used and raw)):
        text = harness.to_ascii(harness.clean_reply(text)).strip()
        raw = harness.to_ascii(harness.clean_reply(raw)).strip()
    else:
        streamer = None                  # nothing inked yet: whole-reply path
        text = harness.clean_reply(text)
        if not planned:                  # plan mode: deterministic fallback only, no model repair
            text = harness.repair_latex(text, _writer_chat(route, ses, host, "repair"))
        text = harness.to_ascii(text).strip()
        if diagram:                          # mandatory visual artifact
            text, why = render_plan.enforce_visual(reading, text)
            if why:
                print(f"[diagram] {why}", flush=True)
    history += [{"role": "user", "content": prompt},
                {"role": "assistant", "content": text}]
    ses.log_turn("Navy", harness.describe(reading))
    ses.log_turn("Monke", text)
    print(f"monke ({time.time() - t0:.1f}s): {text}", flush=True)
    if streamer:
        if streamer.blocked:
            return floor
        y = streamer.finish(raw)
        if merged_used and first_ink[0] is None and streamer.started:
            first_ink[0] = time.time()
            print(f"[turn] merged: first-ink {first_ink[0] - t0:.1f}s", flush=True)
        _TURN["inked"] = bool(streamer.inked)
        return max(floor, y)
    # before any fade/erase: they change the page this check reads
    if strokes and not _same_page(strokes):
        print("[page] question no longer on screen — reply NOT inked, kept "
              "in the log (content safety)", flush=True)
        return floor
    if greet:
        # the greeting served its purpose — fade it before the answer lands
        fade_greeting(greet, host)
    if input_fade and strokes:
        # user opted in: the question fades too, the answer takes its place
        erase_box(strokes, host)
        shot = _shot()                      # freed space is real now

    # ponytail: fade/riddle mode DISABLED pending visual verification on
    # device — erase sweep width/completeness unconfirmed. Re-enable by
    # uncommenting; config [monke] fade/fade_hold are already plumbed.
    # if fade:
    #     # riddle mode: your words dissolve, the answer appears in their
    #     # place, lingers, then dissolves too — page returns clean
    #     erase_box(strokes, host)
    #     y = max(80, min(py for s in strokes for _, py in s))
    #     reply = render(text, style, x=100, y=y)
    #     _ink(reply, style.get("pressure", 2400), step, host)
    #     time.sleep(fade_hold)
    #     erase_box(reply, host)
    #     return floor

    # keep mode: place below everything inked this session, not just this
    # commit — else writing above an old reply would overwrite it. If that
    # overflows, _place retries inside a free band from the screenshot.
    # input_fade: the question was just erased — answer starts where it was.
    if input_fade and strokes:
        y = max(150, min(py for s in strokes for _, py in s))
    else:
        y = _reply_top(floor, strokes, focus_box, shot)
    reply = _place(text, style, y, shot, bottom, log=ses.log_block)
    if reply is None and not _layout(text, style, y, log=lambda *a: None):
        # sim 2026-10-04: an empty reply, or one whose only drawing failed to
        # compile, was reported as NO ROOM ("scroll or turn the page")
        _fail("empty", "[reply] nothing to ink — the reply was empty or its drawing did not "
                       "render (source + error are in the log)")
        return floor
    if reply is None:
        # won't fit. Injected swipes don't move xochitl (run 13 measured
        # swipe up + finger drag; run 16: 3 x swipe left = 17 s, no page) —
        # Navy scrolls or turns, then asks again. The reply is in the log.
        print("NO ROOM — reply not inked (in the log). Scroll or turn to a "
              "fresh page by hand, then ask again.", flush=True)
        return floor
    directive = next((d for d in directives if d.get("op") in ("place", "refine")), None)
    if directive and occ is not None:
        moved, spot = _directive_place(reply, directive, page_regions, occ, floor, bottom)
        current = _shot()
        if moved is not None and spot is not None and not (
                current is not None and _band_busy(current, moved, [])):
            reply = moved
            print(f"[place] {spot.why} scale={spot.k:g}", flush=True)
        else:
            print("[place] directive blocked — normal placement", flush=True)
    _ink(reply, style.get("pressure", 2400), step, host)
    _TURN["inked"] = True
    return max(floor, max((py for s in reply for _, py in s), default=floor))


_DRAWINGS: dict = {}          # (kind, source) -> strokes at origin (100, 0)


def _drawing(kind: str, src: str) -> list:
    """Compile a $$/svg block ONCE per reply source (fit probing lays the
    same reply out at several sizes). Raises like the renderers do."""
    key = (kind, src)
    if key not in _DRAWINGS:
        try:
            if kind == "tex":
                from tex import tex_to_strokes
                # figures default narrower than the page: a full-width
                # circuit filled a whole page in one turn (Sonnet run)
                fig = "circuitikz" in src or "tikzpicture" in src
                _DRAWINGS[key] = tex_to_strokes(src, x=100, y=0,
                                                max_w=850 if fig else 1200)
            else:
                from svg import svg_to_strokes
                _DRAWINGS[key] = svg_to_strokes(src, x=100, y=0, max_w=1200)
        except Exception as e:           # a broken block fails ONCE, not per size
            _DRAWINGS[key] = e
        if len(_DRAWINGS) > 64:
            _DRAWINGS.pop(next(iter(_DRAWINGS)))
    if isinstance(_DRAWINGS[key], Exception):
        raise _DRAWINGS[key]
    return _DRAWINGS[key]


_INLINE_WORDS = {r"\pi": "pi", r"\Omega": "ohm", r"\omega": "omega", r"\mu": "u",
                 r"\cdot": "*", r"\times": "x", r"\tau": "tau", r"\theta": "theta",
                 r"\alpha": "alpha", r"\beta": "beta", r"\lambda": "lambda",
                 r"\Delta": "delta", r"\phi": "phi", r"\approx": "~", r"\,": " ",
                 r"\infty": "infinity"}


def _inline_plain(math: str, frac: bool = False) -> str:
    """'$f_c$' -> 'fc', '$V_{in}$' -> 'Vin', '$2\\pi RC$' -> '2pi RC'.
    '' when the expression needs real typesetting (fractions, roots,
    sums, matrices...). frac=True (mid-sentence use): simple \\frac and
    \\sqrt read as 'a/b', 'sqrt(x)' instead."""
    if frac:
        for _ in range(4):                   # innermost first: \frac{1}{\sqrt{2}}
            math = re.sub(r"\\sqrt\{([^{}\\]*)\}", r"sqrt(\1)", math)
            math = re.sub(r"\\d?frac\{([^{}\\]*)\}\{([^{}\\]*)\}",
                          lambda m: "/".join(f"({p})" if re.search(r"[+\-]", p) else p
                                             for p in m.groups()), math)
    if re.search(r"\\(frac|dfrac|sqrt|sum|int|prod|lim|begin|over|binom|vec|hat|bar)", math):
        return ""
    s = re.sub(r"\\text\{([^{}]*)\}", r"\1", math)
    for k, v in _INLINE_WORDS.items():
        s = s.replace(k, v)
    s = re.sub(r"[_{}]", "", s)
    if "\\" in s or len(s) > 24:
        return ""
    return s.strip()


def _shift(strokes: list, s: float, x0: int, y0: int) -> list:
    """Scale by s about the block's left edge x0, then drop it at y0."""
    return [Tinted([(int(x0 + (x - x0) * s), int(y0 + py * s)) for x, py in st],
                   getattr(st, "color", 0))
            for st in strokes]


def _at(strokes: list, dy: int, dx: int = 0) -> list:
    """Strokes moved down by dy (right by dx), each keeping its class
    (Drawn = thin pen) and pen colour."""
    return [type(s)([(px + dx, py + dy) for px, py in s], getattr(s, "color", 0))
            if isinstance(s, (Drawn, Tinted)) else [(px + dx, py + dy) for px, py in s]
            for s in strokes]


def _box(strokes: list) -> tuple[int, int, int, int]:
    """(x, y, w, h) screen box of strokes."""
    xs = [px for s in strokes for px, _ in s]
    ys = [py for s in strokes for _, py in s]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _block_layout(b, style, y: int, log=None) -> tuple:
    """One plan Block -> (strokes, graph.Graph | None). A GraphSpec graph
    goes to plots.render (host/plots.py) when it imports and validates:
    strokes per curve pen, the Graph is what ses.graphs keeps. Otherwise
    (and for every other kind) the text-grammar executors via _layout, the
    role colour kept; a graph then draws as sampled pgfplots."""
    log = log or _log_block
    if b.kind == "graph" and b.spec is not None:
        g, why = render_plan.plot_graph(b)
        if g is not None:
            try:
                g.x, g.y = 100, y + 20
                g.max_w = int(850 * float(style.get("_scale", 1.0)))   # = _drawing's figures
                st = [Drawn(t, pen) for part, pen in render_plan.graph_batches(g)
                      for t in part if len(t) > 1]
                if st:
                    log("graph", b.src)
                    return st, g
                why = "nothing to ink"
            except Exception as e:
                why = f"render failed: {str(e)[:120]}"
        print(f"[plot] {b.label}: {why} - sampled pgfplots instead", flush=True)
    text = harness.to_ascii(harness.clean_reply(plan_schema.block_text(b))).strip()
    return (_layout(text, style, y, log=log, color=b.color) if text else []), None


def _layout(text: str, style, y: int, log=None, color: int | None = None) -> list:
    """Reply → strokes. Prose via the stroke font; $$...$$ blocks typeset
    through LaTeX (maths/circuitikz) and stacked between the prose.
    log(kind, code) receives every block's raw source (session exporter);
    defaults to the flat blocks.log. color = a plan block's role pen
    (answer red, highlight blue) for its prose and maths; None = as written."""
    log = log or _log_block
    segs = _segments(text)
    out = []
    s = float(style.get("_scale", 1.0))
    for kind, part in segs:
        part = part.strip()
        if not part:
            continue
        if kind == "tex":
            log("tex", part)
            try:
                # curve roles + dash per \addplot, answers in red (render_plan)
                body = render_plan.tex_source(part, color or None)
                seg = _shift(_drawing("tex", body), s, 100, y + 20)
            except Exception as e:
                _fail(part, f"[tex] render failed: {str(e)[:200]}")
                # NEVER ink LaTeX source — a failed block is broken LaTeX,
                # so its source is code, not readable text. Ink it only when
                # it carries no commands/braces (plain text mis-wrapped in
                # $$); otherwise a short note. Source is in blocks.log.
                # Navy 2026-09-26: no "(did not render)" note on the page —
                # a failed drawing is skipped (source + error stay in the log)
                seg = render(part, style, x=100, y=y) \
                    if not ("\\" in part or "{" in part or "$" in part) else []
        elif kind == "svg":
            log("svg", part)
            try:
                seg = _shift(_drawing("svg", part), s, 100, y + 20)
                if not seg:
                    raise ValueError("no drawable elements")
            except Exception as e:
                _fail(part, f"[svg] render failed: {str(e)[:200]}")
                seg = []                 # no failure note on Navy's page
        else:
            seg = render(part, style, x=100, y=y)
            if color:
                seg = [Tinted(t, color) for t in seg]
        if seg:
            if kind != "prose" or s < _THIN_BELOW:
                seg = [Drawn(t) for t in seg]      # thin pen at ink time
            out += seg
            y = max(py for s in seg for _, py in s) + 40
    return out


def _segments(text: str) -> list[tuple[str, str]]:
    """Reply text -> [(prose|tex|svg, part)]. A short $$..$$ INSIDE a sentence
    (prose on its line) reads as plain text: as a block it took a whole line,
    one answer filled a page or shrank to 0.42x (device 2026-09-30)."""
    def inline(m: re.Match) -> str:
        before = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        after = text[m.end():(text.find("\n", m.end()) + 1 or len(text) + 1) - 1]
        if (before.strip() or after.strip()) and "\\begin" not in m.group(1):
            return _inline_plain(m.group(1).strip(), frac=True) or m.group(0)
        return m.group(0)
    text = re.sub(r"\$\$(.+?)\$\$", inline, text, flags=re.S)
    segs: list[tuple[str, str]] = []
    for k, chunk in enumerate(re.split(r"(<svg\b.*?</svg>)", text,
                                       flags=re.S | re.I)):
        if k % 2:
            segs.append(("svg", chunk))
            continue
        for i, part in enumerate(re.split(r"\$\$(.+?)\$\$", chunk, flags=re.S)):
            if i % 2:
                segs.append(("tex", part))
            else:
                # simple inline symbols ($f_c$, $V_{in}$) stay IN the sentence
                # as plain text — promoting them to blocks split one sentence
                # into three lines (device, Sonnet run 2026-09-26)
                part = re.sub(r"(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)",
                              lambda m: _inline_plain(m.group(1)) or m.group(0), part)
                # complex inline math would be inked as raw code by the stroke
                # font — typeset it as a block instead. (only prose is scanned;
                # inner $ in circuitikz labels is safe)
                for j, sub in enumerate(
                        re.split(r"(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)", part)):
                    segs.append(("tex" if j % 2 else "prose", sub))
    return segs


def _log_block(kind: str, code: str) -> None:
    """Maths/diagram source is kept separately (~/.config/smriti/blocks.log)
    — the page only ever shows the rendered preview."""
    try:
        _FLOOR_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_FLOOR_FILE.parent / "blocks.log", "a",
                  encoding="utf-8") as f:
            f.write(f"\n--- {kind} {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n"
                    f"{code.strip()}\n")
    except OSError:
        pass


def _selfcheck() -> None:
    """`python host/monke.py selfcheck` — guards the two placement rules
    that decide what reaches the page: LaTeX source is NEVER inked, and
    inline $..$ is promoted to a typeset block."""
    import tex
    from ink import load_styles
    st = load_styles()["cursive"]
    orig, tex._pdflatex = tex._pdflatex, lambda: "no-such-binary-xyz"
    try:
        # a broken/uncompilable tex block inks NOTHING — never its source,
        # and no "(did not render)" note on the page (Navy 2026-09-26)
        assert _layout(r"$$Z = R + j\left(\omega L\right)$$", st, 100) == []
        # plain text mistakenly wrapped in $$ stays readable
        assert _layout(r"$$hello there$$", st, 100)
    finally:
        tex._pdflatex = orig
    # inline $..$ promotion, circuitikz inner-$ preserved
    import re
    RX = r"(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)"
    c = r"$$\begin{circuitikz}\draw to[R, l=$R_1$];\end{circuitikz}$$"
    assert re.sub(RX, r"$$\1$$", "use $x=1$ here") == "use $$x=1$$ here"
    # adaptive placement: a reply that overflows a tight gap is scaled to fit,
    # and the same reply uses more height when the page is roomy
    long = ("A fairly long monke reply that will not fit at full size in a "
            "short vertical gap, so it must scale down to fit the space.")
    tight = _place(long, st, 100, None, 320)
    assert tight and max(py for s in tight for _, py in s) <= 320, "tight fit"
    roomy = _place(long, st, 100, None, 1780)
    assert (max(py for s in roomy for _, py in s)
            > max(py for s in tight for _, py in s)), "roomy uses full size"
    print("monke selfcheck ok")


def claude_status() -> None:
    """Where Smriti's Claude login lives, who it is, when it expires, what it
    has spent — never prints a token (only a short hash to tell logins
    apart). Revoke: `claude auth logout` with the same CLAUDE_CONFIG_DIR, or
    delete the folder (Smriti goes local-only on the next turn)."""
    import hashlib
    import os
    from datetime import datetime
    from oracle import claude_home
    CLAUDE_CFG.update(_load_cfg().get("claude", {}))
    home = claude_home(CLAUDE_CFG)
    cred = home / ".credentials.json"
    print(f"profile dir : {home}")
    print(f"on drive    : {str(home)[:2].upper()}  (must not be C:)")
    if not cred.exists():
        print("login       : NONE — Claude disabled, Smriti stays local")
    else:
        try:
            oa = json.loads(cred.read_text()).get("claudeAiOauth", {})
        except (OSError, ValueError):
            oa = {}
        fp = hashlib.sha256(str(oa.get("refreshToken", "")).encode()).hexdigest()[:8]
        exp = oa.get("expiresAt")
        print(f"login       : present  fingerprint {fp}  "
              f"plan {oa.get('subscriptionType', '?')}  scopes {','.join(oa.get('scopes', []))}")
        print(f"file written: {datetime.fromtimestamp(cred.stat().st_mtime):%Y-%m-%d %H:%M}")
        if exp:
            print(f"access token: refreshes itself; current one expires "
                  f"{datetime.fromtimestamp(int(exp) / 1000):%Y-%m-%d %H:%M}")
    u = _usage()
    print(f"today       : {u['calls']} calls, "
          f"{u['in']} in / {u['out']} out tokens")
    rows = [l for p in _SESSIONS.glob("*/usage.csv") for l in p.read_text().splitlines()]
    if rows:
        last = max(rows)
        print(f"all-time    : {len(rows)} calls · last {last.split(',')[0]} ({last.split(',')[2]})")
    print("revoke      : $env:CLAUDE_CONFIG_DIR=\"" + str(home) + "\"; claude auth logout")
    print("kill switch : delete the profile dir — next turn is local-only")


if __name__ == "__main__":
    _cmd = __import__("sys").argv[1] if len(__import__("sys").argv) > 1 else ""
    if _cmd == "selfcheck":
        _selfcheck()
    elif _cmd == "claude-status":
        claude_status()
    elif _cmd == "link-test":
        raise SystemExit(0 if link_test() else 1)
    elif _cmd == "doctor":
        doctor(__import__("sys").argv[2] if len(__import__("sys").argv) > 2 else None)
    else:
        run()
