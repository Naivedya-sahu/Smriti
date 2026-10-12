"""Set-of-Mark labels for the reader overview. Pure Pillow drawing."""
from __future__ import annotations

from PIL import ImageDraw, ImageFont


def overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _colour(rid):
    if rid == "F": return (220, 30, 30)
    if rid.startswith("N"): return (30, 90, 220)
    if rid.startswith("E"): return (125, 125, 125)
    return (25, 145, 65)


def mark(image, scale, regions, free=(), return_tags=False):
    """Overlay region/free boxes and non-overlapping ID tags, returning a copy."""
    out = image.copy().convert("RGB")
    draw, font, tags = ImageDraw.Draw(out), ImageFont.load_default(size=14), {}
    entries = list((regions or {}).items()) + list(free or [])
    occupied = []
    for rid, box in entries:
        x0, y0, x1, y1 = [int(round(v * scale)) for v in box]
        x0, y0 = max(0, x0), max(0, y0); x1, y1 = min(out.width - 1, x1), min(out.height - 1, y1)
        colour = _colour(rid)
        if rid.startswith("E"):
            for i in range(x0, x1, 8): draw.line((i, y0, min(i + 4, x1), y0), fill=colour, width=2)
            for i in range(x0, x1, 8): draw.line((i, y1, min(i + 4, x1), y1), fill=colour, width=2)
            for i in range(y0, y1, 8): draw.line((x0, i, x0, min(i + 4, y1)), fill=colour, width=2)
            for i in range(y0, y1, 8): draw.line((x1, i, x1, min(i + 4, y1)), fill=colour, width=2)
        else:
            draw.rectangle((x0, y0, x1, y1), outline=colour, width=3 if rid == "F" else 2)
        bb = draw.textbbox((0, 0), rid, font=font); tw, th = bb[2] - bb[0] + 6, bb[3] - bb[1] + 5
        candidates = [(x0, y0 - th), (x0, y0), (x1 - tw, y0), (x0, y1 - th)]
        best = None
        for tx, ty in candidates:
            tx, ty = min(max(0, tx), out.width - tw), min(max(0, ty), out.height - th)
            candidate = (tx, ty, tx + tw, ty + th)
            if not any(overlap(candidate, old) for old in occupied): best = candidate; break
        if best is None:
            tx, ty = 0, min(out.height - th, len(occupied) * th)
            best = (tx, ty, tx + tw, ty + th)
        occupied.append(best); tags[rid] = best
        draw.rectangle(best, fill="white", outline="black", width=1)
        draw.text((best[0] + 3, best[1] + 2), rid, fill="black", font=font)
    return (out, tags) if return_tags else out
