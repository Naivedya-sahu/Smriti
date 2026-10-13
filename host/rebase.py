"""rebase.py — scroll-aware continuation (pure functions, no I/O).

A reply is planned once as ordered ink units anchored in PAGE coordinates
(x, y, w, h). Whatever the view does afterwards — Navy scrolls between
parts, the reply waits at the screen bottom for his scroll, he writes into
the band a unit was going to use — every pending unit is mapped to the view
on screen NOW, deterministically:

    screen = page + (ox, oy)            (vectors.to_screen, offset branch)

(ox, oy) is the registered offset when known (vectors.register /
monke._prompt_offset), else from the view fields: oy = top (device
2026-09-30 22:30 and the sim: y maps as y + top), ox = -right (sim and the
unscrolled device; on a scrolled device page x is NOT derivable from the
fields — right -468 but registered ox 935 — so x_trusted is False then).
vectors.to_screen's field fallback (y - top) is not used: it is wrong once
the page is scrolled (run 12/15).

Another page id = abort, never ink onto another page.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field, replace

import numpy as np

import layers
import vectors

W, H = vectors.CANVAS_W, vectors.CANVAS_H
BOTTOM = 1780                          # monke page_bottom default: last usable screen row
OWN = (layers.MONKE, layers.GREET)     # Monke's own layers never push a unit

VISIBLE, PARTLY, BELOW, ABOVE = "visible", "partly-visible", "offscreen-below", "offscreen-above"


# ---------------------------------------------------------------- view

@dataclass(frozen=True)
class ViewTransform:
    page_id: str | None
    ox: float
    oy: float
    scale: float = 1.0
    x_trusted: bool = True             # False: ox guessed from view fields on a scrolled page

    @classmethod
    def from_view(cls, v: dict, offset: tuple[float, float] | None = None) -> "ViewTransform":
        """v = vectors.view() dict (may carry vectors' 'ox'/'oy');
        offset = a registered (ox, oy), which wins over everything."""
        pid = v.get("pageId")
        if offset is None and "ox" in v:
            offset = (v["ox"], v["oy"])
        s = float(v.get("scale") or 1)
        if offset is not None:
            return cls(pid, float(offset[0]), float(offset[1]), s, True)
        top, right = float(v.get("top") or 0), float(v.get("right") or 0)
        return cls(pid, -right, top, s, top == 0)

    def page_to_screen(self, x: float, y: float) -> tuple[int, int]:
        return round((x + self.ox) * self.scale), round((y + self.oy) * self.scale)

    def screen_to_page(self, x: float, y: float) -> tuple[float, float]:
        return x / self.scale - self.ox, y / self.scale - self.oy

    def stroke_to_screen(self, stroke) -> list[tuple[int, int]]:
        """Same registered-offset affine and ints as vectors.to_screen."""
        return vectors.to_screen(stroke, {"ox": self.ox, "oy": self.oy, "scale": self.scale})

    def box_to_screen(self, x, y, w, h) -> tuple[int, int, int, int]:
        sx, sy = self.page_to_screen(x, y)
        return sx, sy, round(w * self.scale), round(h * self.scale)

    def visible_page_rect(self, screen_w: int = W, screen_h: int = H):
        """(x0, y0, x1, y1) of the page on screen, page coordinates."""
        x0, y0 = self.screen_to_page(0, 0)
        x1, y1 = self.screen_to_page(screen_w, screen_h)
        return x0, y0, x1, y1


def _transform(v) -> ViewTransform | None:
    if v is None or isinstance(v, ViewTransform):
        return v
    return ViewTransform.from_view(v) if v.get("pageId") else None


# ---------------------------------------------------------------- plan

@dataclass
class Unit:
    x: float                           # page coordinates, anchored once at plan time
    y: float
    w: float
    h: float
    done: bool = False
    tag: object = None                 # caller's handle (strokes, block label ...)


@dataclass
class Plan:
    page_id: str | None
    units: list[Unit] = field(default_factory=list)

    @classmethod
    def from_screen(cls, page_id, boxes, t: ViewTransform, tags=None) -> "Plan":
        """boxes = (x, y, w, h) screen px as laid out now -> page anchors."""
        out = []
        for i, (x, y, w, h) in enumerate(boxes):
            px, py = t.screen_to_page(x, y)
            out.append(Unit(px, py, w / t.scale, h / t.scale,
                            tag=tags[i] if tags else None))
        return cls(page_id, out)

    def pending(self) -> list[tuple[int, Unit]]:
        return [(i, u) for i, u in enumerate(self.units) if not u.done]

    def mark_done(self, i: int) -> None:
        self.units[i].done = True


@dataclass
class Placed:
    index: int
    page: tuple[float, float, float, float]
    screen: tuple[int, int, int, int]
    state: str


@dataclass
class RebaseResult:
    abort: bool
    reason: str = ""
    plan: Plan | None = None           # copy, with Navy-ink shifts applied
    units: list[Placed] = field(default_factory=list)   # pending units only, in order
    dx: float = 0.0                    # screen delta of the view (new - old)
    dy: float = 0.0
    shift: float = 0.0                 # page px the units from shift_from on moved down
    shift_from: int | None = None


def _state(s, bottom: float, screen_w: float) -> str:
    x, y, w, h = s
    if y >= bottom:
        return BELOW
    if y + h <= 0:
        return ABOVE
    if y >= 0 and y + h <= bottom and x >= 0 and x + w <= screen_w:
        return VISIBLE
    return PARTLY


def _ink_boxes(page_strokes) -> list[tuple[float, float, float, float]]:
    """Navy's stroke boxes (x0, y0, x1, y1), page coords, sorted by y0.
    page_strokes = vectors.parse() layers (Monke's layers skipped) or a
    plain list of strokes (all Navy's)."""
    if isinstance(page_strokes, dict):
        strokes = [s for name, ss in page_strokes.items() if name not in OWN for s in ss]
    else:
        strokes = list(page_strokes or [])
    out = []
    for s in strokes:
        if len(s):
            xs, ys = [p[0] for p in s], [p[1] for p in s]
            out.append((min(xs), min(ys), max(xs), max(ys)))
    return sorted(out, key=lambda b: b[1])


def _hit(a, b, pad: float) -> bool:
    return a[0] - pad <= b[2] and b[0] <= a[2] + pad and a[1] - pad <= b[3] and b[1] <= a[3] + pad


def page_turn(plan: Plan, new_view) -> RebaseResult:
    """Another page (or unreadable view) = abort; else a no-op result."""
    t = _transform(new_view)
    if t is None:
        return RebaseResult(True, "view unreadable")
    if t.page_id != plan.page_id:
        return RebaseResult(True, f"page changed {plan.page_id} -> {t.page_id}")
    return RebaseResult(False, plan=plan)


def rebase(plan: Plan, old_view, new_view, page_strokes, *, bottom: int = BOTTOM,
           screen_w: int = W, gap: float = 40, pad: float = 8,
           top: float | None = None) -> RebaseResult:
    """Pending units -> the new view. A unit Navy scrolled past (above
    screen row `top`) goes on at that row. Navy ink (not Monke's layers) in a
    pending unit's band pushes that unit and every later one down by the same
    amount (relative spacing kept) to `gap` px under that ink; repeated until
    no pending unit touches Navy ink. The input plan is not changed."""
    turned = page_turn(plan, new_view)
    if turned.abort:
        return turned
    t, t0 = _transform(new_view), _transform(old_view)
    assert t is not None                               # page_turn checked it
    out = Plan(plan.page_id, [replace(u) for u in plan.units])
    boxes = _ink_boxes(page_strokes)
    tops = [b[1] for b in boxes]
    tall = max((b[3] - b[1] for b in boxes), default=0)   # ink ending above y - tall can't hit
    shift, first = 0.0, None
    row = t.screen_to_page(0, top)[1] if top is not None else None
    for i, u in out.pending():
        u.y += shift
        if row is not None and u.y < row:
            shift += row - u.y
            u.y = row
            first = i if first is None else first
        while True:
            a = (u.x, u.y, u.x + u.w, u.y + u.h)
            lo = bisect.bisect_left(tops, a[1] - pad - tall)
            hi = bisect.bisect_right(tops, a[3] + pad)       # ink starting below the unit can't hit
            low = max((b[3] for b in boxes[lo:hi] if _hit(a, b, pad)), default=None)
            if low is None:
                break
            d = low + gap - u.y
            u.y += d
            shift += d
            first = i if first is None else first
    placed = []
    for i, u in out.pending():
        s = t.box_to_screen(u.x, u.y, u.w, u.h)
        placed.append(Placed(i, (u.x, u.y, u.w, u.h), s, _state(s, bottom, screen_w)))
    dx = (t.ox - t0.ox) * t.scale if t0 else 0.0
    dy = (t.oy - t0.oy) * t.scale if t0 else 0.0
    return RebaseResult(False, "", out, placed, dx, dy, shift, first)


# ---------------------------------------------------------------- continuation

@dataclass
class Continuation:
    decision: str                      # ink_now | wait_for_scroll | page_full | done | abort
    resume_y: float | None = None      # page y where the reply goes on
    index: int | None = None
    screen: tuple[int, int, int, int] | None = None
    direction: str | None = None       # wait_for_scroll: 'down' or 'up'
    reason: str = ""


def continuation(plan: Plan, view, page_height: float, bottom_margin: float, *,
                 screen_h: int = H, screen_w: int = W, screen_top: float = 0) -> Continuation:
    """What to do with the next pending unit in this view. The usable screen
    is rows screen_top .. screen_h - bottom_margin (1872 - 92 = monke's 1780);
    the page has room while the unit ends by page_height - bottom_margin. A
    unit taller than the usable screen is page_full: no scroll shows it whole."""
    t = _transform(view)
    if t is None or t.page_id != plan.page_id:
        return Continuation("abort", reason="page changed or view unreadable")
    todo = plan.pending()
    if not todo:
        return Continuation("done")
    i, u = todo[0]
    s = t.box_to_screen(u.x, u.y, u.w, u.h)
    if u.y + u.h > page_height - bottom_margin:
        return Continuation("page_full", u.y, i, s, reason="no room left on the page")
    if u.h * t.scale > screen_h - bottom_margin - screen_top:
        return Continuation("page_full", u.y, i, s,
                            reason=f"part {u.h:.0f} px tall is taller than the screen")
    st = _state(s, screen_h - bottom_margin, screen_w)
    if st == VISIBLE:
        return Continuation("ink_now", u.y, i, s)
    up = st == ABOVE or (st == PARTLY and s[1] < 0)
    return Continuation("wait_for_scroll", u.y, i, s, "up" if up else "down", st)


# ---------------------------------------------------------------- drift

def view_shift(detected, old_view, new_view, expected=None, observed=None, *,
               blind=()) -> tuple[int, int] | None:
    """detect_view_moved's (moved, dy, dx) -> the screen (dy, dx) to apply.
    Moved but no offset found (a long scroll leaves too little of the ink
    on screen): re-register y from the view fields (oy = top; a vertical
    scroll keeps x). With the expected points and the observed ink, the
    candidate (fields, image) where most of the ink is found wins; fields
    that can't be checked (ink off screen) still count. None = unknown:
    re-anchor under the visible ink. blind = screen boxes the observed ink
    can't show (screen.UI_BOXES)."""
    moved, dy, dx = detected
    if not moved:
        return 0, 0
    try:
        d = round((float(new_view["top"]) - float(old_view["top"])) * float(new_view.get("scale") or 1))
    except (TypeError, KeyError, ValueError):
        d = None
    if expected is None:
        if dy is not None:             # pixel medians are +-1 px: exact fields win when they agree
            return (d if d is not None and abs(d - dy) <= 3 else round(dy)), round(dx)
        return (d, 0) if d else None
    if d is not None and dy is not None and abs(d - dy) <= 3:
        return d, round(dx)            # they agree: the exact fields, not a +-1 px median
    sat = _sat(observed)
    best, score = None, 0.0
    for c, field in (((d, 0), True), ((dy, dx), False)):
        if c[0] is None or (field and not d):
            continue
        c = (round(c[0]), round(c[1]))
        s = _support(sat, expected, c[0], c[1], blind=blind)
        s = (0.5 if field else 0.0) if s is None else s
        if s > score:
            best, score = c, s
    return best if score >= 0.5 else None


def _sat(observed) -> np.ndarray:
    """Summed-area table of the observed ink (bool mask or screen points)."""
    a = np.asarray(observed)
    if not (a.dtype == bool and a.ndim == 2):
        p = _points(observed).round().astype(int)
        a = np.zeros((H, W), bool)
        ok = (p[:, 0] >= 0) & (p[:, 0] < W) & (p[:, 1] >= 0) & (p[:, 1] < H)
        a[p[ok, 1], p[ok, 0]] = True
    return np.pad(a.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)


def _support(sat, expected, dy, dx, r: float = 3, min_pts: int = 10,
             blind=()) -> float | None:
    e = np.asarray(expected, float).reshape(-1, 2) + (dx, dy)
    h, w = sat.shape[0] - 1, sat.shape[1] - 1
    e = e[(e[:, 0] >= 0) & (e[:, 0] < w) & (e[:, 1] >= 0) & (e[:, 1] < h)]
    for x0, y0, x1, y1 in blind:
        e = e[~((e[:, 0] >= x0) & (e[:, 0] < x1) & (e[:, 1] >= y0) & (e[:, 1] < y1))]
    if len(e) < min_pts:
        return None
    x, y, r = e[:, 0].round().astype(int), e[:, 1].round().astype(int), int(round(r))
    x0, x1 = np.clip(x - r, 0, w), np.clip(x + r + 1, 0, w)
    y0, y1 = np.clip(y - r, 0, h), np.clip(y + r + 1, 0, h)
    hits = sat[y1, x1] - sat[y0, x1] - sat[y1, x0] + sat[y0, x0]
    return float((hits > 0).mean())


def support(expected, observed, dy: float = 0, dx: float = 0, r: float = 3,
            blind=()) -> float | None:
    """Fraction of the expected screen points, moved by (dx, dy), with
    observed ink within r px. None = too few of them land on the screen
    (outside the blind boxes) to tell."""
    return _support(_sat(observed), expected, dy, dx, r, blind=blind)


def _points(obs) -> np.ndarray:
    a = np.asarray(obs)
    if a.dtype == bool and a.ndim == 2:
        ys, xs = np.nonzero(a)
        return np.stack([xs, ys], 1).astype(float)
    return a.reshape(-1, 2).astype(float)


def detect_view_moved(expected_screen_pts, observed_screen_ink, *, tol: float = 3,
                      radius: float = 4, min_support: float = 0.25, bin_px: int = 4,
                      max_exp: int = 300, max_obs: int = 3000, still: float = 0.6,
                      candidates: int = 24, blind=()):
    """Where did the ink Monke expects at expected_screen_pts go?
    observed_screen_ink = (M, 2) screen points or a bool ink mask.
    Every (observed - expected) pair votes for an offset; the densest 3x3
    bin block wins, then each expected point's nearest pair offset there is
    taken and the median of those is the estimate (robust to a partial
    redraw and to unrelated ink). >= `still` of the points on ink where
    they were = not moved. Returns (moved, est_dy, est_dx); moved
    with None estimates = the ink is not on screen at any one offset (page
    turn, unreadable)."""
    exp = np.asarray(expected_screen_pts, float).reshape(-1, 2)
    obs = _points(observed_screen_ink)
    if not len(exp) or not len(obs):
        return True, None, None
    sat = _sat(observed_screen_ink)
    if (_support(sat, exp, 0, 0, tol, blind=blind) or 0) >= still:
        return False, 0.0, 0.0                       # still in place, whatever else is on screen
    rng = np.random.default_rng(0)                   # deterministic subsample
    if len(exp) > max_exp:
        exp = exp[rng.choice(len(exp), max_exp, replace=False)]
    if len(obs) > max_obs:
        obs = obs[rng.choice(len(obs), max_obs, replace=False)]
    d = obs[None, :, :] - exp[:, None, :]             # (N, M, 2) pair offsets
    span = int(max(W, H) / bin_px) + 1
    q = np.clip(np.floor(d / bin_px).astype(int) + span, 0, 2 * span)
    n = 2 * span + 1
    hist = np.bincount((q[..., 1] * n + q[..., 0]).ravel(), minlength=n * n).reshape(n, n)
    sm = hist.copy()
    sm[1:] += hist[:-1]
    sm[:-1] += hist[1:]
    sm2 = sm.copy()
    sm2[:, 1:] += sm[:, :-1]
    sm2[:, :-1] += sm[:, 1:]
    # densest vote blocks can be other dense text (sim 2026-10-12): of the
    # top blocks, the one where most expected points find ink wins
    top = np.argpartition(sm2.ravel(), -candidates)[-candidates:]
    cells = np.stack(np.unravel_index(top, sm2.shape), 1)
    cents = (cells[:, ::-1] - span + 0.5) * bin_px          # (dx, dy)
    score = [(_support(sat, exp, c[1], c[0], radius + bin_px / 2, blind=blind) or 0, sm2[tuple(k)])
             for c, k in zip(cents, cells)]
    mode = cents[max(range(len(cents)), key=lambda i: score[i])]

    def matched(center, r):
        e = np.linalg.norm(d - center, axis=2)          # (N, M)
        j = e.argmin(1)
        ok = e[np.arange(len(exp)), j] <= r
        return d[np.arange(len(exp)), j][ok]

    m = matched(mode, 1.5 * bin_px + radius)
    if len(m) < min_support * len(exp):
        still = matched(np.zeros(2), radius)
        if len(still) >= min_support * len(exp):
            return False, 0.0, 0.0
        return True, None, None
    est = np.median(m, 0)
    m = matched(est, radius)                          # refine around the median
    if len(m):
        est = np.median(m, 0)
    edx, edy = float(est[0]), float(est[1])
    return bool(abs(edx) > tol or abs(edy) > tol), edy, edx
