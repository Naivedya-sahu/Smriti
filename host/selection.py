"""selection.py — Navy's lasso selection as the model's grounding target.

When Navy lasso-selects ink on the tablet and then asks ("what is this?",
"fix it"), the selected strokes are the explicit target: their box goes
into the prompt and a crop of the screenshot rides along as an image.

Device facts (monke._erase_prompt, probed): sceneController.selectionItemCount
is the live count; addSelectionRect(rect, 0|1) takes PAGE (.rm scene)
coordinates; the count lags one QML tick. Firmware 3.27.1.0 exposes page-space
boundingRect and itemsBoundingRect; legacy guesses remain as fallbacks. The
first probe that answers is cached for the session, and so is 'none answers'
(docs/lanes/selection-probe.md); monke.run() resets both.

Pure apart from the injected qml_eval(host, js) -> str | None.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import NamedTuple

import vectors

COUNT_JS = "String(sceneController.selectionItemCount)"   # same read as monke
OVERLAP = 0.6                                              # strokes_in threshold
RULE = 'Navy selected this ink; "this/it/these" refers to the selection.'


class Probe(NamedTuple):
    name: str
    js: str


# r -> "[x,y,w,h]" or 'undefined'; every probe is wrapped so a missing
# property answers 'undefined' instead of an ERR from the eye
_RECT = ("function R(r){return (r&&isFinite(r.width)&&r.width>0)?"
         "JSON.stringify([r.x,r.y,r.width,r.height]):'undefined'}")


def _wrap(body: str) -> str:
    return f"(function(){{{_RECT}try{{{body}}}catch(e){{return 'undefined'}}}})()"


PROBES: tuple[Probe, ...] = (
    # Device enumeration 2026-10-13, fw 3.27.1.0: real page-space bounds.
    # Empty selection reports 0,0,0,0, rejected by _parse().
    Probe("boundingRect", _wrap("return R(sceneController.boundingRect)")),
    Probe("itemsBoundingRect", _wrap("return R(sceneController.itemsBoundingRect)")),
    Probe("selectionRect", _wrap("return R(sceneController.selectionRect)")),
    Probe("selectionBoundingRect", _wrap("return R(sceneController.selectionBoundingRect)")),
    Probe("selectedItems", _wrap(
        "var a=sceneController.selectedItems;if(!a||!a.length)return 'undefined';"
        "var x0=1e9,y0=1e9,x1=-1e9,y1=-1e9,ids=[];for(var i=0;i<a.length;i++){"
        "var b=a[i].boundingRect||a[i].rect;if(!b)continue;x0=Math.min(x0,b.x);"
        "y0=Math.min(y0,b.y);x1=Math.max(x1,b.x+b.width);y1=Math.max(y1,b.y+b.height);"
        "if(a[i].id!==undefined)ids.push(String(a[i].id))}"
        "return x1>x0?JSON.stringify({rect:[x0,y0,x1-x0,y1-y0],ids:ids}):'undefined'")),
    Probe("selectionBounds", _wrap("return R(sceneController.selectionBounds)")),
    Probe("selection.boundingRect", _wrap(
        "var s=sceneController.selection;return R(s&&(s.boundingRect||s.rect))")),
)

_CACHE: dict = {"probe": None, "none": False}   # first probe that answered; none answered


def reset_cache() -> None:
    _CACHE.update(probe=None, none=False)


@dataclass
class Selection:
    count: int
    page_box: tuple[float, float, float, float]          # x0, y0, x1, y1 (.rm units)
    screen_box: tuple[int, int, int, int] | None         # None when the view is unknown
    ids: list[str] = field(default_factory=list)
    source: str = ""                                     # Probe.name that answered


def _call(qml_eval, host, js) -> str | None:
    try:
        out = qml_eval(host, js)
    except Exception:
        return None
    out = (out or "").strip()
    if not out or out in ("undefined", "null") or out.startswith("ERR"):
        return None
    return out


def _count(qml_eval, host) -> int | None:
    out = _call(qml_eval, host, COUNT_JS)
    try:
        return int(out) if out is not None else None
    except ValueError:
        return None


def _parse(out: str | None):
    """'[x,y,w,h]' or '{"rect":[...], "ids":[...]}' -> ((x0,y0,x1,y1), ids)."""
    if out is None:
        return None
    try:
        v = json.loads(out)
    except ValueError:
        return None
    ids = []
    if isinstance(v, dict):
        ids = [str(i) for i in v.get("ids") or []]
        v = v.get("rect")
    if not isinstance(v, list) or len(v) != 4:
        return None
    try:
        x, y, w, h = (float(a) for a in v)
    except (TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (x, y, w, h))) or w <= 0 or h <= 0:
        return None
    return (x, y, x + w, y + h), ids


def page_to_screen(box, v: dict) -> tuple[int, int, int, int]:
    (x0, y0), (x1, y1) = vectors.to_screen([box[:2], box[2:]], v)
    return x0, y0, x1, y1


def _flat(page_strokes) -> list:
    if isinstance(page_strokes, dict):
        return [s for ss in page_strokes.values() for s in ss]
    return list(page_strokes or [])


def _overlap(stroke, box) -> float:
    """Share of the stroke's bbox inside box; a flat line or dot is given a
    1-unit thickness so its length still counts."""
    xs, ys = [p[0] for p in stroke], [p[1] for p in stroke]
    x0, y0 = min(xs), min(ys)
    x1, y1 = max(max(xs), x0 + 1), max(max(ys), y0 + 1)
    w = min(x1, box[2]) - max(x0, box[0])
    h = min(y1, box[3]) - max(y0, box[1])
    return max(w, 0) * max(h, 0) / ((x1 - x0) * (y1 - y0))


def strokes_in(selection: Selection, page_strokes, threshold: float = OVERLAP) -> list:
    """Page strokes (a list, or vectors.page()'s {layer: strokes}) whose bbox
    lies >= threshold inside the selection's page box."""
    return [s for s in _flat(page_strokes)
            if s and _overlap(s, selection.page_box) >= threshold - 1e-9]


def _kind(stroke) -> str:
    """dot / line / curve from the stroke's own geometry (chord vs path)."""
    xs, ys = [p[0] for p in stroke], [p[1] for p in stroke]
    if max(max(xs) - min(xs), max(ys) - min(ys)) < 4:
        return "dot"
    path = sum(math.dist(a, b) for a, b in zip(stroke, stroke[1:]))
    return "line" if math.dist(stroke[0], stroke[-1]) >= 0.95 * path else "curve"


def _ints(box) -> str:
    return ",".join(str(int(a)) for a in box)


def grounding_block(selection: Selection, strokes, crop_png_bytes: bytes | None = None,
                    budget_chars: int = 500) -> str:
    """<selection ...> block + the deixis rule, for the model prompt; at most
    budget_chars. box = screen px (same frame as the <page> map), page = .rm
    units; body rows are stroke kind + bbox relative to the page box corner."""
    strokes = [s for s in _flat(strokes) if s]
    kinds: dict[str, int] = {}
    for s in strokes:
        k = _kind(s)
        kinds[k] = kinds.get(k, 0) + 1
    head = f'<selection count="{int(selection.count)}"'
    if selection.screen_box:
        head += f' box="{_ints(selection.screen_box)}"'
    head += f' page="{_ints(selection.page_box)}"'
    if kinds:
        head += f' kinds="{",".join(f"{k}:{n}" for k, n in sorted(kinds.items()))}"'
    if crop_png_bytes:
        head += ' image="attached"'
    head += ">"
    tail = ["</selection>", RULE]
    bx, by = selection.page_box[0], selection.page_box[1]
    rows: list[str] = []
    used = len(head) + sum(len(t) + 1 for t in tail)
    for i, s in enumerate(strokes):
        xs, ys = [p[0] for p in s], [p[1] for p in s]
        row = f"  {_kind(s)} {_ints((min(xs) - bx, min(ys) - by, max(xs) - bx, max(ys) - by))}"
        more = f"  ...+{len(strokes) - i - 1} more" if i < len(strokes) - 1 else ""
        if used + len(row) + 1 + (len(more) + 1 if more else 0) > budget_chars:
            rows.append(f"  ...+{len(strokes) - i} more")
            break
        rows.append(row)
        used += len(row) + 1
    return "\n".join([head, *rows, *tail])


def precedence(selection: Selection | None, deictic_result) -> dict:
    """Which target the turn grounds on. An active, non-empty selection is
    Navy pointing explicitly: it ALWAYS beats a text-based guess at
    'this/it/these'. {winner: selection|deictic|none, target, reason}."""
    if selection is not None and selection.count > 0:
        why = f"active selection of {selection.count} item(s) via {selection.source or '?'}"
        if deictic_result:
            why += " beats text deixis"
        return {"winner": "selection", "target": selection, "reason": why}
    if deictic_result:
        return {"winner": "deictic", "target": deictic_result,
                "reason": "no active selection; text deixis resolved"}
    return {"winner": "none", "target": None, "reason": "no selection and no deixis target"}


def read_selection(qml_eval, host, view: dict | None = None) -> Selection | None:
    """The live selection, or None (none / count unreadable / no probe works).
    view: vectors.view(host), with the registered ox/oy when known."""
    n = _count(qml_eval, host)
    if not n or _CACHE["none"]:                 # no probe works: one round trip a turn
        return None
    order = sorted(PROBES, key=lambda p: p.name != _CACHE["probe"])  # cached first
    for p in order:
        got = _parse(_call(qml_eval, host, p.js))
        if not got:
            continue
        box, ids = got
        _CACHE["probe"] = p.name
        page, screen = box, (page_to_screen(box, view) if view else None)
        return Selection(n, page, screen, ids, p.name)
    _CACHE["none"] = _CACHE["probe"] is None    # a probe that once worked may lag a tick
    return None
