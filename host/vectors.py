"""vectors.py — the notebook's own strokes (xochitl .rm v6) + the live view.

Pixels miss thin ink and can't tell whose ink it is (run 14: a reply
inked over a thin graph); the page file has every stroke, per layer.
xochitl writes it ~10 s after the last stroke while the page is open
(spike 2026-09-30, docs/research-rm-vectors.md), so it is unioned with
the screenshot, which still carries the newest ink (hybrid).

The live view — notebook, page, viewport edges — is one QML call. A
mapping that doesn't land on the screenshot's ink (agree() < 0.8, e.g. the
page file not flushed yet) is not used: pixels alone decide then.
"""

from __future__ import annotations

import io
import json
import logging

import numpy as np
from PIL import Image, ImageDraw

CANVAS_W, CANVAS_H = 1404, 1872
DOCS = "/home/root/.local/share/remarkable/xochitl"

# DeviceSceneView = eye.parent^3, DocumentView = its parent (fw 3.27 probe)
VIEW_JS = (r"(function(){var sv=smritiEyeUi.parent.parent.parent;var dv=sv.parent;"
           r"var d=dv.document;return JSON.stringify({doc:d.id,name:d.visibleName,"
           r"page:dv.currentPage,pages:dv.pageCount,pageId:dv.currentPageId,"
           r"scale:sv.viewScale,left:sv.leftViewportEdge,right:sv.rightViewportEdge,"
           r"top:sv.topViewportEdge});})()")

logging.getLogger("rmscene").setLevel(logging.ERROR)   # "newer format" note: harmless
_CACHE: dict = {}                                       # pageId -> (mtime, layers)


def view(host: str) -> dict | None:
    """{doc, name, page (0-based), pages, pageId, scale, left, right, top, ...}"""
    import monke
    out = monke.qml_eval(host, VIEW_JS)
    try:
        v = json.loads(out or "")
    except ValueError:
        return None
    return v if isinstance(v, dict) and v.get("pageId") else None


def parse(data: bytes) -> dict[str, list[list[tuple[float, float]]]]:
    """.rm bytes -> {layer name: [stroke points in page units]}"""
    import rmscene
    from rmscene import scene_items as si
    tree = rmscene.read_tree(io.BytesIO(data))
    layers: dict[str, list] = {}
    for g in tree.root.children.values():
        name = str(getattr(g.label, "value", g.label) or "")
        layers.setdefault(name, []).extend(
            [(p.x, p.y) for p in c.points] for c in g.children.values()
            if isinstance(c, si.Line) and len(c.points) > 1)
    return layers


def page(host: str, v: dict) -> dict | None:
    """Layers of the visible page, re-fetched only when the file changed."""
    import tablet
    f = f"{DOCS}/{v['doc']}/{v['pageId']}.rm"
    try:
        rc, mt = tablet.run(host, f"stat -c %Y {f} 2>/dev/null", timeout=5)
        if rc or not mt.strip():
            return {}                                   # page never saved: no strokes yet
        hit = _CACHE.get(v["pageId"])
        if hit and hit[0] == mt.strip():
            return hit[1]
        rc, data = tablet.run(host, f"cat {f}", timeout=10)
        layers = parse(data) if rc == 0 else None
    except (OSError, ValueError, Exception):            # rmscene raises its own types
        return None
    if layers is not None:
        _CACHE[v["pageId"]] = (mt.strip(), layers)
    return layers


def to_screen(stroke, v: dict) -> list[tuple[int, int]]:
    # registered offset (ox, oy) when known: the view fields fail on a
    # scrolled page (device 22:30: y + top, x + 936 at right -468). Else the
    # unscrolled measurement: x - rightViewportEdge, y - topViewportEdge.
    if "ox" in v:
        return [(int(x + v["ox"]), int(y + v["oy"])) for x, y in stroke]
    s = float(v.get("scale") or 1)
    return [(int((x - v["right"]) * s), int((y - v["top"]) * s)) for x, y in stroke]


def _shifts(page: np.ndarray, screen: np.ndarray, k: int) -> list[int]:
    """The k best offsets o with screen[i] ~ page[i - o], by overlap ratio
    (IoU) of occupied rows/columns. Plain correlation chased the page's
    densest ink (live page, 22:40: oy -4975 for -10352); one best offset
    alone locked onto a look-alike text line (sim hash_p)."""
    p = np.convolve(page > 0, np.ones(5), "same") > 0          # sampled points: close gaps
    q = (np.convolve(screen >= 3, np.ones(5), "same") > 0).astype(float)
    pp = np.pad(p.astype(float), len(q))
    inter = np.correlate(pp, q, "valid")
    union = np.convolve(pp, np.ones(len(q)), "valid") + q.sum() - inter
    score, out = inter / np.maximum(union, 1), []
    for i in np.argsort(score)[::-1]:
        if score[i] <= 0 or len(out) == k:
            break
        if all(abs(int(i) - j) > 20 for j in out):             # distinct peaks only
            out.append(int(i))
    return [len(q) - i for i in out]


def register(layers: dict, ink: np.ndarray, check: np.ndarray | None = None,
             min_hit: float = 0.8) -> tuple[int, int] | None:
    """(ox, oy) with screen = page + offset, found by matching the page's
    strokes to the screenshot ink (row, then column profiles; the few best
    candidates are tried), accepted only when >= min_hit of the on-screen
    points land on ink. ink = a dot-free mask (the grid fills rows); check =
    a looser mask for the acceptance test (thin/colour lines are light).
    None = no trustworthy match (then nothing is deleted)."""
    pts = np.array([p for ss in layers.values() for s in ss for p in s], float).reshape(-1, 2)
    if len(pts) < 50 or not ink.any():
        return None
    pts = pts.astype(int)
    x0, y0 = pts.min(0)
    img = np.zeros((pts[:, 1].max() - y0 + 1, pts[:, 0].max() - x0 + 1))
    np.add.at(img, (pts[:, 1] - y0, pts[:, 0] - x0), 1)
    c = ink if check is None else check
    d = c.copy()
    for dy in (-3, 0, 3):
        for dx in (-3, 0, 3):
            d |= np.roll(np.roll(c, dy, 0), dx, 1)

    def hits(ox, oy, m, p=pts):
        x, y = p[:, 0] + ox, p[:, 1] + oy
        on = (x >= 0) & (x < ink.shape[1]) & (y >= 0) & (y < ink.shape[0])
        return m[y[on], x[on]].mean() if on.sum() >= 50 // (len(pts) // len(p)) else 0.0
    for oy in (o - y0 for o in _shifts(img.sum(1), ink.sum(1).astype(float), 6)):
        rows = img[max(0, -oy - y0):max(0, -oy - y0 + ink.shape[0])]     # page rows on screen
        if not rows.any():
            continue
        for ox in (o - x0 for o in _shifts(rows.sum(0), ink.sum(0).astype(float), 2)):
            # profiles land within a few px; exact ink (no dilation) picks the best
            _, bx, by = max((hits(ox + a, oy + b, ink, pts[::3]), ox + a, oy + b)
                            for a in range(-8, 9) for b in range(-8, 9))
            if hits(bx, by, d) >= min_hit:
                return int(bx), int(by)
    return None


def mask(layers: dict, v: dict, names=None, width: int = 3) -> np.ndarray:
    img = Image.new("1", (CANVAS_W, CANVAS_H), 0)
    d = ImageDraw.Draw(img)
    for name, strokes in layers.items():
        if names is None or name in names:
            for s in strokes:
                d.line(to_screen(s, v), fill=1, width=width)
    return np.asarray(img, dtype=bool)


def agree(layers: dict, v: dict, ink: np.ndarray, r: int = 4) -> float:
    """Fraction of vector points (on screen) that land on screenshot ink."""
    pts = [p for ss in layers.values() for s in ss for p in to_screen(s, v)[::4]]
    pts = [(x, y) for x, y in pts if 0 <= x < CANVAS_W and 0 <= y < CANVAS_H]
    if not pts:
        return 1.0                                      # nothing to contradict
    a = ink.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            a |= np.roll(np.roll(ink, dy, 0), dx, 1)
    return sum(1 for x, y in pts if a[y, x]) / len(pts)


def context(v: dict | None, layers: dict | None) -> str:
    """Notebook -> page -> screen, one line for the reader/writer."""
    if not v:
        return ""
    out = f"Notebook '{v.get('name')}', page {int(v.get('page', 0)) + 1} of {v.get('pages')}."
    for name, ss in (layers or {}).items():
        ys = [y for s in ss for _, y in s]
        if ss:
            who = "Monke's replies" if name == "Monke" else f"Navy's ink ({name})"
            out += f" {who}: {len(ss)} strokes, y {int(min(ys))}-{int(max(ys))}."
    return out
