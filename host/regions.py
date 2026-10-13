"""regions.py — semantic page regions for the model context (R ids).

  build()          strokes (+ Monke's regions) -> [Region], ids stable vs prior
  context_block()  [Region] -> compact <regions> block for reader/writer prompt
  resolve()        'the circuit in this area' / 'the graph above' / 'R2' -> id
  target()         '@target R3' (protocol.parse_directives) -> (id, bbox)

Pure and deterministic: points/boxes in, values out; no device or model calls.
Boxes are half-open x0,y0,x1,y1 in PAGE coordinates (the .rm stroke space,
vectors.parse), so ids survive scrolling; to_screen_box() maps to pixels.
Navy-ink kinds come from stroke geometry (a heuristic: axes + smooth curve =
graph, mostly axis-aligned wires = circuit, short glyph line = text, '=' bars
= math, closed shapes = diagram); a kind the model states (hint()) wins and
is kept across turns. Monke's kinds come from the session (protocol
describe_reply types).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from xml.sax.saxutils import quoteattr

import protocol
import vectors
from workspace import _near, _union

Box = tuple[int, int, int, int]
KINDS = ("text", "math", "graph", "circuit", "diagram", "monke_reply", "unknown")
_MONKE_KIND = {"graph": "graph", "circuit": "circuit", "diagram": "diagram", "sketch": "diagram",
               "math": "math", "text": "text"}
Q = 40                       # bbox quantum for the stable-id key


@dataclass(frozen=True)
class Region:
    id: str                  # R1..Rn, stable across turns for unchanged ink
    kind: str                # one of KINDS
    owner: str               # navy | monke
    bbox: Box                # page coordinates, half-open
    stroke_count: int
    label: str = ""          # Monke's own id (G1, M2...) or a model label
    desc: str = ""           # what it contains (Monke body / model target_desc)
    created: int = 0         # turn first seen
    updated: int = 0         # turn its box/strokes last changed
    source: str = "geom"     # geom | session | model (who decided the kind)
    hw: int = field(default=0, compare=False, repr=False)   # id high-water: never reuse

    @property
    def center(self) -> tuple[float, float]:
        return (self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2


# ---- geometry helpers ------------------------------------------------------

def _sbox(stroke) -> Box:
    xs = [p[0] for p in stroke]
    ys = [p[1] for p in stroke]
    return (math.floor(min(xs)), math.floor(min(ys)), math.floor(max(xs)) + 1, math.floor(max(ys)) + 1)


def _gap(a: Box, b: Box) -> float:
    """Euclidean distance between two boxes (0 when they touch/overlap)."""
    dx = max(0, a[0] - b[2], b[0] - a[2])
    dy = max(0, a[1] - b[3], b[1] - a[3])
    return math.hypot(dx, dy)


def _iou(a: Box, b: Box) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _inside(a: Box, b: Box, m: int = 10) -> bool:
    return a[0] >= b[0] - m and a[1] >= b[1] - m and a[2] <= b[2] + m and a[3] <= b[3] + m


def _key(owner: str, b: Box) -> tuple:
    return (owner,) + tuple(round(v / Q) for v in b)


def _num(rid: str) -> int:
    m = re.fullmatch(r"R(\d+)", rid or "")
    return int(m[1]) if m else 0


# ---- clustering ----------------------------------------------------------------

def _cluster(boxes: list[Box], gap: int) -> list[list[int]]:
    """Stroke indices grouped like workspace._pen_boxes (boxes within `gap`
    merge, repeated until no two group boxes are near), but in page
    coordinates (nothing clipped to the screen) and via a grid hash, so 2000
    strokes stay well under 50 ms."""
    parent = list(range(len(boxes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    cell, half, grid = max(2 * gap, 64), gap / 2, {}
    for i, b in enumerate(boxes):
        x0, y0, x1, y1 = b[0] - gap, b[1] - gap, b[2] + gap, b[3] + gap     # _near(b, o, gap)
        cx0, cy0 = int((b[0] - half) // cell), int((b[1] - half) // cell)
        cx1, cy1 = int((b[2] + half) // cell), int((b[3] + half) // cell)
        ri = i
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                bucket = grid.setdefault((cx, cy), [])
                for j in bucket:
                    o = boxes[j]
                    if o[0] <= x1 and x0 <= o[2] and o[1] <= y1 and y0 <= o[3]:
                        rj = find(j)
                        if rj != ri:
                            parent[rj] = ri
                bucket.append(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(boxes)):
        groups.setdefault(find(i), []).append(i)
    out = []                                    # [box, members]: fixpoint merge on group boxes
    for members in groups.values():
        b = boxes[members[0]]
        for i in members[1:]:
            b = _union(b, boxes[i])
        out.append([b, members])
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                if _near(out[i][0], out[j][0], gap):
                    out[i] = [_union(out[i][0], out[j][0]), out[i][1] + out[j][1]]
                    out.pop(j)
                    merged = True
                    break
            if merged:
                break
    return [sorted(m) for _, m in out]


# ---- classification (Navy's ink) -------------------------------------------------

def _feat(s) -> dict:
    b = _sbox(s)
    w, h = b[2] - b[0] - 1, b[3] - b[1] - 1
    path = sum(math.dist(s[i], s[i + 1]) for i in range(len(s) - 1))
    chord = math.dist(s[0], s[-1])
    straight = path > 5 and chord / path > 0.95
    flips, last = 0, 0
    for i in range(len(s) - 1):
        dy = s[i + 1][1] - s[i][1]
        sgn = (dy > 0.5) - (dy < -0.5)
        if sgn and last and sgn != last:
            flips += 1
        last = sgn or last
    return {"box": b, "w": w, "h": h, "path": path, "straight": straight,
            "horiz": straight and h <= 0.15 * max(w, 1), "vert": straight and w <= 0.15 * max(h, 1),
            "closed": path > 60 and chord < 0.15 * path, "flips": flips}


def classify(strokes) -> str:
    """text | math | graph | circuit | diagram | unknown, from geometry only."""
    strokes = [s for s in strokes if len(s) > 1]
    if len(strokes) > 400:                     # a big page block: an even sample decides
        strokes = strokes[::len(strokes) // 400 + 1]
    fs = [_feat(s) for s in strokes]
    if not fs:
        return "unknown"
    b = fs[0]["box"]
    for f in fs[1:]:
        b = _union(b, f["box"])
    W, H = b[2] - b[0], b[3] - b[1]
    long_h = [f for f in fs if f["horiz"] and f["w"] >= 0.6 * W]
    long_v = [f for f in fs if f["vert"] and f["h"] >= 0.6 * H]
    smooth = [f for f in fs if not f["straight"] and f["flips"] <= 3 and f["w"] >= 0.35 * W
              and f["path"] > 80]
    if W >= 120 and H >= 100 and long_h and long_v and smooth:
        return "graph"
    lined = [f for f in fs if f["path"] >= 5]
    axis = [f for f in lined if f["horiz"] or f["vert"]]
    zig = any(not f["straight"] and f["flips"] >= 4 and f["w"] >= 3 * max(f["h"], 1) for f in fs)
    if W >= 100 and H >= 80 and len(fs) >= 4 and (len(axis) >= 0.5 * len(lined) or zig and len(axis) >= 3):
        return "circuit"
    dims = sorted(max(f["w"], f["h"]) for f in fs)
    if dims[len(dims) // 2] < 60:                              # glyph-sized strokes: writing
        bars = [f for f in fs if f["horiz"] and f["w"] < 80]
        eq = any(a is not c and abs(a["box"][1] - c["box"][1]) <= 25
                 and min(a["box"][2], c["box"][2]) > max(a["box"][0], c["box"][0])
                 for a in bars for c in bars)
        frac = any(f["horiz"] and any(o["box"][3] <= f["box"][1] + 4 for o in fs)
                   and any(o["box"][1] >= f["box"][3] - 4 for o in fs)
                   and H <= 220 for f in fs if f["w"] >= 60)
        return "math" if eq or frac else "text"
    if any(f["closed"] for f in fs) and min(W, H) >= 60:
        return "diagram"
    return "unknown"


# ---- build -----------------------------------------------------------------

def navy_strokes(layers: dict | None) -> list:
    """Navy's strokes of a page's layers (Monke's layers, protocol.MONKE_LAYERS, dropped)."""
    return [s for name, ss in (layers or {}).items() if name not in protocol.MONKE_LAYERS for s in ss]


def build(strokes: list, monke_regions: list, prior: list | None = None, turn: int = 0,
          gap: int = 60, exclude_box: Box | None = None) -> list[Region]:
    """Regions of a page: Navy's strokes clustered by proximity and
    classified, plus Monke's regions ([{box, type?, id?, text?, strokes?}],
    monke._monke_regions shape, same coordinate frame as `strokes`).
    exclude_box: the focus/prompt box — its strokes are not a region (like
    protocol.page_map's F). Ids: matched to `prior` by quantised bbox, else
    by IoU >= 0.5 (grown ink), else new R<n> in reading order."""
    ss = [s for s in strokes or [] if len(s) > 0]
    sb = [_sbox(s) for s in ss]
    if exclude_box:
        keep = [i for i, b in enumerate(sb) if not _inside(b, exclude_box, 10)]
        ss, sb = [ss[i] for i in keep], [sb[i] for i in keep]
    fresh = []                                              # (owner, box, n, kind, label, desc, source)
    for members in _cluster(sb, gap):
        b = sb[members[0]]
        for i in members[1:]:
            b = _union(b, sb[i])
        fresh.append(("navy", b, len(members), classify([ss[i] for i in members]), "", "", "geom"))
    for m in monke_regions or []:
        if not m.get("box"):
            continue
        b = tuple(int(v) for v in m["box"])
        kind = _MONKE_KIND.get(str(m.get("type") or ""), "monke_reply")
        fresh.append(("monke", b, int(m.get("strokes") or 0), kind, str(m.get("id") or ""),
                      " ".join(str(m.get("text") or "").split()), "session"))
    fresh.sort(key=lambda f: (f[1][1], f[1][0], f[0]))

    prior = list(prior or [])
    hw = max([p.hw for p in prior] + [_num(p.id) for p in prior] + [0])
    match: dict[int, Region] = {}
    used: set[str] = set()
    pk = {_key(p.owner, p.bbox): p for p in prior}
    for i, f in enumerate(fresh):                           # 1: same quantised box
        p = pk.get(_key(f[0], f[1]))
        if p and p.id not in used:
            match[i], _ = p, used.add(p.id)
    pairs = sorted(((_iou(f[1], p.bbox), i, p.id) for i, f in enumerate(fresh) if i not in match
                    for p in prior if p.owner == f[0] and p.id not in used), reverse=True)
    byid = {p.id: p for p in prior}
    for iou, i, pid in pairs:                               # 2: grown/shrunk ink
        if iou >= 0.5 and i not in match and pid not in used:
            match[i], _ = byid[pid], used.add(pid)
    out = []
    for i, (owner, b, n, kind, label, desc, src) in enumerate(fresh):
        p = match.get(i)
        if p is None:
            hw += 1
            out.append(Region(f"R{hw}", kind, owner, b, n, label, desc, turn, turn, src))
            continue
        changed = p.bbox != b or p.stroke_count != n
        if p.source == "model":                             # the model's word on kind stays
            kind, desc, src = p.kind, desc or p.desc, "model"
        out.append(Region(p.id, kind, owner, b, n, label or p.label, desc or p.desc,
                          p.created, turn if changed else p.updated, src))
    return [replace(r, hw=hw) for r in out]


def hint(regions: list[Region], rid: str, kind: str | None = None, desc: str | None = None,
         label: str | None = None) -> list[Region]:
    """Record the model's reading of a region (reader target_kind /
    target_desc); build() keeps it for that region on later turns."""
    out = []
    for r in regions:
        if r.id == rid:
            r = replace(r, kind=kind if kind in KINDS else r.kind,
                        desc=desc if desc is not None else r.desc,
                        label=label if label is not None else r.label, source="model")
        out.append(r)
    return out


# ---- context block ---------------------------------------------------------------

def _focus_box(focus, regions) -> Box | None:
    if focus is None:
        return None
    if isinstance(focus, str):
        r = _find(focus, regions)
        return r.bbox if r else None
    if len(focus) == 2:
        return (int(focus[0]), int(focus[1]), int(focus[0]) + 1, int(focus[1]) + 1)
    x0, y0, x1, y1 = (int(v) for v in focus)
    return (x0, y0, x1, y1)


def _entry(r: Region, near: bool, rich: bool = True) -> str:
    s = (f'<r id="{r.id}" kind="{r.kind}" owner="{r.owner}" box="{",".join(map(str, r.bbox))}"'
         f' n="{r.stroke_count}"')
    if rich and r.label:
        s += f" label={quoteattr(r.label[:12])}"
    if rich and r.desc:
        s += f" desc={quoteattr(r.desc[:40])}"
    return s + (' near="focus"' if near else "") + "/>"


def context_block(regions: list[Region], focus=None, budget_chars: int = 700) -> str:
    """<regions> block, nearest-to-focus first (reading order without a
    focus). focus: a region id, a box or an (x, y) point. Never longer than
    budget_chars, except that the focus-nearest region is always kept."""
    if not regions:
        return "<regions/>"
    fb = _focus_box(focus, regions)
    order = sorted(regions, key=lambda r: ((_gap(r.bbox, fb) if fb else 0), r.bbox[1], r.bbox[0], r.id))
    head, tail = "<regions>", "</regions>"
    first = _entry(order[0], fb is not None)
    if len(head) + len(first) + len(tail) + 2 > budget_chars:
        first = _entry(order[0], fb is not None, rich=False)
    lines, used = [first], len(head) + len(first) + len(tail) + 2
    for k, r in enumerate(order[1:], 1):
        e = _entry(r, False)
        rest = len(order) - k - 1
        more = len(f'<more n="{rest}"/>') + 1 if rest else 0
        if used + len(e) + 1 + more > budget_chars:
            m = f'<more n="{len(order) - k}"/>'
            if used + len(m) + 1 <= budget_chars:
                lines.append(m)
            break
        lines.append(e)
        used += len(e) + 1
    return "\n".join([head] + lines + [tail])


# ---- resolve ----------------------------------------------------------------------

_KIND_WORDS = (
    ("circuit", r"circuits?|schematics?|resistors?|capacitors?|wiring|network"),
    ("graph", r"graphs?|plots?|curves?|axes|charts?"),
    ("math", r"equations?|formulae?|formulas?|expressions?|integrals?|derivations?|maths?|calculations?"),
    ("diagram", r"diagrams?|figures?|drawings?|sketch(?:es)?|flowcharts?|pictures?|shapes?"),
    ("text", r"text|notes?|sentences?|paragraphs?|words?|writing|question"),
)
_REPLY = re.compile(r"\b(answer|reply|response|output)\b")
_MINE = re.compile(r"\b(my|mine|i drew|i wrote|i made)\b")
_YOURS = re.compile(r"\b(your|yours|you drew|you wrote|you made)\b")
_DEIXIS = re.compile(r"\b(this|that|these|those|here|there|area|near|nearby|beside|next to)\b")
_REL = (("above", r"\b(above|up there|earlier|previous)\b"), ("below", r"\b(below|under|beneath|underneath)\b"),
        ("left-of", r"\b(to the left|left of)\b"), ("right-of", r"\b(to the right|right of)\b"))
_ORD = (("left", r"\b(left|leftmost|left-hand)\b"), ("right", r"\b(right|rightmost|right-hand)\b"),
        ("top", r"\b(top|topmost|upper|first)\b"), ("bottom", r"\b(bottom|lowest|lower|last)\b"))


def _find(rid: str, regions: list[Region]) -> Region | None:
    return next((r for r in regions if r.id == rid), None) or \
        next((r for r in regions if r.label and r.label == rid), None)


def _pick_nearest(cands, anchor, what):
    ds = sorted(((_gap(r.bbox, anchor), r.bbox[1], r.bbox[0], r) for r in cands), key=lambda t: t[:3])
    d1, d2 = ds[0][0], ds[1][0]
    if d2 - d1 < max(40.0, 0.25 * d2):
        return None, 0.0, f"ambiguous: {ds[0][3].id} and {ds[1][3].id} are equally close ({what})"
    return ds[0][3].id, round(0.7 + 0.25 * min(1.0, (d2 - d1) / max(d2, 1.0)), 2), \
        f"nearest {what} ({d1:.0f} vs {d2:.0f} px)"


def resolve(text: str, regions: list[Region], pen_point=None, last_prompt_bbox=None
            ) -> tuple[str | None, float, str]:
    """Deictic phrase -> (region id | None, confidence 0..1, reason).
    Explicit ids (R3, or Monke's G1) win; kind words narrow by kind,
    my/your by owner; above/below/left of filter against the anchor (pen
    point, else the prompt's box); left/right/top/bottom one pick the
    extreme; otherwise the nearest. Two equally good candidates -> None."""
    t = " ".join((text or "").lower().split())
    for tok in re.findall(r"\b([A-Za-z]\d+)\b", text or ""):
        r = _find(tok, regions) or _find(tok.upper(), regions)
        if r:
            return r.id, 1.0, f"explicit id {tok}"
        if re.fullmatch(r"[RNMGF]\d+", tok.upper()):
            return None, 0.0, f"unknown id {tok}"
    kind = next((k for k, rx in _KIND_WORDS if re.search(rf"\b({rx})\b", t)), None)
    owner = "navy" if _MINE.search(t) else "monke" if _YOURS.search(t) or _REPLY.search(t) else None
    rel = next((k for k, rx in _REL if re.search(rx, t)), None)
    ordn = None if rel else next((k for k, rx in _ORD if re.search(rx, t)), None)
    deictic = bool(_DEIXIS.search(t)) or bool(re.search(r"\b(one)\b", t))
    if not (kind or owner or rel or ordn or deictic):
        return None, 0.0, "no region reference"
    anchor = _focus_box(tuple(pen_point) if pen_point is not None else
                        tuple(last_prompt_bbox) if last_prompt_bbox else None, regions)
    cands = [r for r in regions
             if not (last_prompt_bbox and (_inside(r.bbox, tuple(last_prompt_bbox), 10)
                                           or _iou(r.bbox, tuple(last_prompt_bbox)) >= 0.6))]
    if kind:
        cands = [r for r in cands if r.kind == kind]
    if owner:
        cands = [r for r in cands if r.owner == owner]
    what = " ".join(filter(None, (owner, kind or "region")))
    if rel and anchor is None:                       # no anchor: 'the graph above' = the top one
        ordn, rel = {"above": "top", "below": "bottom", "left-of": "left", "right-of": "right"}[rel], None
    if rel and anchor is not None:
        a: Box = anchor
        cx = lambda r: r.center[0]
        cy = lambda r: r.center[1]
        test = {"above": lambda r: cy(r) < a[1], "below": lambda r: cy(r) > a[3],
                "left-of": lambda r: cx(r) < a[0], "right-of": lambda r: cx(r) > a[2]}[rel]
        cands = [r for r in cands if test(r)]
        what += f" {rel} the prompt"
    if not cands:
        return None, 0.0, f"no {what}"
    if len(cands) == 1:
        conf = 0.9 if (kind or owner or rel) else 0.8
        return cands[0].id, conf, f"only {what}"
    if ordn:
        key = {"left": lambda r: r.center[0], "right": lambda r: -r.center[0],
               "top": lambda r: r.center[1], "bottom": lambda r: -r.center[1]}[ordn]
        ds = sorted(cands, key=lambda r: (key(r), r.id))
        if key(ds[1]) - key(ds[0]) < 40:
            return None, 0.0, f"ambiguous: {ds[0].id} and {ds[1].id} are both {ordn}most"
        return ds[0].id, 0.85, f"{ordn}most {what}"
    if anchor is None:
        return None, 0.0, f"ambiguous: {len(cands)} {what} and no pen/prompt position"
    return _pick_nearest(cands, anchor, what)


# ---- model output hook ---------------------------------------------------------

def directive_boxes(reply_or_directives, regions: list[Region]) -> list[dict]:
    """protocol.parse_directives output (or the raw reply) with each
    directive's region id mapped back: id -> the canonical R id, plus 'box'
    (page coords) when the id/label is a known region."""
    found = (protocol.parse_directives(reply_or_directives)[0]
             if isinstance(reply_or_directives, str) else reply_or_directives)
    out = []
    for d in found:
        d = dict(d)
        r = _find(d.get("id") or "", regions) if d.get("id") else None
        if r:
            d.update(id=r.id, box=r.bbox)
        out.append(d)
    return out


def target(reply: str, regions: list[Region]) -> tuple[str, Box] | None:
    """'@target R3' at the head of a reply -> ('R3', bbox); None when absent/unknown."""
    for d in directive_boxes(reply, regions):
        if d["op"] == "target" and "box" in d:
            return d["id"], d["box"]
    return None


def boxes(regions: list[Region]) -> dict[str, Box]:
    """{id: bbox} — the shape of protocol.boxes(), for merging into page_regions."""
    return {r.id: r.bbox for r in regions}


def to_screen_box(bbox: Box, view: dict) -> Box:
    """A page-coordinate box in the live view's screen pixels (vectors.to_screen)."""
    (x0, y0), (x1, y1) = vectors.to_screen([(bbox[0], bbox[1]), (bbox[2], bbox[3])], view)
    return (x0, y0, x1, y1)


def to_page_box(box: Box, view: dict) -> Box:
    """A screen-pixel box (e.g. monke._monke_regions' 'box') in page coordinates.
    Inverse of vectors.to_screen(): registered offsets apply before scale."""
    (x0, y0), (x1, y1) = vectors.to_page([(box[0], box[1]), (box[2], box[3])], view)
    return (int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
