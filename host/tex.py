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
import re
import subprocess

from deps import ssh_argv
import sys
import tempfile
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium

from ink import Tinted, color_masks, skeletonize, trace, visible_dots


def _pdflatex() -> str:
    """pdflatex by deps.pdflatex(): configured ([tex] pdflatex, the user's
    existing MiKTeX/TeX Live) > SMRITI_PDFLATEX > bundled TinyTeX > (dev only)
    PATH / ~/.TinyTeX. '' when none — the installed app never silently uses
    a PATH TeX the user didn't pick."""
    import deps
    return deps.pdflatex()[0]


def have_pdflatex() -> bool:
    import shutil
    p = _pdflatex()
    return bool(p) and (Path(p).is_file() or shutil.which(p) is not None)


TEMPLATE = r"""\documentclass[preview,border=6pt]{standalone}
\usepackage{amsmath,amssymb}
\usepackage{circuitikz}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\begin{document}
%s
\end{document}
"""

DPI = 300

# xochitl gray (pen 1) has no pgf colour of its own: real grey is anti-aliased
# black to the hue split. Curves meant for the gray pen are typeset in this
# keyed green (0,153,0) instead and inked with pen 1 (render_plan curve roles).
GRAY_KEY = "green!60!black"


def pen_masks(rgb: np.ndarray, dark: int = 128) -> dict:
    """ink.color_masks plus the gray key: {0 black, 1 gray, 6 blue, 7 red}."""
    out = color_masks(rgb, dark)
    r, g, b = (rgb[..., i].astype(np.int16) for i in range(3))
    key = ((0.299 * r + 0.587 * g + 0.114 * b) < dark) & (g > r + 60) & (g > b + 60)
    if key.any():
        out[1] = key
        if 0 in out:
            out[0] = out[0] & ~key
            if not out[0].any():
                del out[0]
    return dict(sorted(out.items()))


_LABEL = __import__("re").compile(r"(?<![\w{])([lvia](?:[_^])?)\s*=\s*(\$[^${}]*(?:\{[^{}]*\}[^${}]*)*\$)")


def brace_labels(tex: str) -> str:
    """circuitikz options l=/v=/i=/a= with a math value containing '=' must
    be braced: `l=$R_1=2k\\Omega$` dies with 'Extra }, or forgotten $'
    (pgfkeys splits on the inner '='). Models write it constantly — both
    circuits of a device session failed on it (2026-09-26)."""
    if "circuitikz" not in tex:
        return tex
    return _LABEL.sub(lambda m: f"{m.group(1)}={{{m.group(2)}}}", tex)


def block_src(body: str) -> str:
    """A $$...$$ body as it is compiled: environments as is, maths in $...$.
    The one wrapping rule for both the renderer and the repair check.
    Models also emit \\[..\\], \\(..\\) or $..$ inside $$: unwrapped first, or
    $\\[..\\]$ fails and the answer is never inked (device 2026-09-30)."""
    body = body.strip()
    for a, b in (("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if body.startswith(a) and body.endswith(b) and len(body) > len(a) + len(b):
            body = body[len(a):-len(b)].strip()
    m = re.match(r"\\begin\{(\w+\*?)\}", body)
    if m:
        env = m.group(1)
        # display environments -> inline twin (standalone has no display mode)
        twin = _DISPLAY_TWIN.get(env, env)
        if twin != env:
            inner = body[m.end():]
            inner = re.sub(r"\\end\{" + re.escape(env) + r"\}\s*$", "", inner).strip()
            body = (f"\\begin{{{twin}}} {inner} \\end{{{twin}}}" if twin else inner)
            return f"$\\displaystyle {body}$"
        if env in _MATH_ENVS:                # math-only: needs math mode
            return f"$\\displaystyle {body}$"
        return body                          # tikz, circuitikz, tabular ...: as is
    return f"${body}$"


# live 2026-10-10: ChatGPT writes $$\begin{aligned}..$$ (Claude never did)
_MATH_ENVS = {"aligned", "alignedat", "gathered", "split", "cases", "dcases", "array",
              "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix"}
_DISPLAY_TWIN = {"align": "aligned", "align*": "aligned", "gather": "gathered",
                 "gather*": "gathered", "equation": "", "equation*": "", "multline": "gathered",
                 "multline*": "gathered", "flalign": "aligned", "flalign*": "aligned"}


def tex_to_image(tex: str, dpi: int = DPI, color: bool = False) -> np.ndarray:
    """LaTeX snippet → bool array (True = ink)."""
    tex = brace_labels(tex)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "main.tex"
        src.write_text(TEMPLATE % tex, encoding="utf-8")
        exe = _pdflatex()
        if not exe:
            raise RuntimeError("pdflatex not found: smriti deps set pdflatex <path>")
        import deps
        try:
            r = subprocess.run(
                [exe, "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                cwd=td, capture_output=True, text=True, timeout=180, env=deps.tex_env(exe),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            raise RuntimeError(f"pdflatex failed to start ({exe}): {e}") from e
        pdf = Path(td) / "main.pdf"
        if r.returncode != 0 or not pdf.exists():
            tail = "\n".join(r.stdout.splitlines()[-12:])
            raise RuntimeError(f"pdflatex failed:\n{tail}")
        data = pdf.read_bytes()   # open from memory: no handle on the temp dir
    page = pdfium.PdfDocument(data)[0]
    rgb = np.asarray(page.render(scale=dpi / 72).to_pil().convert("RGB"))
    return rgb if color else (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1]
                              + 0.114 * rgb[..., 2]) < 128


def tex_to_strokes(tex: str, x: int = 100, y: int = 150,
                   max_w: int = 1200, decimate: int = 2
                   ) -> list[list[tuple[int, int]]]:
    """LaTeX → strokes in page coords, scaled to fit max_w."""
    # traced at 2x, scaled back: at 300 dpi the skeleton collapsed x and '.'
    # to 2-point strokes (inked as a 1 px slash / nothing, runs 7-9)
    # one mask per pen colour (run 14: '\addplot[blue]' inked black)
    strokes = []
    for c, img in pen_masks(tex_to_image(tex, DPI * 2, color=True)).items():
        for s in visible_dots([[(px / 2, py / 2) for px, py in s]
                               for s in trace(skeletonize(img), decimate * 2, dots=True)]):
            strokes.append(Tinted(s, c))
    if not strokes:
        return []
    w = max(px for s in strokes for px, _ in s)
    scale = min(1.0, max_w / max(w, 1))
    return [Tinted([(int(px * scale) + x, int(py * scale) + y) for px, py in s], s.color)
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
        subprocess.run([*ssh_argv(a.rm2), "/home/root/.vellum/bin/lamp"],
                       input=cmds.encode("ascii"), check=True)
        print("drawn on device")
    if not a.preview and not a.send:
        sys.exit("nothing to do: pass --preview and/or --send")


if __name__ == "__main__":
    main()
