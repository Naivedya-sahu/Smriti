"""protocol.py — Smriti Page Protocol (SPP) v1, both directions.

  page_map()          daemon -> model: the <page> block (who inked what, where)
  parse_directives()  model -> daemon: leading @target / @place / @refine lines

Grammar: skill/smriti/references/protocol.md. Pure: boxes and text in, text
out; no device calls, no model calls. Boxes are half-open screen pixels.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape, quoteattr

import vectors
from workspace import Box, _inside, _pen_boxes

W, H = vectors.CANVAS_W, vectors.CANVAS_H
MONKE_LAYERS = ("Monke", "Monke-greet")        # layers.MONKE, layers.GREET: never Navy's
CAP = 12                                       # regions per page map
BODY = 60                                      # chars of a Monke region's body


def _clip(box) -> Box | None:
    """Box cut to the screen; None when nothing of it is visible."""
    if not box:
        return None
    x0, y0, x1, y1 = max(0, int(box[0])), max(0, int(box[1])), min(W, int(box[2])), min(H, int(box[3]))
    return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None


def _strokes_box(strokes) -> Box | None:
    pts = [p for s in strokes or [] for p in s]
    return (min(x for x, _ in pts), min(y for _, y in pts),
            max(x for x, _ in pts) + 1, max(y for _, y in pts) + 1) if pts else None


def _navy(view, layers, focus, gap: int) -> list[tuple[Box, int, str]]:
    """Navy's ink groups on screen: strokes of his layers, merged by proximity
    (workspace._pen_boxes; it also drops what is off-screen). The newest
    writing is F, not a group: its strokes may already be in the page file."""
    out = []
    for name, ss in (layers or {}).items() if view else ():
        if name in MONKE_LAYERS:
            continue
        on = [vectors.to_screen(s, view) for s in ss]
        if focus:
            on = [s for s in on if not (s and _inside(_strokes_box([s]), focus, 10))]
        out += [(box, n, name) for box, n in _pen_boxes(on, gap)]
    return out


def page_map(view: dict | None, layers: dict | None, strokes: list, monke_regions: list,
             free_bands: list, focus_box=None, cap: int = CAP, gap: int = 60,
             free_rects: list | None = None) -> str:
    """The <page> block of protocol.md §1.

    view / layers: the frame's live view and page-file layers (shot.info,
    vectors.py); strokes: the newest writing (screen px) -> region F, or
    focus_box (a selection) when given; monke_regions: [{box, type?, turn?,
    text?, id?}] from the session; free_bands: [(y0, y1)] (screen.free_bands).
    Off-screen regions are dropped; at most `cap` regions."""
    focus = _clip(focus_box or _strokes_box(strokes))
    monke = [dict(m, box=b) for m in monke_regions or [] if (b := _clip(m.get("box")))]
    monke = monke[-(cap - 1):]                             # the newest of Monke's stay
    room = cap - bool(focus) - len(monke)
    # ponytail: "smallest merge first" = widen the proximity gap until the
    # groups fit; a per-pair merge order if the coarse groups mislead the model
    navy = _navy(view, layers, focus, gap)
    while len(navy) > max(room, 0) and gap < H:
        gap *= 2
        navy = _navy(view, layers, focus, gap)
    navy = sorted(navy, key=lambda g: (g[0][1], g[0][0]))[:max(room, 0)]

    def box(b):
        return f'box="{b[0]},{b[1]},{b[2]},{b[3]}"'
    rows = [(b, f'<region id="N{i}" owner="navy" layer={quoteattr(name)} {box(b)} '
                f'strokes="{n}" age="old"/>') for i, (b, n, name) in enumerate(navy, 1)]
    k = 0
    for m in monke:
        if not m.get("id"):
            k += 1
        attrs = (f'id={quoteattr(str(m.get("id") or f"M{k}"))} owner="monke"'
                 + (f' type={quoteattr(str(m["type"]))}' if m.get("type") else "")
                 + f' {box(m["box"])}' + (f' turn="{int(m["turn"])}"' if m.get("turn") else ""))
        body = escape(" ".join(str(m.get("text") or "").split())[:BODY])
        rows.append((m["box"], f"<region {attrs}>{body}</region>" if body else f"<region {attrs}/>"))
    if focus:
        rows.append((focus, f'<region id="F" owner="navy" {box(focus)} '
                            f'strokes="{len(strokes or [])}" age="new" focus="yes"/>'))
    rows.sort(key=lambda r: (r[0][1], r[0][0]))
    head = "<page"
    if view:
        head += (f' notebook={quoteattr(str(view.get("name") or ""))}'
                 f' page="{int(view.get("page", 0)) + 1}/{view.get("pages")}"')
    head += f' screen="{W}x{H}"'
    if view:
        head += f' scrolled="{"yes" if round(float(view.get("top") or 0)) else "no"}"'
    free = [f'<free id="{escape(str(rid))}" box="{b[0]},{b[1]},{b[2]},{b[3]}"/>'
            for rid, b in free_rects or []]
    if not free:
        free = [f'<free box="0,{int(y0)},{W},{int(y1)}"/>' for y0, y1 in free_bands or []]
    return "\n".join([head + ">"] + ["  " + r for _, r in rows] + ["  " + f for f in free]
                     + ["</page>"])


def region(page: str, rid: str) -> str | None:
    """The <region> line of one id in a page map (None = no such region)."""
    m = re.search(rf'^\s*(<region id="{re.escape(rid)}" .*)$', page or "", re.M)
    return m[1] if m else None


def boxes(page: str) -> dict[str, Box]:
    """Named region/free boxes parsed from a page map."""
    out = {}
    for rid, raw in re.findall(r'<(?:region|free)\s+[^>]*id="(\w+)"[^>]*box="([\d,-]+)"', page or ""):
        try:
            values = tuple(map(int, raw.split(",")))
        except ValueError:
            continue
        if len(values) == 4:
            out[rid] = values
    return out


def region_strokes(view: dict | None, layers: dict | None, box: Box,
                   margin: int = 10) -> list[list[tuple[int, int]]]:
    """Navy-layer vector strokes lying inside a screen-space region box."""
    if not view or not box:
        return []
    x0, y0, x1, y1 = box
    outer = (x0 - margin, y0 - margin, x1 + margin, y1 + margin)
    out = []
    for name, strokes in (layers or {}).items():
        if name in MONKE_LAYERS:
            continue
        for stroke in strokes:
            on = [(int(round(x)), int(round(y))) for x, y in vectors.to_screen(stroke, view)]
            sb = _strokes_box([on]) if on else None
            if sb and _inside(sb, outer, 0):
                out.append(on)
    return out


def describe_reply(text: str) -> tuple[str, str]:
    """(type, body) of a logged Monke reply, for its region in the page map."""
    # a tikz flowchart / phasor drawing is a diagram, not maths (bug #4 lane)
    kind = ("circuit" if "circuitikz" in text else "graph" if "\\begin{axis}" in text
            else "diagram" if "tikzpicture" in text
            else "sketch" if "<svg" in text else "math" if "$$" in text else "text")
    body = re.sub(r"\$\$\s*\\begin\{.*?\$\$|<svg.*?</svg>", "[drawing]", text, flags=re.S)
    return kind, " ".join(body.split())[:BODY]


_DIRECTIVES = (
    (re.compile(r"@target\s+(\w+)"), lambda m: {"op": "target", "id": m[1]}),
    (re.compile(r"@place\s+(?:(below|beside|above|left-of|in)\s+(\w+)|free)"),
     lambda m: {"op": "place", "how": m[1] or "free", "id": m[2]}),
    (re.compile(r"@refine\s+(\w+)\s+as\s+(graph|circuit|diagram|text)"),
     lambda m: {"op": "refine", "id": m[1], "as": m[2]}),
)


def parse_directives(text: str) -> tuple[list[dict], str]:
    """Leading '@...' lines of a reply -> directive dicts; returns them and
    the reply without them. A leading '@' line that is no known directive is
    taken off too (op 'unknown'): directive lines are never inked."""
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln and not ln.startswith("@"):
            break
        if ln:
            out.append(next((make(m) for rx, make in _DIRECTIVES if (m := rx.fullmatch(ln))),
                            {"op": "unknown", "raw": ln}))
        i += 1
    return (out, "\n".join(lines[i:])) if out else ([], text)
