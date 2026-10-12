"""Pure collision-safe placement on the 1404x1872 notebook canvas."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

W, H = 1404, 1872
UI_BOXES = ((0, 0, 120, H), (0, 0, W, 100), (1290, 1760, W, H),
            (560, 1810, 850, H), (1335, 0, 1365, H))


@dataclass(frozen=True)
class Occ:
    cell: int
    busy: np.ndarray

    def free(self, box):
        x0, y0, x1, y1 = box
        x0, y0 = max(0, x0 // self.cell), max(0, y0 // self.cell)
        x1, y1 = min(self.busy.shape[1], (x1 + self.cell - 1) // self.cell), min(self.busy.shape[0], (y1 + self.cell - 1) // self.cell)
        return x0 < x1 and y0 < y1 and not self.busy[y0:y1, x0:x1].any()


@dataclass(frozen=True)
class Spot:
    x: int
    y: int
    k: float
    why: str


def _dilate(a, r):
    p = np.pad(a, r)
    out = np.zeros_like(a)
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            out |= p[dy:dy + a.shape[0], dx:dx + a.shape[1]]
    return out


def occupancy(shot, extra_strokes=(), cell=24):
    """Dilated Navy/Monke ink plus UI exclusion, compressed to a cell grid."""
    if hasattr(shot, "convert"):
        a = np.asarray(shot.convert("L").resize((W, H))) < 100
    else:
        a = np.asarray(shot, dtype=bool)
        if a.shape != (H, W):
            raise ValueError("occupancy mask must be 1872x1404")
    for stroke in extra_strokes:
        for x, y in stroke:
            if 0 <= x < W and 0 <= y < H:
                a[max(0, y - 1):y + 2, max(0, x - 1):x + 2] = True
    a = _dilate(a, 20)
    for x0, y0, x1, y1 in UI_BOXES:
        a[y0:y1, x0:x1] = True
    rows, cols = (H + cell - 1) // cell, (W + cell - 1) // cell
    pad = np.pad(a, ((0, rows * cell - H), (0, cols * cell - W)), constant_values=True)
    return Occ(cell, pad.reshape(rows, cell, cols, cell).any((1, 3)))


def free_rects(occ, k=4, min_w=300, min_h=150):
    """Largest empty grid rectangles, greedily non-overlapping, E1 first."""
    a, c = ~occ.busy, occ.cell
    candidates = []
    heights = np.zeros(a.shape[1], int)
    for y, row in enumerate(a):
        heights = np.where(row, heights + 1, 0)
        stack = []
        for x in range(a.shape[1] + 1):
            h = heights[x] if x < a.shape[1] else 0
            start = x
            while stack and stack[-1][1] > h:
                left, high = stack.pop()
                candidates.append((high * (x - left), (left * c, (y - high + 1) * c, x * c, (y + 1) * c)))
                start = left
            if not stack or stack[-1][1] < h:
                stack.append((start, h))
    out = []
    for _, box in sorted(candidates, reverse=True):
        x0, y0, x1, y1 = box
        x0, y0, x1, y1 = max(120, x0), max(100, y0), min(W, x1), min(H, y1)
        box = x0, y0, x1, y1
        if x1 - x0 < min_w or y1 - y0 < min_h:
            continue
        if any(not (x1 <= b[0] or b[2] <= x0 or y1 <= b[1] or b[3] <= y0) for _, b in out):
            continue
        out.append((f"E{len(out) + 1}", box))
        if len(out) == k:
            break
    return out


def resolve(directive, regions, occ, size, floor=0, bottom=H):
    """First collision-free directive spot; k is always 1, .85, or .7."""
    w, h = size
    how, rid = directive.get("how", "below"), directive.get("id")
    target = regions.get(rid) if rid else None
    free = [(key, box) for key, box in regions.items() if key.startswith("E")]
    if not free:
        free = free_rects(occ)
    candidates = []
    if target:
        x0, y0, x1, y1 = target
        if how == "beside": candidates = [(x1 + 40, y0, f"beside {rid}"),
                                            (x1 + 40 + occ.cell, y0, f"beside {rid}"),
                                            (x0 - 40 - w, y0, f"left-of {rid}"),
                                            (x0, y1 + 40, f"below {rid}")]
        elif how == "left-of": candidates = [(x0 - 40 - w, y0, f"left-of {rid}"),
                                               (x0 - 40 - occ.cell - w, y0, f"left-of {rid}"),
                                               (x0, y1 + 40, f"below {rid}")]
        elif how == "above": candidates = [(x0, y0 - 40 - h, f"above {rid}")]
        else: candidates = [(x0, y1 + 40, f"below {rid}"), (x1 + 40, y0, f"beside {rid}")]
    if how in ("free", "in") or not candidates:
        choices = ([p for p in free if p[0] == rid] if how == "in" and rid else free)
        candidates += [(b[0] + 20, b[1] + 20, f"free {name}") for name, b in choices]
    candidates.append((120, max(100, floor + 40), "fallback below"))
    for x, y, why in candidates:
        for scale in (1.0, .85, .7):
            bw, bh = int(round(w * scale)), int(round(h * scale))
            box = x, y, x + bw, y + bh
            if x >= 120 and y >= 100 and box[2] <= W and box[3] <= bottom and occ.free(box):
                return Spot(int(x), int(y), scale, why)
    return None


def pack(sizes, occ, floor=0, bottom=H, pair=(), gap=40):
    """Place boxes of `sizes` (w, h) in reading order on one page: below the
    lowest box so far at the left margin; a box flagged in `pair` first tries
    beside the previous one (two figures per row); then any free rectangle
    left. Each placed box (+gap/2) is marked busy, so no later box overlaps
    it, and every box lies in empty, non-UI page space. k is 1, .85 or .7
    (as resolve). Returns [Spot | None]; None = no room (stays in the log)."""
    busy, half = occ.busy.copy(), gap // 2
    spots, prev, low = [], None, max(100, int(floor))
    for i, (w, h) in enumerate(sizes):
        cur = Occ(occ.cell, busy)
        cands = []
        if prev and i < len(pair) and pair[i]:
            cands.append((prev[2] + gap, prev[1], "beside previous"))
        cands.append((120, low + gap if prev or floor else low, "below"))
        cands += [(b[0] + 20, b[1] + 20, f"free {name}")
                  for name, b in free_rects(cur, k=8, min_w=min(300, w), min_h=min(150, h))]
        spot = None
        for x, y, why in cands:
            for scale in (1.0, .85, .7):
                bw, bh = int(round(w * scale)), int(round(h * scale))
                box = x, y, x + bw, y + bh
                if x >= 120 and y >= 100 and box[2] <= W and box[3] <= bottom and cur.free(box):
                    spot = Spot(int(x), int(y), scale, why)
                    break
            if spot:
                break
        spots.append(spot)
        if spot is None:
            continue
        prev = (spot.x, spot.y, spot.x + int(round(w * spot.k)), spot.y + int(round(h * spot.k)))
        low = max(low, prev[3])
        c = occ.cell
        x0, y0 = max(0, (prev[0] - half) // c), max(0, (prev[1] - half) // c)
        busy[y0:(prev[3] + half + c - 1) // c, x0:(prev[2] + half + c - 1) // c] = True
    return spots


def shift(strokes, x, y, k=1.0):
    """Translate/scales strokes from their own top-left without mutation."""
    points = [p for s in strokes for p in s]
    if not points:
        return []
    ox, oy = min(p[0] for p in points), min(p[1] for p in points)
    out = []
    for stroke in strokes:
        pts = [(int(round(x + (px - ox) * k)), int(round(y + (py - oy) * k))) for px, py in stroke]
        try:
            out.append(type(stroke)(pts, color=stroke.color))
        except (AttributeError, TypeError):
            out.append(pts)
    return out
