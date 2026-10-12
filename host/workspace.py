"""Pure, model-independent workspace observations from visible pixels and pen paths.

Boxes are half-open screen coordinates; no OCR, device calls or model routing.
The caller supplies a frame ID only when it can verify snapshot identity.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from screen import CANVAS_H, CANVAS_W, ink_mask, page_diff

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class Region:
    source: str                 # pen | screen_delta; provenance, not ownership
    box: Box                    # [x0, y0, x1, y1) in visible screen pixels
    stroke_count: int
    density: float              # dark-pixel fraction in the visible crop


@dataclass(frozen=True)
class Workspace:
    frame_id: str | None
    size: tuple[int, int]
    regions: tuple[Region, ...]


def _union(a: Box, b: Box) -> Box:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _near(a: Box, b: Box, gap: int) -> bool:
    return (a[0] <= b[2] + gap and b[0] <= a[2] + gap
            and a[1] <= b[3] + gap and b[1] <= a[3] + gap)


def _inside(a: Box, b: Box, margin: int) -> bool:
    return (a[0] >= b[0] - margin and a[1] >= b[1] - margin
            and a[2] <= b[2] + margin and a[3] <= b[3] + margin)


def _pen_boxes(strokes: list, gap: int) -> list[tuple[Box, int]]:
    groups: list[tuple[Box, int]] = []
    for stroke in strokes:
        points = [(x, y) for x, y in stroke
                  if 0 <= x < CANVAS_W and 0 <= y < CANVAS_H]
        if not points:
            continue
        xs, ys = zip(*points)
        box = min(xs), min(ys), max(xs) + 1, max(ys) + 1
        count = 1
        # Merge repeatedly: the new stroke may connect two older regions.
        i = 0
        while i < len(groups):
            other, n = groups[i]
            if _near(box, other, gap):
                box, count = _union(box, other), count + n
                groups.pop(i)
                i = 0
            else:
                i += 1
        groups.append((box, count))
    return groups


def observe(after: Image.Image, strokes: list, *, before: Image.Image | None = None,
            frame_id: str | None = None, pen_gap: int = 40) -> Workspace:
    """Group recent pen paths and independently changed pixels into regions.

    A screen delta inside a pen region (within pen_gap) is redundant and is
    omitted; one reaching past it is kept.
    An absent before frame is not evidence of a blank prior page.
    """
    if after.size != (CANVAS_W, CANVAS_H) or (before is not None and before.size != after.size):
        raise ValueError("workspace frames must match the visible screen dimensions")
    if pen_gap < 0:
        raise ValueError("pen_gap must not be negative")
    pen_boxes = _pen_boxes(strokes, pen_gap)
    sources = [("pen", box, count) for box, count in pen_boxes]
    if before is not None:
        changed = page_diff(before, after)
        if changed is not None:
            # page_diff returns inclusive maxima; this contract uses half-open.
            box = changed[0], changed[1], changed[2] + 1, changed[3] + 1
            # Redundant only when inside a pen region (+ pen_gap for stroke
            # width): the union box may also hold a distant edit.
            if not any(_near(pen_box, box, 0) and _inside(box, pen_box, pen_gap)
                       for pen_box, _ in pen_boxes):
                sources.append(("screen_delta", box, 0))
    mask = ink_mask(after)
    sources.sort(key=lambda item: (item[1][1], item[1][0], item[0]))
    regions = tuple(Region(src, box, count,
                           float(mask[box[1]:box[3], box[0]:box[2]].mean()))
                    for src, box, count in sources)
    return Workspace(frame_id, after.size, regions)
