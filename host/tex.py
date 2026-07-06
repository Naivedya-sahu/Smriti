"""
tex.py — LaTeX / CircuiTikZ → ink strokes on the RM2.

Renders LaTeX through MiKTeX (pdflatex) → pypdfium2 → binary raster, then
reuses the ink.py skeleton→trace pipeline: formulas and circuit diagrams
come out as pen strokes, drawn like everything else Smriti writes.

    python host/tex.py '$V = IR$' --preview out.png
    python host/tex.py '$\\int_0^\\infty e^{-x^2}dx = \\frac{\\sqrt{\\pi}}{2}$' --send
    python host/tex.py '\\begin{circuitikz}\\draw (0,0) to[R=$R_1$] (3,0) to[C=$C_1$] (3,-2) node[ground]{};\\end{circuitikz}' --send

MiKTeX note: first compile may auto-install packages (slow once). If MiKTeX
is freshly installed run `initexmf` first (machine gotcha).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium

from ink import skeletonize, trace

TEMPLATE = r"""\documentclass[preview,border=6pt]{standalone}
\usepackage{amsmath,amssymb}
\usepackage{circuitikz}
\begin{document}
%s
\end{document}
"""

DPI = 300


def tex_to_image(tex: str) -> np.ndarray:
    """LaTeX snippet → bool array (True = ink)."""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "main.tex"
        src.write_text(TEMPLATE % tex, encoding="utf-8")
        r = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
            cwd=td, capture_output=True, text=True, timeout=180)
        pdf = Path(td) / "main.pdf"
        if r.returncode != 0 or not pdf.exists():
            tail = "\n".join(r.stdout.splitlines()[-12:])
            raise RuntimeError(f"pdflatex failed:\n{tail}")
        data = pdf.read_bytes()   # open from memory: no handle on the temp dir
    page = pdfium.PdfDocument(data)[0]
    bmp = page.render(scale=DPI / 72).to_pil().convert("L")
    return np.asarray(bmp) < 128


def tex_to_strokes(tex: str, x: int = 100, y: int = 150,
                   max_w: int = 1200, decimate: int = 2
                   ) -> list[list[tuple[int, int]]]:
    """LaTeX → strokes in page coords, scaled to fit max_w."""
    img = tex_to_image(tex)
    strokes = trace(skeletonize(img), decimate)
    if not strokes:
        return []
    w = max(px for s in strokes for px, _ in s)
    scale = min(1.0, max_w / max(w, 1))
    return [[(int(px * scale) + x, int(py * scale) + y) for px, py in s]
            for s in strokes]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tex")
    ap.add_argument("--x", type=int, default=100)
    ap.add_argument("--y", type=int, default=150)
    ap.add_argument("--max-w", type=int, default=1200)
    ap.add_argument("--preview", metavar="PNG")
    ap.add_argument("--send", action="store_true", help="draw on the RM2")
    ap.add_argument("--rm2", default="rm2")
    ap.add_argument("--pressure", type=int, default=2600)
    a = ap.parse_args()

    strokes = tex_to_strokes(a.tex, a.x, a.y, a.max_w)
    print(f"{len(strokes)} strokes")
    if a.preview:
        from PIL import Image, ImageDraw
        img = Image.new("L", (1404, 1872), 255)
        d = ImageDraw.Draw(img)
        for s in strokes:
            if len(s) > 1:
                d.line(s, fill=0, width=3)
        img.save(a.preview)
        print(f"wrote {a.preview}")
    if a.send:
        from write import to_lamp
        cmds = to_lamp(strokes, a.pressure, step=2)
        subprocess.run(["ssh", a.rm2, "/home/root/.vellum/bin/lamp"],
                       input=cmds.encode("ascii"), check=True)
        print("drawn on device")
    if not a.preview and not a.send:
        sys.exit("nothing to do: pass --preview and/or --send")


if __name__ == "__main__":
    main()
