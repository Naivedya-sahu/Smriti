"""
ink.py — Smriti handwriting engine (v0.1.1)

text string → styled glyph raster (Pillow) → Zhang-Suen thinning →
stroke trace → ordered pen paths for lamp.

Technique from MaximeRivest/riddle (Rust, Paper Pro), reimplemented
in Python for RM2. No AI, no input — pure text-to-ink.

CLI:
    python host/ink.py preview "hello monke" --style cursive -o preview.png
    python host/ink.py compile --style cursive -o device/fonts/cursive.json
    python host/ink.py selfcheck
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
EM = 1000          # stroke-font coordinate units per em
RASTER_PX = 256    # glyph raster size for compile (big = clean skeleton)
CHARSET = [chr(c) for c in range(32, 127)]


# ── styles ────────────────────────────────────────────────────────────────────

def load_styles(path: Path | None = None) -> dict:
    with open(path or REPO / "styles.toml", "rb") as f:
        return tomllib.load(f)["style"]


def load_font(style: dict, px: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(REPO / style["font"]), px)
    if "wght" in style:
        try:
            font.set_variation_by_axes([style["wght"]])
        except OSError:
            pass  # static font, no variation axes
    return font


# ── raster ────────────────────────────────────────────────────────────────────

def rasterize(text: str, font: ImageFont.FreeTypeFont, slant_deg: float = 0.0) -> np.ndarray:
    """Render text to a bool array (True = ink), optionally sheared."""
    l, t, r, b = font.getbbox(text)
    if r <= l or b <= t:
        return np.zeros((1, 1), dtype=bool)
    pad = 4
    img = Image.new("L", (r - l + 2 * pad, b + 2 * pad), 0)
    ImageDraw.Draw(img).text((pad - l, pad), text, font=font, fill=255)
    if slant_deg:
        sh = np.tan(np.radians(slant_deg))
        w, h = img.size
        img = img.transform(
            (int(w + abs(sh) * h) + 1, h), Image.AFFINE,
            (1, sh, -sh * h if sh > 0 else 0, 0, 1, 0), fillcolor=0)
    return np.asarray(img) > 128


# ── Zhang-Suen thinning ───────────────────────────────────────────────────────

def skeletonize(img: np.ndarray) -> np.ndarray:
    """Zhang-Suen thinning, vectorized. img: bool, True=ink → 1px skeleton."""
    sk = img.copy()
    while True:
        changed = False
        for sub in (0, 1):
            P = np.pad(sk, 1).astype(np.uint8)
            p2 = P[:-2, 1:-1]; p3 = P[:-2, 2:];  p4 = P[1:-1, 2:]
            p5 = P[2:, 2:];    p6 = P[2:, 1:-1]; p7 = P[2:, :-2]
            p8 = P[1:-1, :-2]; p9 = P[:-2, :-2]
            ring = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            B = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            A = sum(((ring[i] == 0) & (ring[i + 1] == 1)).astype(np.uint8)
                    for i in range(8))
            if sub == 0:
                cond = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                cond = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            kill = sk & (B >= 2) & (B <= 6) & (A == 1) & cond
            if kill.any():
                sk[kill] = False
                changed = True
        if not changed:
            return sk


# ── stroke tracing ────────────────────────────────────────────────────────────

def trace(skel: np.ndarray, decimate: int = 2) -> list[list[tuple[int, int]]]:
    """1px skeleton → ordered polylines [(x,y),...]. Endpoint-first walk;
    junctions consumed greedily. ponytail: O(n^2) endpoint rescan per stroke,
    fine at glyph/line scale; spatial index if pages get slow."""
    live = {(int(r), int(c)) for r, c in zip(*np.nonzero(skel))}

    def nbrs(p):
        r, c = p
        return [(r + dr, c + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                if (dr or dc) and (r + dr, c + dc) in live]

    strokes = []
    while live:
        ends = [p for p in live if len(nbrs(p)) <= 1]
        cur = min(ends) if ends else min(live)
        path = [cur]
        live.discard(cur)
        while True:
            nxt = nbrs(cur)
            if not nxt:
                break
            # prefer 4-connected continuation, then diagonal
            cur = min(nxt, key=lambda q: abs(q[0] - path[-1][0]) + abs(q[1] - path[-1][1]))
            live.discard(cur)
            path.append(cur)
        pts = [(c, r) for r, c in path]
        if decimate > 1 and len(pts) > 2:
            pts = pts[::decimate] + ([pts[-1]] if (len(pts) - 1) % decimate else [])
        if len(pts) >= 2:
            strokes.append(pts)
    strokes.sort(key=lambda s: (min(p[0] for p in s), min(p[1] for p in s)))
    return strokes


# ── high-level: text → page strokes ──────────────────────────────────────────

def render(text: str, style: dict, x: int = 100, y: int = 150,
           max_w: int = 1300) -> list[list[tuple[int, int]]]:
    """Full line(s) → strokes in page coords. Whole-line raster keeps
    cursive joins (Pillow shapes the run as one piece)."""
    size = style.get("size", 60)
    font = load_font(style, size)
    dec = style.get("decimate", 2)
    line_h = int(size * style.get("line_height", 1.5))
    out = []
    cy = y
    for line in _wrap(text, font, max_w - x):
        if line.strip():
            bmp = rasterize(line, font, style.get("slant", 0.0))
            for s in trace(skeletonize(bmp), dec):
                out.append([(px + x, py + cy) for px, py in s])
        cy += line_h
    return out


def _wrap(text: str, font, width: int) -> list[str]:
    lines = []
    for raw in text.split("\n"):
        cur = ""
        for word in raw.split(" "):
            cand = (cur + " " + word).strip()
            if cur and font.getbbox(cand)[2] > width:
                lines.append(cur)
                cur = word
            else:
                cur = cand
        lines.append(cur)
    return lines


# ── stroke-font compile (device asset) ───────────────────────────────────────

def compile_strokefont(style: dict) -> str:
    """Per-glyph strokes → flat .sf text for the busybox-awk device replayer.

        F <em> <line_height> <pressure> <size>
        G <ascii-code> <advance-em>
        S x1 y1 x2 y2 ...          (stroke, int em coords, y down)

    ponytail: per-glyph = no contextual shaping; cursive joins are the font's
    entry/exit design only. Whole-line host render if joins look broken."""
    font = load_font(style, RASTER_PX)
    scale = EM / RASTER_PX
    lines = [f"F {EM} {style.get('line_height', 1.5)} "
             f"{style.get('pressure', 2400)} {style.get('size', 60)}"]
    for ch in CHARSET:
        adv = round(font.getlength(ch) * scale)
        lines.append(f"G {ord(ch)} {adv}")
        if not ch.strip():
            continue
        bmp = rasterize(ch, font, style.get("slant", 0.0))
        for s in trace(skeletonize(bmp), style.get("decimate", 2)):
            lines.append("S " + " ".join(
                f"{round(x * scale)} {round(y * scale)}" for x, y in s))
    return "\n".join(lines) + "\n"


# ── preview (proof without device) ───────────────────────────────────────────

def preview(strokes: list[list[tuple[int, int]]], out: Path,
            w: int = 1404, h: int = 1872) -> None:
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    for s in strokes:
        d.line(s, fill=0, width=3)
    img.save(out)


# ── self-check ────────────────────────────────────────────────────────────────

def selfcheck() -> None:
    # skeleton of a 10px-thick bar collapses to ~1px line
    bar = np.zeros((20, 60), dtype=bool)
    bar[5:15, 5:55] = True
    sk = skeletonize(bar)
    assert sk.sum() < bar.sum() / 4, "thinning failed"
    rows = np.nonzero(sk.any(axis=1))[0]
    assert len(rows) <= 3, f"skeleton too thick: {len(rows)} rows"
    st = trace(sk, decimate=1)
    assert st and max(len(s) for s in st) >= 40, "trace lost the bar"
    # a glyph produces strokes for every style
    for name, style in load_styles().items():
        s = render("ab", style, x=0, y=0)
        assert s, f"style {name}: no strokes"
    print("selfcheck OK")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preview")
    p.add_argument("text")
    p.add_argument("--style", default="cursive")
    p.add_argument("-o", "--out", default="preview.png")
    c = sub.add_parser("compile")
    c.add_argument("--style", default="cursive")
    c.add_argument("-o", "--out", required=True)
    sub.add_parser("selfcheck")
    a = ap.parse_args()

    if a.cmd == "selfcheck":
        selfcheck()
        return
    style = load_styles()[a.style]
    if a.cmd == "preview":
        preview(render(a.text, style), Path(a.out))
        print(f"wrote {a.out}")
    elif a.cmd == "compile":
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(compile_strokefont(style), newline="\n")
        print(f"wrote {a.out} ({Path(a.out).stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
