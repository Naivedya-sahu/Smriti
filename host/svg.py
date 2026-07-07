"""
svg.py — minimal SVG → pen strokes (zero deps).

The AI can emit `<svg viewBox="0 0 W H">...</svg>` blocks for freeform
diagrams; this turns them into lamp strokes. Supported (stroke outlines
only, fills ignored):

    <line> <rect> <circle> <ellipse> <polyline> <polygon>
    <path d="..."> with M m L l H h V v C c Q q Z z

Layer test CLI:
    python host/svg.py selfcheck
"""

from __future__ import annotations

import math
import re
import sys

Pt = tuple[float, float]


def _flat_cubic(p0: Pt, p1: Pt, p2: Pt, p3: Pt, n: int = 16) -> list[Pt]:
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
                    u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]))
    return out


def _flat_quad(p0: Pt, p1: Pt, p2: Pt, n: int = 12) -> list[Pt]:
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u**2 * p0[0] + 2 * u * t * p1[0] + t**2 * p2[0],
                    u**2 * p0[1] + 2 * u * t * p1[1] + t**2 * p2[1]))
    return out


def _path_strokes(d: str) -> list[list[Pt]]:
    # every path command letter must be tokenized, else its coords get
    # swallowed by the previous command's implicit-repeat and over-read
    tok = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|-?\d*\.?\d+(?:e-?\d+)?", d)
    strokes: list[list[Pt]] = []
    cur: list[Pt] = []
    x = y = sx = sy = 0.0
    pcx = pcy = None          # previous cubic/quad control point (for S/T)
    i, cmd = 0, ""

    def take(n):
        """Bounds-safe: a malformed/truncated path returns 0s instead of
        crashing — a slightly-wrong drawing beats killing the whole reply."""
        nonlocal i
        vals = [float(tok[i + k]) if i + k < len(tok) else 0.0 for k in range(n)]
        i += n
        return vals

    while i < len(tok):
        if re.match(r"[A-Za-z]", tok[i]):
            cmd = tok[i]
            i += 1
        rel = cmd.islower()
        if cmd in "Mm":
            dx, dy = take(2)
            if rel:
                dx, dy = x + dx, y + dy
            if len(cur) > 1:
                strokes.append(cur)
            x, y = sx, sy = dx, dy
            cur = [(x, y)]
            pcx = pcy = None
            cmd = "l" if rel else "L"           # subsequent pairs are lineto
        elif cmd in "Ll":
            dx, dy = take(2)
            x, y = (x + dx, y + dy) if rel else (dx, dy)
            cur.append((x, y)); pcx = pcy = None
        elif cmd in "Hh":
            (dx,) = take(1)
            x = x + dx if rel else dx
            cur.append((x, y)); pcx = pcy = None
        elif cmd in "Vv":
            (dy,) = take(1)
            y = y + dy if rel else dy
            cur.append((x, y)); pcx = pcy = None
        elif cmd in "CcSs":
            if cmd in "Cc":
                v = take(6)
                if rel:
                    v = [v[0]+x, v[1]+y, v[2]+x, v[3]+y, v[4]+x, v[5]+y]
                c1, c2, end = (v[0], v[1]), (v[2], v[3]), (v[4], v[5])
            else:                               # S/s: reflect prev control
                v = take(4)
                if rel:
                    v = [v[0]+x, v[1]+y, v[2]+x, v[3]+y]
                c1 = (2*x - pcx, 2*y - pcy) if pcx is not None else (x, y)
                c2, end = (v[0], v[1]), (v[2], v[3])
            cur += _flat_cubic((x, y), c1, c2, end)
            pcx, pcy = c2; x, y = end
        elif cmd in "QqTt":
            if cmd in "Qq":
                v = take(4)
                if rel:
                    v = [v[0]+x, v[1]+y, v[2]+x, v[3]+y]
                ctrl, end = (v[0], v[1]), (v[2], v[3])
            else:                               # T/t: reflect prev control
                v = take(2)
                if rel:
                    v = [v[0]+x, v[1]+y]
                ctrl = (2*x - pcx, 2*y - pcy) if pcx is not None else (x, y)
                end = (v[0], v[1])
            cur += _flat_quad((x, y), ctrl, end)
            pcx, pcy = ctrl; x, y = end
        elif cmd in "Aa":                       # arc: approximate as line to end
            v = take(7)
            end = (x + v[5], y + v[6]) if rel else (v[5], v[6])
            cur.append(end); x, y = end; pcx = pcy = None
        elif cmd in "Zz":
            cur.append((sx, sy)); x, y = sx, sy; pcx = pcy = None
        else:                                   # unknown token: skip it
            i += 1
    if len(cur) > 1:
        strokes.append(cur)
    return strokes


def _attr(el: str, name: str, default: float = 0.0) -> float:
    m = re.search(rf'{name}="(-?[\d.]+)"', el)
    return float(m.group(1)) if m else default


def svg_to_strokes(svg: str, x: int = 100, y: int = 100,
                   max_w: int = 1200, max_h: int = 900) -> list[list[tuple[int, int]]]:
    """Parse an SVG snippet into pen strokes, scaled to fit max_w×max_h and
    placed at (x, y)."""
    raw: list[list[Pt]] = []
    for el in re.findall(r"<(?:line|rect|circle|ellipse|polyline|polygon|path)\b[^>]*/?>",
                         svg, flags=re.I):
        tag = re.match(r"<(\w+)", el).group(1).lower()
        if tag == "line":
            raw.append([(_attr(el, "x1"), _attr(el, "y1")),
                        (_attr(el, "x2"), _attr(el, "y2"))])
        elif tag == "rect":
            rx, ry = _attr(el, "x"), _attr(el, "y")
            w, h = _attr(el, "width"), _attr(el, "height")
            raw.append([(rx, ry), (rx + w, ry), (rx + w, ry + h),
                        (rx, ry + h), (rx, ry)])
        elif tag in ("circle", "ellipse"):
            cx, cy = _attr(el, "cx"), _attr(el, "cy")
            rx = _attr(el, "r") or _attr(el, "rx")
            ry = _attr(el, "r") or _attr(el, "ry")
            raw.append([(cx + rx * math.cos(a), cy + ry * math.sin(a))
                        for a in [k * math.tau / 36 for k in range(37)]])
        elif tag in ("polyline", "polygon"):
            m = re.search(r'points="([^"]+)"', el)
            if m:
                nums = [float(v) for v in re.findall(r"-?[\d.]+", m.group(1))]
                pts = list(zip(nums[::2], nums[1::2]))
                if tag == "polygon" and pts:
                    pts.append(pts[0])
                if len(pts) > 1:
                    raw.append(pts)
        elif tag == "path":
            m = re.search(r'd="([^"]+)"', el)
            if m:
                raw += _path_strokes(m.group(1))
    if not raw:
        return []
    xs = [px for s in raw for px, _ in s]
    ys = [py for s in raw for _, py in s]
    w, h = max(xs) - min(xs) or 1.0, max(ys) - min(ys) or 1.0
    scale = min(max_w / w, max_h / h, 4.0)     # cap: tiny svgs shouldn't blow up
    scaled = [[(int(x + (px - min(xs)) * scale), int(y + (py - min(ys)) * scale))
               for px, py in s] for s in raw]
    # densify: a rect/polygon is only a few waypoints, and the downstream
    # lamp decimation (every Nth point) would delete whole edges. Resample
    # each segment to ~4px so no edge can be dropped and lines stay solid.
    return [_densify(s, 4) for s in scaled]


def _densify(stroke: list, d: int = 4) -> list:
    """Resample a polyline so consecutive points are <= d px apart, keeping
    every original vertex. Survives waypoint decimation without losing edges."""
    if len(stroke) < 2:
        return stroke
    out = [stroke[0]]
    for (x0, y0), (x1, y1) in zip(stroke, stroke[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) // d))
        for i in range(1, n + 1):
            t = i / n
            out.append((int(x0 + (x1 - x0) * t), int(y0 + (y1 - y0) * t)))
    return out


def selfcheck() -> None:
    svg = '''<svg viewBox="0 0 100 60">
      <rect x="10" y="10" width="30" height="20"/>
      <circle cx="70" cy="20" r="10"/>
      <line x1="40" y1="20" x2="60" y2="20"/>
      <path d="M10 50 L30 40 Q40 35 50 40 C60 45 70 45 80 40 Z"/>
      <polyline points="85,50 90,45 95,50"/>
    </svg>'''
    s = svg_to_strokes(svg, x=100, y=100, max_w=600)
    assert len(s) == 5, f"expected 5 strokes, got {len(s)}"
    xs = [px for st in s for px, _ in st]
    ys = [py for st in s for _, py in st]
    assert min(xs) >= 100 and min(ys) >= 100, "offset wrong"
    assert max(xs) <= 100 + 600, "scale overflow"
    assert len(s[3]) > 20, "curves not flattened"
    # rect must survive decimation: densified so every edge keeps points
    rect = s[0]
    assert len(rect) > 40, f"rect not densified ({len(rect)} pts) — edges would drop"
    dec = rect[::3]        # simulate lamp decimation
    xr = [p[0] for p in dec]; yr = [p[1] for p in dec]
    assert max(xr) - min(xr) > 50 and max(yr) - min(yr) > 30, \
        "rect collapsed under decimation — edges missing"
    # smooth-curve path (T/S) must not crash — real AI Bode-plot output
    bode = svg_to_strokes('<svg viewBox="0 0 400 300"><polyline points="50,250 350,250"/>'
                          '<path d="M 50,70 Q 150,70 150,150 T 350,230"/></svg>')
    assert len(bode) == 2 and len(bode[1]) > 20, f"smooth path broke: {bode}"
    # truncated path must return, not raise
    assert svg_to_strokes('<svg viewBox="0 0 10 10"><path d="M0 0 C 1 1 2"/></svg>') is not None
    print(f"svg selfcheck ok: {len(s)} strokes, rect {len(rect)} pts survives "
          f"decimation, T/S paths + truncated paths safe, "
          f"bbox {min(xs)},{min(ys)} - {max(xs)},{max(ys)}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selfcheck":
        selfcheck()
    else:
        sys.exit(__doc__)
