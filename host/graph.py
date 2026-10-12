"""
graph.py — deterministic plots on paper, overlays on the SAME axes.

The model only READS the equation ("y = sin(3*x), x from 0 to 5"); Python
writes the pgfplots — no model-authored LaTeX to break. A notebook keeps its
last Graph; "add"/"+"/"also" readings draw ONLY the new curve onto it:
limits are fixed at first draw (sampled here), so the axes never move, and
the new curve's strokes = full render minus the old render (pixel diff).

GraphSpec plots (function / stem / pole_zero / freq_response) are built by
host/plots.py as Graph(kind=...) and drawn as vectors in milliseconds; they
register and overlay ("add ... to G2") exactly like these.

Each curve gets a pen colour (xochitl enum, set via the eye's qml-cmd
channel) + a dash style — colours are real in the saved file/app; on the
RM2 panel they show as greys (black 0, blue 29, red 76, gray 144), so the
dash pattern keeps curves apart on e-ink.

    uv run python host/graph.py selfcheck
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

# (xochitl pen colour enum, pgfplots style) per curve, in order
# no dotted styles: dots are too short to survive skeleton->trace (device
# export 2026-09-26 showed a dotted overlay coming out sparse)
STYLES = [(0, "thick"), (7, "thick, dashed"), (6, "thick, dash dot"),
          (1, "thick, dash dot dot")]
COLOR_NAMES = {0: "black", 1: "gray", 2: "white", 6: "blue", 7: "red"}

_FUNCS = {"sin": np.sin, "cos": np.cos, "tan": np.tan, "exp": np.exp,
          "ln": np.log, "log": np.log, "sqrt": np.sqrt, "abs": np.abs,
          "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
          "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh}
_TOKEN = re.compile(r"\s*(\d+\.?\d*(?:e[-+]?\d+)?|[a-z]+|\*\*|[-+*/^(),])", re.I)


def safe_eval(expr: str, x: np.ndarray) -> np.ndarray:
    """Evaluate a pgfplots-style expression over x. Whitelist tokenizer —
    only numbers, x, pi, e and _FUNCS names get through (the expression
    comes from a model / the tablet, never eval raw text)."""
    out, pos = [], 0
    s = expr.strip()
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m:
            raise ValueError(f"bad token at {s[pos:pos + 8]!r}")
        t = m.group(1)
        pos = m.end()
        if t.isalpha():
            tl = t.lower()
            if tl == "x":
                out.append("x")
            elif tl == "pi":
                out.append("pi")
            elif tl == "e":
                out.append("e")
            elif tl in _FUNCS:
                out.append(f"F['{tl}']")
            else:
                raise ValueError(f"unknown name {t!r}")
        else:
            out.append("**" if t == "^" else t)
    code = " ".join(out)
    with np.errstate(all="ignore"):
        y = eval(code, {"__builtins__": {}},            # noqa: S307 — tokens whitelisted
                 {"x": x, "pi": math.pi, "e": math.e, "F": _FUNCS})
    return np.broadcast_to(np.asarray(y, dtype=float), x.shape)


def target(text: str) -> tuple[str | None, str]:
    """'add y = ... to G2' -> ('G2', 'add y = ...'); no label -> (None, text)."""
    m = re.search(r"\s*,?\s*\b(?:to|on|in)\s+(?:graph\s+)?(g\d+)\b", text, re.I)
    if not m:
        return None, text
    return m.group(1).upper(), (text[:m.start()] + text[m.end():]).strip()


def parse(text: str) -> tuple[str, float, float, bool] | None:
    """'[add|+|also] y = <expr>[, x from a to b]' -> (expr, a, b, overlay).
    None when the text isn't a plottable equation."""
    m = re.search(r"(add|\+|also|overlay)?\s*:?\s*\by\s*=\s*(.+?)"
                  r"(?:,?\s*(?:for\s+)?x\s+(?:from|in)\s+\[?\s*(-?[\d.]+)\s*"
                  r"(?:to|,)\s*(-?[\d.]+)\]?)?\s*$", text.strip(), re.I)
    if not m:
        return None
    expr = m.group(2).strip().rstrip(",")
    try:
        safe_eval(expr, np.linspace(0, 1, 3))
    except Exception:
        return None
    return (expr, float(m.group(3) or -5), float(m.group(4) or 5),
            bool(m.group(1)))


@dataclass
class Graph:
    xmin: float
    xmax: float
    ymin: float = 0.0
    ymax: float = 1.0
    curves: list[str] = field(default_factory=list)
    x: int = 150            # page placement of the rendered figure
    y: int = 150
    max_w: int = 1100
    label: str = ""         # "G1"… drawn as the title; prompts name it ("add … to G1")
    # "tex" = the pgfplots path below; "function" | "stem" | "pole_zero" |
    # "freq_response" = direct vector strokes from a GraphSpec (host/plots.py)
    kind: str = "tex"
    spec: dict = field(default_factory=dict)

    @classmethod
    def new(cls, expr: str, a: float, b: float, **kw) -> "Graph":
        g = cls(a, b, **kw)
        xs = np.linspace(a, b, 400)
        ys = safe_eval(expr, xs)
        ys = ys[np.isfinite(ys)]
        lo, hi = (float(ys.min()), float(ys.max())) if ys.size else (-1.0, 1.0)
        pad = (hi - lo) * 0.25 or 1.0     # headroom for later overlays
        g.ymin, g.ymax = lo - pad, hi + pad
        g.curves.append(expr)
        return g

    def tex(self, n: int | None = None) -> str:
        """Figure with the first n curves (all when None). Fixed limits +
        fixed legend slot -> identical bounding box for every n."""
        n = len(self.curves) if n is None else n
        body = []
        for i, c in enumerate(self.curves):
            style = STYLES[i % len(STYLES)][1]
            if i >= n:
                style += ", draw=none, forget plot"   # reserve nothing visible
            body.append(rf"\addplot[domain={self.xmin:g}:{self.xmax:g},"
                        rf"samples=160,{style}]{{{c}}};")
            if i < n:
                body.append(rf"\addlegendentry{{$y_{{{i + 1}}}$}}")
        return (r"\begin{tikzpicture}\begin{axis}[axis lines=middle,"
                r"xlabel=$x$,ylabel=$y$,width=11cm,height=7cm,"
                + (rf"title={{{self.label}}}," if self.label else "") +
                # frameless legend: a frame grows per entry, so each overlay
                # would redraw it in the new curve's colour
                r"trig format plots=rad,legend pos=north east,"
                r"legend style={draw=none,fill=none},"
                rf"xmin={self.xmin:g},xmax={self.xmax:g},"
                rf"ymin={self.ymin:g},ymax={self.ymax:g},"
                r"restrict y to domain=" + f"{self.ymin:g}:{self.ymax:g}" + "]"
                + "".join(body) + r"\end{axis}\end{tikzpicture}")

    def color(self, i: int) -> int:
        if self.kind != "tex":
            from plots import curve_style
            return curve_style(i)[0]
        return STYLES[i % len(STYLES)][0]


def _dilate(a: np.ndarray, r: int) -> np.ndarray:
    out = a.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= np.roll(np.roll(a, dy, 0), dx, 1)
    return out


def strokes(g: Graph, only_new: bool = False, compile_fn=None):
    """Strokes of the whole figure, or (only_new) of the LAST curve + its
    legend entry, positioned exactly as the full figure would be.
    Direct-plot graphs (kind != "tex") skip pdflatex: plots.strokes draws
    them as vectors (Ink strokes carrying pen + part)."""
    if g.kind != "tex":
        import plots
        return plots.strokes(g, only_new)
    from ink import skeletonize, trace, visible_dots
    if compile_fn is None:
        from tex import tex_to_image as compile_fn
    full = compile_fn(g.tex())
    img = full
    if only_new and len(g.curves) > 1:
        old = compile_fn(g.tex(len(g.curves) - 1))
        if old.shape != full.shape:
            raise RuntimeError("overlay bbox moved — refusing to misalign")
        img = full & ~_dilate(old, 3)
    # scale from the FULL figure so overlay strokes land on the old axes
    # dots=True + visible_dots: tick '0.5' lost its point (run 12), as tex.py
    fs = visible_dots(trace(skeletonize(full), 2, dots=True))
    w = max((px for s in fs for px, _ in s), default=1)
    k = min(1.0, g.max_w / max(w, 1))
    st = visible_dots(trace(skeletonize(img), 2, dots=True)) if img is not full else fs
    return [[(int(px * k) + g.x, int(py * k) + g.y) for px, py in s]
            for s in st if len(s) > 1]


def _selfcheck() -> None:
    x = np.linspace(0, 1, 5)
    assert np.allclose(safe_eval("x^2 + 2*x", x), x ** 2 + 2 * x)
    assert np.allclose(safe_eval("sin(pi*x)*exp(-x)", x),
                       np.sin(np.pi * x) * np.exp(-x))
    for bad in ["__import__('os')", "x.real", "open(1)", "().__class__"]:
        try:
            safe_eval(bad, x)
            raise AssertionError(f"accepted {bad!r}")
        except ValueError:
            pass
    assert parse("y = sin(3*x)*exp(-x/4), x from 0 to 5") == \
        ("sin(3*x)*exp(-x/4)", 0.0, 5.0, False)
    assert parse("add y = cos(x)")[3] is True
    assert parse("+ y=x^2 for x in [-2, 2]") == ("x^2", -2.0, 2.0, True)
    assert parse("hello monke") is None
    assert parse("plot y = x^2 - 2*x, x from -1 to 3") == ("x^2 - 2*x", -1.0, 3.0, False)
    assert parse("please add y = cos(x)")[3] is True
    g = Graph.new("sin(x)", 0, 6)
    g.curves.append("cos(x)")
    assert "forget plot" in g.tex(1) and "forget plot" not in g.tex()
    assert g.tex(1).split("[axis")[1][:200] == g.tex().split("[axis")[1][:200]
    print("graph selfcheck ok")


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["selfcheck"]:
        _selfcheck()
    else:
        sys.exit(__doc__)
