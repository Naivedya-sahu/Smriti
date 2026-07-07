"""
demo_diode.py — full-stack render demo / smoke test. Inks a complete note on
the diode that exercises every output path Smriti has:

    text      handwriting prose (stroke font)
    equation  LaTeX math, typeset  (Shockley equation)
    circuit   circuitikz           (half-wave rectifier loop)
    diagram   inline SVG -> strokes (PN junction + I-V curve)

    ssh monke 'cd ~/smriti && SMRITI_SCREEN_URL=... uv run python host/demo_diode.py'
    # options: --host rm2  --style cursive  --keep (don't clear the page first)

No AI involved — pure system render, so it doubles as an on-device check
that fonts, TinyTeX and the SVG tracer all work end to end.
"""
from __future__ import annotations

import argparse
import os

from ink import load_styles, render
from svg import svg_to_strokes
from tex import tex_to_strokes
from write import to_lamp
from screen import grab, ink_floor

LEFT = 100
PAGE_BOTTOM = 1780


def erase_region(host: str, x0: int, y0: int, x1: int, y1: int,
                 sweep: int = 14) -> None:
    """Serpentine eraser wipe of a rectangle (clears the page for the demo)."""
    from monke import lamp
    cmds, y, left = [f"erase down {x0} {y0}"], y0, True
    while y <= y1:
        cmds.append(f"erase move {x1 if left else x0} {y}")
        y += sweep
        if y <= y1:
            cmds.append(f"erase move {x1 if left else x0} {y}")
        left = not left
    cmds.append("erase up")
    lamp("\n".join(cmds) + "\n", host)


# PN junction: P box | depletion | N box, holes (o) left, electrons drift, a
# forward-current arrow underneath. No <text> (the tracer ignores it) — the
# P / N / depletion labels are inked as prose separately.
JUNCTION_SVG = """<svg viewBox="0 0 460 180">
  <rect x="10" y="20" width="180" height="110"/>
  <rect x="270" y="20" width="180" height="110"/>
  <rect x="190" y="20" width="80" height="110"/>
  <circle cx="55" cy="55" r="7"/><circle cx="105" cy="80" r="7"/>
  <circle cx="150" cy="50" r="7"/><circle cx="80" cy="105" r="7"/>
  <circle cx="330" cy="55" r="4"/><circle cx="380" cy="85" r="4"/>
  <circle cx="420" cy="50" r="4"/><circle cx="355" cy="105" r="4"/>
  <line x1="30" y1="160" x2="430" y2="160"/>
  <polyline points="410,150 430,160 410,170"/>
</svg>"""

# I-V curve: axes + exponential forward branch + flat reverse branch.
IV_SVG = """<svg viewBox="0 0 320 200">
  <line x1="20" y1="100" x2="300" y2="100"/>
  <line x1="160" y1="10" x2="160" y2="190"/>
  <line x1="20" y1="98" x2="160" y2="102"/>
  <path d="M160 100 C 200 100 215 95 230 60 C 240 35 250 15 260 12"/>
</svg>"""


def build(style) -> list:
    """Stack every block top-to-bottom, return all strokes."""
    out: list = []
    y = 110

    def prose(text, size=44):
        nonlocal y, out
        st = dict(style)
        st["size"] = size
        seg = render(text, st, x=LEFT, y=y)
        out += seg
        y = (max(py for s in seg for _, py in s) if seg else y) + 26

    def block(seg, gap=30):
        nonlocal y, out
        if seg:
            out += seg
            y = max(py for s in seg for _, py in s) + gap

    prose("DIODE - one way valve for current.", size=60)
    prose("PN junction: holes meet electrons, depletion zone forms.")
    block(svg_to_strokes(JUNCTION_SVG, x=LEFT + 40, y=y, max_w=780, max_h=230))
    prose("left P (holes), right N (electrons), middle depletion.", size=38)
    prose("Shockley equation - current vs voltage:")
    block(tex_to_strokes(r"$I = I_S\left(e^{V/nV_T} - 1\right)$", x=LEFT, y=y + 15, max_w=900))
    prose("Forward bias conducts, reverse blocks. I-V curve:")
    block(svg_to_strokes(IV_SVG, x=LEFT + 40, y=y, max_w=560, max_h=155))
    prose("Half-wave rectifier - diode passes one half:")
    block(tex_to_strokes(
        r"\begin{circuitikz}\draw (0,0) to[sV, l=$v_s$] (0,3) -- (2,3) "
        r"to[D, l=$D$] (4,3) to[R, l=$R_L$] (4,0) -- (0,0);\end{circuitikz}",
        x=LEFT, y=y + 15, max_w=950))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="rm2")
    ap.add_argument("--style", default="cursive")
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--keep", action="store_true", help="don't clear the page")
    a = ap.parse_args()
    style = load_styles()[a.style]

    if not a.keep:
        img = grab(os.environ.get("SMRITI_SCREEN_URL"))
        fl = ink_floor(img) if img is not None else 1780
        if fl > 40:
            print(f"clearing existing ink (floor {fl})…", flush=True)
            erase_region(a.host, 20, 20, 1380, min(fl + 40, 1840))

    strokes = build(style)
    bottom = max(py for s in strokes for _, py in s)
    print(f"note = {len(strokes)} strokes, y 120-{bottom}", flush=True)
    if bottom > PAGE_BOTTOM:
        print(f"WARNING: note height {bottom} exceeds page bottom {PAGE_BOTTOM}",
              flush=True)
    from monke import lamp
    lamp(to_lamp(strokes, style.get("pressure", 2400), a.step), a.host)
    print("diode note inked.", flush=True)


if __name__ == "__main__":
    main()
