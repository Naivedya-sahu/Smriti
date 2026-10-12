"""
plots.py — fast deterministic Signals & Systems / DSP plot executors.

The model only supplies parameters (a GraphSpec); Python validates them,
computes the samples and draws VECTOR strokes directly (no pdflatex, no
skeleton tracing of a figure) — milliseconds per graph, identical strokes
for the same spec.

    validate(plot)            -> None | reason        never raises
    render(plot, label=None)  -> graph.Graph          kind = plot["type"]
    graph.strokes(g[, only_new])                      -> [Ink] (Tinted + .part)

GraphSpec (shared contract with render_plan / brain):
    {"type": "function"|"stem"|"pole_zero"|"freq_response",
     "title", "xlabel", "ylabel": str|null, "x", "y": [min,max]|null,
     "curves": [{"expr": str, "label": str|null}]|null, "n": [nmin,nmax]|null,
     "values": [number]|null, "zeros", "poles": [[re,im]]|null,
     "b", "a": [number]|null, "domain": "z"|"s"|null, "scale": "linear"|"db"|null,
     "show": "mag"|"phase"|null, "marks": [{"x": number, "label": str|null}]|null}

Conventions: curve pens/dashes come from render_plan.CURVES (blue solid, red
dashed, gray dash-dot, cycled); axes and text black. The RM2 panel is
grey, so curves also differ by dash, stem head shape and a legend label.
Strokes carry .part ("axes", "curve0", "impulse0", "legend0", "marks",
"text", "zeros", "poles") so an inker can split them by pen.

    uv run python host/plots.py preview build/dsp_preview.png
"""

from __future__ import annotations

import ast
import math
import re
import sys
from functools import lru_cache

import numpy as np

import graph
from ink import Tinted

TYPES = ("function", "stem", "pole_zero", "freq_response")
MAX_CURVES, MAX_STEMS, MAX_COEF, MAX_ROOTS, MAX_MARKS = 4, 128, 17, 16, 6
MAX_ABS, MIN_SPAN, MAX_EXPR, MAX_NODES = 1e6, 1e-6, 160, 120
MAX_TITLE, MAX_LABEL = 48, 28
N_SAMPLES = 721
XY_SIZE, PZ_SIZE = (600, 400), (420, 460)          # native figure px (W, H)
TICK, AXLAB, TITLE, LEG = 24, 28, 30, 24           # text sizes (px per em)
CAP = 0.66                                          # PatrickHand cap height / em
DASHES = {"solid": None, "dashed": (12.0, 7.0), "dash dot": (14.0, 5.0, 3.0, 5.0),
          "dash dot dot": (14.0, 5.0, 3.0, 5.0, 3.0, 5.0)}
EXTRA_DASH = ("dash dot dot",)                      # 4th curve: same pen as 1st, new dash


class Ink(Tinted):
    """A stroke with its pen colour and which part of the figure it draws."""
    def __init__(self, pts=(), color: int = 0, part: str = ""):
        super().__init__(pts, color)
        self.part = part


# ── conventions shared with render_plan ──────────────────────────────────────

_CURVES = None


def _curves() -> tuple:
    global _CURVES
    if _CURVES is None:
        try:                      # lazy: render_plan imports this module's callers
            import render_plan
            _CURVES = tuple(tuple(c) for c in render_plan.CURVES)
        except Exception:         # mid-edit / import cycle: render_plan's values
            _CURVES = ((6, "solid"), (7, "dashed"), (1, "dash dot"))
    return _CURVES


def curve_style(i: int) -> tuple[int, str]:
    """(xochitl pen enum, dash name) of curve i."""
    cv = _curves()
    pen, dash = cv[i % len(cv)]
    if i >= len(cv):
        dash = EXTRA_DASH[(i - len(cv)) % len(EXTRA_DASH)]
    return pen, dash


# ── safe expression evaluator (AST whitelist, never eval) ────────────────────

def _step(x):
    return np.where(x >= 0, 1.0, 0.0)


def _rect(x):
    return np.where(np.abs(x) < 0.5, 1.0, 0.0)


def _tri(x):
    return np.maximum(0.0, 1.0 - np.abs(x))


def _ramp(x):
    return np.where(x >= 0, x, 0.0)


def _kron(x):
    return np.where(np.abs(x) < 1e-9, 1.0, 0.0)


FUNCS = {"sin": np.sin, "cos": np.cos, "tan": np.tan, "exp": np.exp, "log": np.log,
         "ln": np.log, "log10": np.log10, "sqrt": np.sqrt, "abs": np.abs, "sign": np.sign,
         "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan, "sinh": np.sinh,
         "cosh": np.cosh, "tanh": np.tanh, "u": _step, "step": _step, "rect": _rect,
         "tri": _tri, "sinc": np.sinc, "ramp": _ramp}
CONSTS = {"pi": math.pi, "e": math.e}
VARS = ("t", "x", "n", "w")
_BIN = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide,
        ast.Pow: np.power, ast.Mod: np.mod}
_UN = {ast.UAdd: np.positive, ast.USub: np.negative}
_CMP = {ast.Lt: np.less, ast.LtE: np.less_equal, ast.Gt: np.greater,
        ast.GtE: np.greater_equal}             # no ==, in, is: indicators only
_ALLOWED = "sin cos tan exp log log10 sqrt abs sign u step rect tri sinc delta"


def _prep(expr: str) -> str:
    s = (expr.replace("^", "**").replace("−", "-").replace("·", "*").replace("π", "pi")
         .replace("ω", "w"))
    # implicit products a model/handwriting writes: 2t, 2pi, (t+1)(t-1)
    s = re.sub(r"(?<![\w.])(\d+\.?\d*(?:[eE][-+]?\d+)?)(?![eE][-+]?\d)\s*(?=[A-Za-z_(])",
               r"\1*", s)
    return re.sub(r"\)\s*(?=[A-Za-z_\d(])", ")*", s)


def _is_delta(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id.lower() == "delta")


def _has(node, pred) -> bool:
    return any(pred(n) for n in ast.walk(node))


def _build(node, discrete: bool):
    """AST node -> fn(env array) -> array|float. Raises ValueError(reason)."""
    if isinstance(node, ast.Constant):
        if type(node.value) in (int, float):
            v = float(node.value)
            return lambda env: v
        raise ValueError(f"constant {node.value!r} not allowed (numbers only)")
    if isinstance(node, ast.Name):
        if node.id in VARS:
            return lambda env: env
        if node.id.lower() in CONSTS:
            v = CONSTS[node.id.lower()]
            return lambda env: v
        raise ValueError(f"unknown name {node.id!r} (use t, x or n, and pi, e)")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        op, a, b = _BIN[type(node.op)], _build(node.left, discrete), _build(node.right, discrete)
        return lambda env: op(a(env), b(env))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        op, a = _UN[type(node.op)], _build(node.operand, discrete)
        return lambda env: op(a(env))
    if isinstance(node, ast.Compare) and all(type(o) in _CMP for o in node.ops):
        # a 0/1 indicator, e.g. rect written as (abs(x)<=1); chained -1<=x<=1.
        # Operands go through _build, so the whitelist still holds.
        parts = [_build(node.left, discrete)] + [_build(c, discrete) for c in node.comparators]
        ops = [_CMP[type(o)] for o in node.ops]

        def cmp(env, parts=parts, ops=ops):
            vals = [p(env) for p in parts]
            ok = np.ones(np.shape(env), dtype=bool)
            for i, op in enumerate(ops):
                ok &= np.asarray(op(vals[i], vals[i + 1]), dtype=bool)
            return ok.astype(float)
        return cmp
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("only plain calls like sin(t) are allowed")
        name = node.func.id
        if node.keywords or len(node.args) != 1 or isinstance(node.args[0], ast.Starred):
            raise ValueError(f"{name}() takes exactly one argument")
        if name.lower() == "delta":
            if not discrete:
                raise ValueError("delta(...) only as a k*delta(a*t+b) term")
            f, a = _kron, _build(node.args[0], discrete)
        elif name.lower() in FUNCS:
            f, a = FUNCS[name.lower()], _build(node.args[0], discrete)
        else:
            raise ValueError(f"unknown function {name!r} (allowed: {_ALLOWED})")
        return lambda env: f(a(env))
    raise ValueError(f"{type(node).__name__} not allowed in an expression")


def _terms(node, sign: float = 1.0):
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        yield from _terms(node.left, sign)
        yield from _terms(node.right, sign if isinstance(node.op, ast.Add) else -sign)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        yield from _terms(node.operand, -sign if isinstance(node.op, ast.USub) else sign)
    else:
        yield sign, node


def _const(node) -> float:
    if _has(node, lambda n: isinstance(n, ast.Name) and n.id in VARS) or _has(node, _is_delta):
        raise ValueError("delta(...) only as a k*delta(a*t+b) term")
    with np.errstate(all="ignore"):
        v = float(_build(node, False)(0.0))
    if not math.isfinite(v):
        raise ValueError("delta weight is not finite")
    return v


def _impulse(sign: float, node):
    """k*delta(arg) | delta(arg)*k | delta(arg)/k -> (weight, arg fn)."""
    k, call = 1.0, node
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        if _is_delta(node.right):
            k, call = _const(node.left), node.right
        elif _is_delta(node.left):
            k, call = _const(node.right), node.left
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and _is_delta(node.left):
        k, call = 1.0 / (_const(node.right) or float("nan")), node.left
    if not _is_delta(call) or call.keywords or len(call.args) != 1:
        raise ValueError("delta(...) only as a k*delta(a*t+b) term")
    if _has(call.args[0], _is_delta):
        raise ValueError("delta(...) only as a k*delta(a*t+b) term")
    return sign * k, _build(call.args[0], False)


class Expr:
    """Compiled expression: continuous part fn (None = only impulses) +
    CT impulses [(weight, arg fn)] (DT: delta is a Kronecker delta in fn)."""
    def __init__(self, fn, impulses):
        self.fn, self.impulses = fn, impulses

    def __call__(self, x):
        with np.errstate(all="ignore"):
            if self.fn is None:
                return np.zeros_like(np.asarray(x, float))
            return np.broadcast_to(np.asarray(self.fn(x), dtype=float), np.shape(x)).copy()

    def deltas(self) -> list[tuple[float, float]]:
        """[(t0, area)] of the CT impulses; arguments must be linear in t."""
        out = []
        for w, arg in self.impulses:
            with np.errstate(all="ignore"):
                f0, f1, f2 = (float(arg(np.float64(v))) for v in (0.0, 1.0, 2.0))
            a = f1 - f0
            if not all(map(math.isfinite, (f0, f1, f2))) or abs(a) < 1e-12 \
                    or abs(f2 - 2 * f1 + f0) > 1e-9 * (1 + abs(a)):
                raise ValueError("delta argument must be linear in t, like delta(t-1)")
            out.append((-f0 / a, w / abs(a)))
        return out


@lru_cache(maxsize=256)
def compile_expr(expr: str, discrete: bool = False) -> Expr:
    """Whitelisted AST -> Expr. Raises ValueError(reason)."""
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("expr must be a non-empty string")
    if len(expr) > MAX_EXPR:
        raise ValueError(f"expr longer than {MAX_EXPR} characters")
    try:
        tree = ast.parse(_prep(expr.strip()), mode="eval")
    except SyntaxError:
        raise ValueError(f"cannot parse {expr!r} (write products as 2*t, powers as t**2)") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ValueError("expression too long")
    body = tree.body
    if discrete or not _has(body, _is_delta):
        return Expr(_build(body, discrete), [])
    cont, imps = [], []
    for sign, term in _terms(body):
        if _has(term, _is_delta):
            imps.append(_impulse(sign, term))
        else:
            cont.append((sign, _build(term, False)))
    fn = None
    if cont:
        def fn(env, parts=tuple(cont)):
            return sum(s * f(env) for s, f in parts)
    return Expr(fn, imps)


def evaluate(expr: str, x, discrete: bool = False) -> np.ndarray:
    """Values of expr over x (t, x, n and w all name the same variable).
    CT impulses evaluate to 0 here; they are drawn as arrows."""
    return compile_expr(expr, discrete)(np.asarray(x, dtype=float))


# ── DSP numbers ──────────────────────────────────────────────────────────────

def freq_response(b, a, w, domain: str = "z") -> np.ndarray:
    """z: H(e^{jw}) = sum b_k e^{-jwk} / sum a_k e^{-jwk} (b, a in powers of
    z^-1, like scipy.signal.freqz). s: H(jw) with b, a highest power first
    (like scipy.signal.freqs)."""
    w = np.asarray(w, dtype=float)
    b, a = np.asarray(b, dtype=float), np.asarray(a if a is not None else [1.0], dtype=float)
    with np.errstate(all="ignore"):
        if domain == "s":
            return np.polyval(b, 1j * w) / np.polyval(a, 1j * w)
        e = np.exp(-1j * w)
        return np.polyval(b[::-1], e) / np.polyval(a[::-1], e)


def _clean_roots(r) -> np.ndarray:
    r = np.asarray(r, dtype=complex).ravel()
    r = np.where(np.abs(r.imag) < 1e-9 * (1 + np.abs(r)), r.real + 0j, r)
    r = np.where(np.abs(r.real) < 1e-12, 1j * r.imag, r)
    return np.array(sorted(r, key=lambda z: (round(z.real, 9), round(z.imag, 9))), dtype=complex)


def zeros_poles(plot: dict) -> tuple[np.ndarray, np.ndarray]:
    """Zeros and poles: explicit [[re, im]] lists, else roots of b/a
    (z: powers of z^-1 incl. the z^(N-M) factor; s: highest power first)."""
    if plot.get("zeros") is not None or plot.get("poles") is not None:
        return tuple(_clean_roots([complex(*p) for p in (plot.get(k) or [])])
                     for k in ("zeros", "poles"))
    b = [float(v) for v in (plot.get("b") or [1.0])]
    a = [float(v) for v in (plot.get("a") or [1.0])]
    zs, ps = list(np.roots(b)), list(np.roots(a))
    if (plot.get("domain") or "z") == "z":
        m, n = len(b) - 1, len(a) - 1
        zs += [0.0] * max(0, n - m)
        ps += [0.0] * max(0, m - n)
    return _clean_roots(zs), _clean_roots(ps)


# ── validation ───────────────────────────────────────────────────────────────

_TEX = {r"\omega": "ω", r"\Omega": "Ω", r"\pi": "π", r"\theta": "θ", r"\tau": "τ",
        r"\varphi": "φ", r"\phi": "φ", r"\alpha": "α", r"\sigma": "σ", r"\Delta": "Δ",
        r"\mu": "μ", r"\lambda": "λ", r"\infty": "∞", r"\angle": "∠", r"\to": "→",
        r"\cdot": "·", r"\pm": "±", r"\leq": "≤", r"\geq": "≥", r"\le": "≤", r"\ge": "≥",
        r"\approx": "≈", r"\deg": "°", r"\left": "", r"\right": "", r"\,": " ", r"\;": " ",
        r"\ ": " ", r"\!": "", "$": ""}
_FONT_UNI = set("μΩπ∞≈·−°²³½ⁿ±≤≥λ")      # what PatrickHand inks beyond ASCII
# glyphs PatrickHand lacks, drawn here: (advance, [polyline]) in x-height units, y up
_CUSTOM = {
    "ω": (1.05, [[(0.12, 0.95), (0.03, 0.5), (0.1, 0.12), (0.27, 0.0), (0.43, 0.12),
                  (0.5, 0.5), (0.57, 0.12), (0.73, 0.0), (0.9, 0.12), (0.97, 0.5),
                  (0.88, 0.95)]]),
    "θ": (0.8, [[(0.38, 1.5), (0.13, 1.3), (0.04, 0.75), (0.13, 0.18), (0.38, 0.0),
                 (0.62, 0.18), (0.71, 0.75), (0.62, 1.3), (0.38, 1.5)],
                [(0.06, 0.75), (0.7, 0.75)]]),
    "τ": (0.8, [[(0.02, 0.92), (0.75, 0.97)], [(0.4, 0.95), (0.35, 0.2), (0.45, 0.0),
                                                (0.62, 0.06)]]),
    "φ": (0.95, [[(0.45, 0.95), (0.15, 0.85), (0.03, 0.48), (0.15, 0.1), (0.45, 0.0),
                  (0.75, 0.1), (0.87, 0.48), (0.75, 0.85), (0.45, 0.95)],
                 [(0.45, 1.45), (0.45, -0.55)]]),
    "α": (0.95, [[(0.82, 1.0), (0.62, 0.32), (0.45, 0.05), (0.25, 0.0), (0.06, 0.2),
                  (0.06, 0.72), (0.25, 1.0), (0.45, 0.95), (0.6, 0.6), (0.7, 0.15),
                  (0.86, 0.0)]]),
    "σ": (0.95, [[(0.92, 0.95), (0.4, 0.95), (0.12, 0.8), (0.03, 0.45), (0.15, 0.08),
                  (0.4, 0.0), (0.65, 0.1), (0.75, 0.45), (0.62, 0.82), (0.4, 0.95)]]),
    "Δ": (1.15, [[(0.05, 0.0), (0.55, 1.45), (1.05, 0.0), (0.05, 0.0)]]),
    "∠": (1.1, [[(0.85, 1.2), (0.05, 0.0), (1.0, 0.0)]]),
    "→": (1.2, [[(0.05, 0.5), (1.05, 0.5)], [(0.8, 0.75), (1.05, 0.5), (0.8, 0.25)]]),
    # the font's flagged '1' traces to an arrow-like skeleton at tick size
    "1": (0.97, [[(0.18, 1.12), (0.5, 1.4), (0.5, 0.0)]]),
}
INKABLE = {chr(c) for c in range(32, 127)} | _FONT_UNI | set(_CUSTOM)


def clean_text(s: str) -> str:
    """LaTeX-ish names -> the unicode the handwriting font inks."""
    for k in sorted(_TEX, key=len, reverse=True):
        s = s.replace(k, _TEX[k])
    return s


def _text_field(v, what: str, limit: int) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError(f"{what} must be a string or null")
    s = clean_text(v).strip()
    if len(s) > limit:
        raise ValueError(f"{what} longer than {limit} characters")
    bad = sorted({c for c in s if c not in INKABLE})
    if bad:
        raise ValueError(f"{what}: cannot ink {''.join(bad)!r}; use ASCII or "
                         "ω π θ τ φ α σ Δ Ω μ λ ∠ ∞ ± (or \\omega style names)")
    return s or None


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{what} must be a finite number")
    return float(v)


def _range(v, what: str, lim: float = MAX_ABS) -> list | None:
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f"{what} range must be [min, max]")
    lo, hi = (_num(t, f"{what} range value") for t in v)
    if not lo < hi:
        raise ValueError(f"{what} range must be [min, max] with min < max")
    if max(abs(lo), abs(hi)) > lim or hi - lo < MIN_SPAN:
        raise ValueError(f"{what} range [{lo:g}, {hi:g}] out of bounds "
                         f"(|value| <= {lim:g}, span >= {MIN_SPAN:g})")
    return [lo, hi]


def _nums(v, what: str, cap: int) -> list | None:
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or not v:
        raise ValueError(f"{what} must be a non-empty list of numbers")
    if len(v) > cap:
        raise ValueError(f"{what}: at most {cap} numbers")
    out = [_num(t, f"{what} entry") for t in v]
    if max(abs(t) for t in out) > MAX_ABS:
        raise ValueError(f"{what}: |value| <= {MAX_ABS:g}")
    return out


def _pairs(v, what: str) -> list | None:
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) > MAX_ROOTS:
        raise ValueError(f"{what} must be a list of at most {MAX_ROOTS} [re, im] pairs")
    out = []
    for p in v:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError(f"{what} entries must be [re, im]")
        re_, im = _num(p[0], f"{what} re"), _num(p[1], f"{what} im")
        if max(abs(re_), abs(im)) > MAX_ABS:
            raise ValueError(f"{what}: |value| <= {MAX_ABS:g}")
        out.append([re_, im])
    return out


def _choice(v, what: str, options: tuple):
    if v is None:
        return options[0]
    if v not in options:
        raise ValueError(f"{what} must be one of {', '.join(options)} or null")
    return v


def _check(plot) -> dict:
    """Normalized copy of a valid spec (defaults filled). Raises ValueError."""
    if not isinstance(plot, dict):
        raise ValueError("plot must be a JSON object")
    kind = plot.get("type")
    if kind not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    s = {"type": kind,
         "title": _text_field(plot.get("title"), "title", MAX_TITLE),
         "xlabel": _text_field(plot.get("xlabel"), "xlabel", MAX_LABEL),
         "ylabel": _text_field(plot.get("ylabel"), "ylabel", MAX_LABEL),
         "x": _range(plot.get("x"), "x"), "y": _range(plot.get("y"), "y"),
         "domain": _choice(plot.get("domain"), "domain", ("z", "s")),
         "scale": _choice(plot.get("scale"), "scale", ("linear", "db")),
         "show": _choice(plot.get("show"), "show", ("mag", "phase"))}
    curves = plot.get("curves")
    if curves is not None:
        if not isinstance(curves, (list, tuple)) or not 1 <= len(curves) <= MAX_CURVES:
            raise ValueError(f"curves must be a list of 1..{MAX_CURVES} {{expr, label}}")
        s["curves"] = []
        for i, c in enumerate(curves):
            if not isinstance(c, dict) or not isinstance(c.get("expr"), str):
                raise ValueError(f"curve {i + 1} needs an 'expr' string")
            try:
                ex = compile_expr(c["expr"], kind == "stem")
                ex.deltas()
                ex(np.linspace(-1.0, 1.0, 5))
            except ValueError as e:
                raise ValueError(f"curve {i + 1} ({c['expr'][:40]!r}): {e}") from None
            s["curves"].append({"expr": c["expr"].strip(),
                                "label": _text_field(c.get("label"), f"curve {i + 1} label",
                                                     MAX_LABEL)})
    else:
        s["curves"] = None
    marks = plot.get("marks")
    if marks is not None:
        if not isinstance(marks, (list, tuple)) or len(marks) > MAX_MARKS:
            raise ValueError(f"marks must be a list of at most {MAX_MARKS} {{x, label}}")
        s["marks"] = [{"x": _num((m or {}).get("x") if isinstance(m, dict) else None, "mark x"),
                       "label": _text_field(m.get("label"), "mark label", 16)} for m in marks]
    else:
        s["marks"] = []
    s["values"] = _nums(plot.get("values"), "values", MAX_STEMS)
    s["zeros"], s["poles"] = _pairs(plot.get("zeros"), "zeros"), _pairs(plot.get("poles"), "poles")
    s["b"], s["a"] = _nums(plot.get("b"), "b", MAX_COEF), _nums(plot.get("a"), "a", MAX_COEF)
    nr = plot.get("n")
    if nr is not None:
        nr = _range(nr, "n", 1e4)
        if any(v != int(v) for v in nr):
            raise ValueError("n range must be integers [nmin, nmax]")
        nr = [int(nr[0]), int(nr[1])]
        if nr[1] - nr[0] + 1 > MAX_STEMS:
            raise ValueError(f"n range too long: at most {MAX_STEMS} samples")
    s["n"] = nr

    if kind == "function":
        if not s["curves"]:
            raise ValueError("function needs curves: [{expr, label}]")
        s["x"] = s["x"] or [-5.0, 5.0]
    elif kind == "stem":
        if not s["curves"] and not s["values"]:
            raise ValueError("stem needs values (with n) or curves with expr in n")
        if s["values"]:
            if nr is None:
                nr = [0, len(s["values"]) - 1]
            elif nr[1] - nr[0] + 1 != len(s["values"]):
                raise ValueError(f"values has {len(s['values'])} numbers but n range "
                                 f"{nr} has {nr[1] - nr[0] + 1}")
        s["n"] = nr or [-5, 15]
    elif kind == "pole_zero":
        if all(s[k] is None for k in ("zeros", "poles", "b", "a")):
            raise ValueError("pole_zero needs zeros/poles or b/a coefficients")
        _coef_check(s)
        z, p = zeros_poles(s)
        if len(z) > MAX_ROOTS or len(p) > MAX_ROOTS:
            raise ValueError(f"at most {MAX_ROOTS} zeros and poles")
        if not np.all(np.isfinite(np.concatenate([z, p, [0]]))):
            raise ValueError("roots are not finite")
    else:                                                    # freq_response
        if s["b"] is None:
            raise ValueError("freq_response needs b (and a; a defaults to [1])")
        s["a"] = s["a"] or [1.0]
        _coef_check(s)
        if s["domain"] == "z" and s["x"] and s["x"][1] - s["x"][0] > 8 * math.pi:
            raise ValueError("z-domain ω range wider than 8π rad/sample")
        if s["domain"] == "s" and s["x"]:
            if s["x"][0] <= 0:
                raise ValueError("s-domain ω axis is logarithmic: x must be [ωmin > 0, ωmax]")
            if s["x"][1] / s["x"][0] > 1e8:
                raise ValueError("s-domain ω range wider than 8 decades")
    return s


def _coef_check(s: dict) -> None:
    b, a = s.get("b"), s.get("a")
    if b is not None and not any(b):
        raise ValueError("b is all zeros")
    if a is not None:
        if not any(a):
            raise ValueError("a is all zeros")
        if s["domain"] == "z" and a[0] == 0:
            raise ValueError("a[0] must be nonzero (z-domain: a0 + a1 z^-1 + ...)")


def validate(plot) -> str | None:
    """None when the spec is renderable, else a short reason. Never raises."""
    try:
        _check(plot)
        return None
    except ValueError as e:
        return str(e)
    except Exception as e:                                     # noqa: BLE001
        return f"invalid plot spec ({type(e).__name__}: {e})"[:200]


# ── render: spec -> Graph with fixed limits ──────────────────────────────────

def _pad(lo: float, hi: float, frac: float = 0.12) -> tuple[float, float]:
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return -1.0, 1.0
    if hi - lo < 1e-12:
        return lo - 1.0, hi + 1.0
    p = (hi - lo) * frac
    return lo - p, hi + p


def _robust(vals: np.ndarray) -> tuple[float, float]:
    v = vals[np.isfinite(vals)]
    if not v.size:
        return float("nan"), float("nan")
    lo, hi = float(v.min()), float(v.max())
    p2, p98 = np.percentile(v, [2, 98])
    if hi - lo > 50 * max(p98 - p2, 1e-12):            # poles (tan, 1/t): ignore the spikes
        lo, hi = float(p2), float(p98)
    return lo, hi


def _nice(span: float, target: int = 6) -> float:
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * mag >= raw * 0.999:
            return m * mag
    return 10 * mag


def _snap(lo: float, hi: float) -> tuple[float, float]:
    st = _nice(hi - lo)
    return math.floor(lo / st + 1e-9) * st, math.ceil(hi / st - 1e-9) * st


def render(plot: dict, label: str | None = None, **kw) -> graph.Graph:
    """A validated GraphSpec -> graph.Graph (kind = type, limits fixed now so
    later overlays land on the same axes). Raises ValueError(reason)."""
    s = _check(plot)
    kind = s["type"]
    curves = [c["expr"] for c in (s["curves"] or [])]
    if kind == "function":
        x0, x1 = s["x"]
        xs = np.linspace(x0, x1, N_SAMPLES)
        vals, imps = [np.zeros(1)], [0.0]
        for c in curves:
            ex = compile_expr(c)
            if ex.fn is not None:
                vals.append(ex(xs))
            imps += [w for t0, w in ex.deltas() if x0 <= t0 <= x1]
        y = s["y"] or _pad(*_robust(np.concatenate(vals + [np.array(imps)])))
        s["xlabel"] = s["xlabel"] or ("x" if any(re.search(r"\bx\b", c) for c in curves) else "t")
    elif kind == "stem":
        n0, n1 = s["n"]
        ns = np.arange(n0, n1 + 1, dtype=float)
        vals = [np.zeros(1)] + ([np.array(s["values"])] if s["values"] else [])
        vals += [evaluate(c, ns, True) for c in curves]
        y = s["y"] or _pad(*_robust(np.concatenate(vals)))
        x0, x1 = n0 - 1.0, n1 + 1.0
        s["xlabel"] = s["xlabel"] or "n"
        if s["values"]:
            curves = ["x[n]"] + curves
    elif kind == "freq_response":
        z = s["domain"] == "z"
        if s["x"]:
            x0, x1 = s["x"]
        elif z:
            x0, x1 = 0.0, math.pi
        else:
            x0, x1 = _auto_w(s)
        s["_xlog"] = not z
        s["_xpi"] = z and _pi_multiple(x0) and _pi_multiple(x1)
        ys = _response(s, _wgrid(s, x0, x1))
        if s["y"]:
            y = s["y"]
        elif s["show"] == "phase":
            if z:
                y, s["_ypi"] = (-math.pi * 1.08, math.pi * 1.08), True
            else:
                lo, hi = _robust(ys)
                y = (45 * math.floor(lo / 45 - 1e-9), 45 * math.ceil(hi / 45 + 1e-9))
                y = y if y[1] > y[0] else (y[0] - 45, y[1] + 45)
        elif s["scale"] == "db":
            fin = ys[np.isfinite(ys)]
            hi = float(fin.max()) if fin.size else 0.0
            lo = max(float(fin.min()) if fin.size else -40.0, hi - 60.0)
            y = _snap(*_pad(lo, hi, 0.06))
        else:
            fin = ys[np.isfinite(ys)]
            hi = float(fin.max()) if fin.size else 1.0
            y = (0.0, hi * 1.15 if hi > 0 else 1.0)
        zs = "e^{jω}" if z else "jω"
        s["xlabel"] = s["xlabel"] or ("ω" if z else "ω (rad/s)")
        s["ylabel"] = s["ylabel"] or (f"∠H({zs})" + ("" if z else " (deg)")
                                      if s["show"] == "phase" else
                                      f"|H({zs})|" + (" dB" if s["scale"] == "db" else ""))
        curves = ["H(" + zs + ")"] + curves
    else:                                                     # pole_zero
        zs_, ps_ = zeros_poles(s)
        r = max([abs(v) for v in np.concatenate([zs_, ps_])] + [0.0])
        if s["domain"] == "z":
            r = max(1.25, math.ceil(r * 1.2 * 4) / 4)
        else:
            r = _nice(max(r, 1.0) * 1.3, 4) * math.ceil(max(r, 1.0) * 1.3 / _nice(max(r, 1.0) * 1.3, 4))
        x0, x1, y = -r, r, (-r, r)
        if s["x"] or s["y"]:                                  # explicit view, kept square
            xr, yr = s["x"] or [-r, r], s["y"] or [-r, r]
            half = max(xr[1] - xr[0], yr[1] - yr[0]) / 2
            cx, cy = sum(xr) / 2, sum(yr) / 2
            x0, x1, y = cx - half, cx + half, (cy - half, cy + half)
        s["xlabel"], s["ylabel"] = s["xlabel"] or "Re", s["ylabel"] or "Im"
        curves = []
    s["_base"] = 1 if kind == "freq_response" or (kind == "stem" and s["values"]) else 0
    opts = {"x": 0, "y": 0, **kw}
    return graph.Graph(float(x0), float(x1), float(y[0]), float(y[1]), curves=curves,
                       label=label or "", kind=kind, spec=s, **opts)


def _pi_multiple(v: float) -> bool:
    k = v / (math.pi / 4)
    return abs(k - round(k)) < 1e-6


def _auto_w(s: dict) -> tuple[float, float]:
    z, p = zeros_poles({"b": s["b"], "a": s["a"], "domain": "s"})
    mags = [abs(v) for v in np.concatenate([z, p]) if abs(v) > 1e-12]
    lo, hi = (min(mags), max(mags)) if mags else (1.0, 1.0)
    a, b = math.floor(math.log10(lo)) - 1, math.ceil(math.log10(hi)) + 1
    if b - a < 2:
        b = a + 2
    return 10.0 ** a, 10.0 ** min(b, a + 6)


def _wgrid(s: dict, x0: float, x1: float) -> np.ndarray:
    if s.get("_xlog"):
        return np.logspace(math.log10(x0), math.log10(x1), N_SAMPLES)
    return np.linspace(x0, x1, N_SAMPLES)


def _response(s: dict, w: np.ndarray) -> np.ndarray:
    h = freq_response(s["b"], s["a"], w, s["domain"])
    with np.errstate(all="ignore"):
        if s["show"] == "phase":
            ph = np.angle(h)
            return ph if s["domain"] == "z" else np.degrees(np.unwrap(ph))
        m = np.abs(h)
        return 20 * np.log10(np.maximum(m, 1e-12)) if s["scale"] == "db" else m


# ── text: cached per-glyph strokes from the handwriting font ─────────────────

_GLYPHS: dict = {}
_FONT = None
_RPX = 64                       # raster size a glyph is traced at (then scaled)


def _font():
    global _FONT
    if _FONT is None:
        import ink
        _FONT = ink.load_font(ink.load_styles()["print"], _RPX)
    return _FONT


def _glyph(ch: str):
    """(advance em, [(pts em, is_dot)]) baseline-relative, y down."""
    g = _GLYPHS.get(ch)
    if g is not None:
        return g
    if ch in _CUSTOM:
        xh = 30 / 64                                   # PatrickHand x-height / em
        adv, lines = _CUSTOM[ch]
        g = (adv * xh, [([(x * xh, -y * xh) for x, y in ln], False) for ln in lines])
    else:
        from PIL import Image, ImageDraw

        import ink
        f = _font()
        adv = f.getlength(ch) / _RPX
        l, t, r, b = f.getbbox(ch, anchor="ls")
        strokes = []
        if ch.strip() and r > l and b > t:
            pad = 4
            l, t, r, b = int(l), int(t), int(math.ceil(r)), int(math.ceil(b))
            img = Image.new("L", (r - l + 2 * pad, b - t + 2 * pad), 0)
            ImageDraw.Draw(img).text((pad - l, pad - t), ch, font=f, fill=255, anchor="ls")
            for st in ink.trace(ink.skeletonize(np.asarray(img) > 128), 2, dots=True):
                tiny = ink._tiny(st)
                strokes.append(([((px - pad + l) / _RPX, (py - pad + t) / _RPX) for px, py in st],
                                tiny))
        g = (adv, strokes)
    _GLYPHS[ch] = g
    return g


def _runs(s: str) -> list[tuple[str, int]]:
    """'H(e^{jω})' -> [(char, level)], level 1 = superscript, -1 = subscript."""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c in "^_" and i + 1 < len(s):
            lvl = 1 if c == "^" else -1
            if s[i + 1] == "{":
                j = s.find("}", i + 2)
                j = len(s) if j < 0 else j
                out += [(ch, lvl) for ch in s[i + 2:j]]
                i = j + 1
            else:
                out.append((s[i + 1], lvl))
                i += 2
            continue
        if c not in "{}":
            out.append((c, 0))
        i += 1
    return out


def text_width(s: str, size: float) -> float:
    return sum(_glyph(c)[0] * size * (0.62 if lvl else 1.0) for c, lvl in _runs(clean_text(s)))


def _text(s: str, x: float, y: float, size: float, anchor: str = "l") -> list:
    """Strokes (float px) of s with its baseline at y; anchor l | c | r."""
    s = clean_text(s)
    w = text_width(s, size)
    x -= w / 2 if anchor == "c" else w if anchor == "r" else 0
    out = []
    for ch, lvl in _runs(s):
        k = size * (0.62 if lvl else 1.0)
        dy = -0.42 * size if lvl > 0 else 0.18 * size if lvl < 0 else 0.0
        adv, strokes = _glyph(ch)
        for pts, dot in strokes:
            if dot:                                         # decimal point: a ring that inks
                cx = x + sum(p[0] for p in pts) / len(pts) * k
                cy = y + dy + sum(p[1] for p in pts) / len(pts) * k
                out.append([(cx + 2.2 * math.cos(a), cy + 2.2 * math.sin(a))
                            for a in np.linspace(0, 2 * math.pi, 7)])
            else:
                out.append([(x + px * k, y + dy + py * k) for px, py in pts])
        x += adv * k
    return out


# ── geometry helpers ─────────────────────────────────────────────────────────

class _Fig:
    def __init__(self, w: int, h: int):
        self.w, self.h, self.items = w, h, []

    def line(self, part: str, pen: int, pts, dash=None):
        for seg in _dash(pts, DASHES.get(dash) if isinstance(dash, str) else dash):
            if len(seg) > 1:
                self.items.append((part, pen, seg))

    def text(self, part: str, pen: int, s: str, x, y, size, anchor="l"):
        if not s:
            return
        w = text_width(s, size)
        lo = x - (w / 2 if anchor == "c" else w if anchor == "r" else 0)
        lo = min(max(lo, 2.0), self.w - 2.0 - w)           # keep inside the figure box
        for st in _text(s, lo, y, size, "l"):
            if len(st) > 1:
                self.items.append((part, pen, st))


def _dash(pts, pattern):
    if not pattern:
        return [list(pts)]
    out, cur, idx, left, on = [], [pts[0]], 0, pattern[0], True
    for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
        seg = math.hypot(xb - xa, yb - ya)
        pos = 0.0
        while seg - pos > left:
            pos += left
            p = (xa + (xb - xa) * pos / seg, ya + (yb - ya) * pos / seg)
            if on:
                cur.append(p)
                out.append(cur)
                cur = None
            else:
                cur = [p]
            on, idx = not on, (idx + 1) % len(pattern)
            left = pattern[idx]
        left -= seg - pos
        if on:
            cur.append((xb, yb))
    if on and cur and len(cur) > 1:
        out.append(cur)
    return out


def _lb(a, b, box):
    """Liang-Barsky: visible parameter span (t0, t1) of segment a-b, or None."""
    x0, y0, x1, y1 = box
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return None
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                return None
    return t0, t1


def _clip(pts, pins, box):
    """Polyline clipped to box -> [(pts, pins)] pieces (pins = kept indices)."""
    pieces, cur, cp = [], [], set()
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        span = _lb(a, b, box)
        if span is None:
            if len(cur) > 1:
                pieces.append((cur, cp))
            cur, cp = [], set()
            continue
        t0, t1 = span
        pa = a if t0 == 0 else (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
        pb = b if t1 == 1 else (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
        if not cur:
            cur = [pa]
            if t0 == 0 and i in pins:
                cp.add(0)
        cur.append(pb)
        if t1 == 1 and i + 1 in pins:
            cp.add(len(cur) - 1)
        if t1 < 1:
            pieces.append((cur, cp))
            cur, cp = [], set()
    if len(cur) > 1:
        pieces.append((cur, cp))
    return pieces


def _thin(pts, pins, step: float = 1.6):
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        p = pts[i]
        if i in pins or (p[0] - out[-1][0]) ** 2 + (p[1] - out[-1][1]) ** 2 >= step * step:
            out.append(p)
    out.append(pts[-1])
    return out


class _Axes:
    def __init__(self, g, l, t, r, b):
        self.g, self.l, self.t, self.r, self.b = g, l, t, r, b
        self.log = bool(g.spec.get("_xlog"))
        self.fx0, self.fx1 = self._f(g.xmin), self._f(g.xmax)

    def _f(self, x):
        return np.log10(x) if self.log else x

    def px(self, x):
        return self.l + (self._f(x) - self.fx0) / (self.fx1 - self.fx0) * (self.r - self.l)

    def py(self, y):
        g = self.g
        span = g.ymax - g.ymin
        y = np.clip(y, g.ymin - 4 * span, g.ymax + 4 * span)
        return self.t + (g.ymax - y) / span * (self.b - self.t)


def _fmt(v: float, step: float) -> str:
    if abs(v) < abs(step) * 1e-6:
        return "0"
    d = max(0, -math.floor(math.log10(abs(step)) + 1e-9))
    txt = f"{abs(v):.{d}f}"
    if abs(v) >= 1e5:
        e = math.floor(math.log10(abs(v)))
        m = abs(v) / 10 ** e
        txt = (f"{m:g}·" if abs(m - 1) > 1e-9 else "") + "10^{" + str(e) + "}"
    return ("−" if v < 0 else "") + txt


def _pi_label(k: int, den: int) -> str:
    g = math.gcd(abs(k), den)
    num, d = k // g, den // g
    if num == 0:
        return "0"
    sgn = "−" if num < 0 else ""
    num = abs(num)
    return sgn + ("" if num == 1 else str(num)) + "π" + ("" if d == 1 else f"/{d}")


def _ticks(lo: float, hi: float, pi: bool = False, deg: bool = False, target: int = 6):
    """[(value, label)] inside [lo, hi]."""
    span = hi - lo
    if pi:
        for den in (4, 2, 1):
            if span / (math.pi / den) <= 6:
                break
        step = math.pi / den
        k0, k1 = math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9)
        return [(k * step, _pi_label(k, den)) for k in range(k0, k1 + 1)]
    if deg:
        step = next((s for s in (15, 30, 45, 90, 180, 360) if span / s <= 7), 360)
    else:
        step = _nice(span, target)
    k0, k1 = math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9)
    return [(k * step, _fmt(k * step, step)) for k in range(k0, k1 + 1)]


def _head(shape: int, cx: float, cy: float, r: float = 5.0):
    if shape == 0:                                       # circle
        return [(cx + r * math.cos(a), cy + r * math.sin(a))
                for a in np.linspace(0, 2 * math.pi, 13)]
    if shape == 1:                                       # square
        return [(cx - r, cy - r), (cx + r, cy - r), (cx + r, cy + r), (cx - r, cy + r),
                (cx - r, cy - r)]
    if shape == 2:                                       # triangle
        return [(cx, cy - r * 1.2), (cx + r * 1.1, cy + r * 0.8),
                (cx - r * 1.1, cy + r * 0.8), (cx, cy - r * 1.2)]
    return [(cx, cy - r * 1.3), (cx + r, cy), (cx, cy + r * 1.3), (cx - r, cy),
            (cx, cy - r * 1.3)]                          # diamond


def _arrow(fig, part, pen, tip, direction, size=10.0):
    dx, dy = direction
    n = math.hypot(dx, dy) or 1.0
    dx, dy = dx / n, dy / n
    bx, by = tip[0] - dx * size, tip[1] - dy * size
    fig.line(part, pen, [(bx - dy * size * 0.45, by + dx * size * 0.45), tip,
                         (bx + dy * size * 0.45, by - dx * size * 0.45)])


def _auto_label(expr: str, i: int, kind: str) -> str:
    e = expr.replace("**", "^").replace("*", "·").replace(" ", "")
    if len(e) <= 14 and all(c in INKABLE for c in e):
        return e
    return f"x{i + 1}[n]" if kind == "stem" else f"y{i + 1}"


# ── figures ──────────────────────────────────────────────────────────────────

def _title(fig, g):
    s = g.spec
    x0 = 4.0
    if g.label:
        fig.text("text", 0, g.label, 4, 34, TITLE)
        x0 += text_width(g.label, TITLE) + 18
    if s.get("title"):
        w = text_width(s["title"], TITLE)
        size = TITLE * min(1.0, (fig.w - x0 - 4) / max(w, 1))
        cx = max(x0 + text_width(s["title"], size) / 2, fig.w / 2)
        fig.text("text", 0, s["title"], cx, 34, size, "c")


def _series(g):
    """[(label | None, kind, payload)] for every curve index of the graph."""
    s, out = g.spec, []
    base = s.get("_base", 0)
    labels = [c["label"] for c in (s.get("curves") or [])]
    for i, c in enumerate(g.curves):
        if i < base:
            out.append((None, "values" if g.kind == "stem" else "H", None))
        else:
            j = i - base
            out.append((labels[j] if j < len(labels) else None, "expr", c))
    return out


def _xy(g, fig: _Fig):
    s, kind = g.spec, g.kind
    frame = kind == "freq_response"
    xin = not frame and g.xmin < 0 < g.xmax
    l, r, t, b = (30 if xin else 78), 30, 76, (66 if frame else 40)
    ax = _Axes(g, l, t, fig.w - r, fig.h - b)
    ay0 = ax.py(0.0) if (not frame and g.ymin <= 0 <= g.ymax) else ax.py(g.ymin)
    ax0 = ax.px(0.0) if (not frame and g.xmin <= 0 <= g.xmax) else ax.px(g.xmin)
    ay0, ax0 = float(ay0), float(ax0)
    _title(fig, g)
    # axes + arrows
    fig.line("axes", 0, [(l, ay0), (fig.w - r + 12, ay0)])
    _arrow(fig, "axes", 0, (fig.w - r + 12, ay0), (1, 0))
    fig.line("axes", 0, [(ax0, fig.h - b), (ax0, t - 14)])
    _arrow(fig, "axes", 0, (ax0, t - 14), (0, -1))
    # ticks
    cap = CAP * TICK
    yin = not frame and g.ymin < 0 < g.ymax
    if ax.log:
        for e in range(math.ceil(math.log10(g.xmin) - 1e-9), math.floor(math.log10(g.xmax) + 1e-9) + 1):
            for m in range(1, 10):
                v = m * 10.0 ** e
                if g.xmin <= v <= g.xmax:
                    px = float(ax.px(v))
                    fig.line("axes", 0, [(px, ay0), (px, ay0 + (7 if m == 1 else 4))])
            v = 10.0 ** e
            if g.xmin <= v * (1 + 1e-9) and v <= g.xmax * (1 + 1e-9):
                fig.text("axes", 0, "10^{" + str(e) + "}", float(ax.px(v)), ay0 + 9 + cap, TICK, "c")
    else:
        step = s["type"] == "stem"
        xt = _ticks(g.xmin, g.xmax, pi=bool(s.get("_xpi")))
        if step:
            st_ = max(1, int(round(_nice(g.xmax - g.xmin, 8))))
            xt = [(float(k), _fmt(k, 1)) for k in range(math.ceil(g.xmin), math.floor(g.xmax) + 1)
                  if k % st_ == 0]
        for v, lab in xt:
            px = float(ax.px(v))
            fig.line("axes", 0, [(px, ay0), (px, ay0 + 6)])
            if not (xin and yin and abs(v) < 1e-12):
                fig.text("axes", 0, lab, px, ay0 + 9 + cap, TICK, "c")
    for v, lab in _ticks(g.ymin, g.ymax, pi=bool(s.get("_ypi")),
                         deg=frame and s["show"] == "phase" and not s.get("_ypi"), target=5):
        py = float(ax.py(v))
        fig.line("axes", 0, [(ax0 - 6, py), (ax0, py)])
        if not (xin and yin and abs(v) < 1e-12):
            fig.text("axes", 0, lab, ax0 - 9, py + cap / 2, TICK, "r")
    if xin and yin:
        fig.text("axes", 0, "0", ax0 - 6, ay0 + 9 + cap, TICK, "r")
    # axis labels
    if s.get("xlabel"):
        if frame:
            fig.text("text", 0, s["xlabel"], (l + fig.w - r) / 2, fig.h - 10, AXLAB, "c")
        else:
            fig.text("text", 0, s["xlabel"], fig.w - r + 10, ay0 - 10, AXLAB, "r")
    if s.get("ylabel"):
        fig.text("text", 0, s["ylabel"], ax0 + 10, t - 8, AXLAB, "l")
    # marks
    for m in s.get("marks") or []:
        if g.xmin <= m["x"] <= g.xmax and (not ax.log or m["x"] > 0):
            px = float(ax.px(m["x"]))
            fig.line("marks", 0, [(px, fig.h - b), (px, t)], (6.0, 5.0))
            if m.get("label"):
                w = text_width(m["label"], LEG)
                left = px + w + 8 < fig.w - r
                fig.text("marks", 0, m["label"], px + 5 if left else px - 5,
                         t + 4 + CAP * LEG, LEG, "l" if left else "r")
    # curves
    box = (l, t, fig.w - r, fig.h - b)
    ser = _series(g)
    # legend slots depend only on earlier entries -> overlays never move them
    room = [ax0 + 10 + (text_width(s["ylabel"], AXLAB) + 24 if s.get("ylabel") else 6), 0]
    for i, (lab, how, payload) in enumerate(ser):
        pen, dash = curve_style(i)
        if kind == "stem":
            _stems(g, fig, ax, i, how, payload, pen, dash, box)
        elif kind == "freq_response":
            w = _wgrid(s, g.xmin, g.xmax)
            ys = _response(s, w) if how == "H" else evaluate(payload, w)
            _polyline(fig, ax, box, f"curve{i}", pen, dash, w, ys, None)
        else:
            ex = compile_expr(payload)
            xs = np.linspace(g.xmin, g.xmax, N_SAMPLES)
            if ex.fn is not None:
                _polyline(fig, ax, box, f"curve{i}", pen, dash, xs, ex(xs), ex)
            for t0, wgt in ex.deltas():
                if g.xmin <= t0 <= g.xmax:
                    px = float(ax.px(t0))
                    y0 = float(np.clip(ax.py(0.0), t, fig.h - b))
                    y1 = float(ax.py(min(max(wgt, g.ymin), g.ymax)))
                    if abs(y1 - y0) > 4:
                        fig.line(f"impulse{i}", pen, [(px, y0), (px, y1)])
                        _arrow(fig, f"impulse{i}", pen, (px, y1), (0, y1 - y0))
                    if abs(wgt - 1) > 1e-9:
                        fig.text(f"impulse{i}", pen, f"({wgt:g})", px + 6,
                                 y1 + (CAP * LEG if wgt < 0 else -4), LEG, "l")
        text = lab or ("H" if how == "H" else "x[n]" if how == "values"
                       else _auto_label(payload, i, kind))
        w = (18 if kind == "stem" else 40) + 8 + text_width(text, LEG)
        if room[0] + w <= fig.w - 6:                   # top margin row, left to right
            x1, y = room[0] + w - text_width(text, LEG) - 8, t - 8
            room[0] += w + 18
        else:                                          # row full: inside, top right
            room[1] += 1
            x1, y = fig.w - r - 4 - text_width(text, LEG) - 8, t + 6 + room[1] * 28
        if lab is not None or len(ser) > 1:
            _legend(fig, i, text, pen, dash, kind == "stem", x1, y)


def _legend(fig, i, text, pen, dash, stem, x1, y):
    mid = y - CAP * LEG / 2
    if stem:
        fig.line(f"legend{i}", pen, [(x1 - 14, mid + 9), (x1 - 14, mid - 3)], dash)
        fig.line(f"legend{i}", pen, _head(i % 4, x1 - 14, mid - 7, 4.5))
    else:
        fig.line(f"legend{i}", pen, [(x1 - 40, mid), (x1, mid)], dash)
    fig.text(f"legend{i}", pen, text, x1 + 8, y, LEG, "l")


def _polyline(fig, ax, box, part, pen, dash, xs, ys, ex):
    """One curve: jumps located by bisection -> vertical edges (rect, u) or
    breaks (tan's poles), clipped to the plot box, thinned, dashed."""
    g = ax.g
    ys = np.asarray(ys, dtype=float)
    span = g.ymax - g.ymin
    jump = {}
    if ex is not None and ex.fn is not None:
        d = np.abs(np.diff(ys))
        cand = np.nonzero(np.isfinite(d) & (d > 0.05 * span))[0]
        if cand.size:
            lo, hi, flo, fhi = xs[cand], xs[cand + 1], ys[cand], ys[cand + 1]
            for _ in range(42):
                mid = (lo + hi) / 2
                fm = ex(mid)
                left = np.abs(fm - flo) >= np.abs(fhi - fm)
                hi, fhi = np.where(left, mid, hi), np.where(left, fm, fhi)
                lo, flo = np.where(left, lo, mid), np.where(left, flo, fm)
            res = np.abs(fhi - flo)
            for c, xm, a, bb, rr in zip(cand, (lo + hi) / 2, flo, fhi, res):
                if not np.isfinite(rr) or rr > 0.5 * d[c]:
                    jump[int(c)] = (float(xm), float(a), float(bb))
    elif ex is None:                                    # sampled data (freq response)
        d = np.abs(np.diff(ys))
        for c in np.nonzero(np.isfinite(d) & (d > 0.45 * span))[0]:
            jump[int(c)] = None
    pieces, cur, pins = [], [], set()

    def flush():
        nonlocal cur, pins
        if len(cur) > 1:
            pieces.append((cur, pins))
        cur, pins = [], set()

    lim_lo, lim_hi = g.ymin - 1e-3 * span, g.ymax + 1e-3 * span
    for i in range(len(xs)):
        y = ys[i]
        if not math.isfinite(y):
            flush()
            continue
        cur.append((xs[i], y))
        if i in jump:
            jv = jump[i]
            if jv is None:
                flush()
                continue
            xm, a, bb = jv
            if math.isfinite(a):
                pins.add(len(cur))
                cur.append((xm, a))
            if math.isfinite(a) and math.isfinite(bb) and lim_lo <= a <= lim_hi \
                    and lim_lo <= bb <= lim_hi:
                pins.add(len(cur))
                cur.append((xm, bb))
            else:
                flush()
                if math.isfinite(bb):
                    pins.add(0)
                    cur.append((xm, bb))
    flush()
    for pts, pn in pieces:
        P = [(float(ax.px(x)), float(ax.py(y))) for x, y in pts]
        for q, qp in _clip(P, pn, box):
            fig.line(part, pen, _thin(q, qp), dash)


def _stems(g, fig, ax, i, how, payload, pen, dash, box):
    s = g.spec
    n0, n1 = s["n"]
    ns = np.arange(n0, n1 + 1, dtype=float)
    vals = np.array(s["values"], dtype=float) if how == "values" else evaluate(payload, ns, True)
    l, t, r, b = box
    y0 = float(np.clip(ax.py(0.0), t, b))
    for n, v in zip(ns, vals):
        if not math.isfinite(v):
            continue
        px = float(ax.px(n + 0.2 * i))
        py = float(ax.py(v))
        if not (l <= px <= r):
            continue
        inside = t <= py <= b
        tip = min(max(py, t), b)
        if abs(tip - y0) > 5.5:
            end = tip + (5 if tip < y0 else -5) if inside else tip
            fig.line(f"curve{i}", pen, [(px, y0), (px, end)], dash)
        if inside:
            fig.line(f"curve{i}", pen, _head(i % 4, px, py))


def _pz(g, fig: _Fig):
    s = g.spec
    side, l, t = 360, 30, 76
    ax = _Axes(g, l, t, l + side, t + side)
    _title(fig, g)
    ox = float(np.clip(ax.px(0.0), l, l + side))
    oy = float(np.clip(ax.py(0.0), t, t + side))
    fig.line("axes", 0, [(l, oy), (l + side + 12, oy)])
    _arrow(fig, "axes", 0, (l + side + 12, oy), (1, 0))
    fig.line("axes", 0, [(ox, t + side), (ox, t - 14)])
    _arrow(fig, "axes", 0, (ox, t - 14), (0, -1))
    fig.text("text", 0, s["xlabel"], l + side + 12, oy - 10, AXLAB, "r")
    fig.text("text", 0, s["ylabel"], ox + 10, t - 8, AXLAB, "l")
    cap = CAP * TICK
    z = s["domain"] == "z"
    if z:
        pts = [(float(ax.px(math.cos(a))), float(ax.py(math.sin(a))))
               for a in np.linspace(0, 2 * math.pi, 97)]
        for q, _ in _clip(pts, set(), (l, t, l + side, t + side)):
            fig.line("axes", 0, q, (9.0, 6.0))
        ticks = [(1.0, "1"), (-1.0, "−1")]
        yt = [(1.0, "j"), (-1.0, "−j")]
    else:
        ticks = [(v, lab) for v, lab in _ticks(g.xmin, g.xmax, target=4) if abs(v) > 1e-12]
        yt = [(v, lab) for v, lab in _ticks(g.ymin, g.ymax, target=4) if abs(v) > 1e-12]
    for v, lab in ticks:
        if g.xmin < v < g.xmax:
            px = float(ax.px(v))
            fig.line("axes", 0, [(px, oy - 4), (px, oy + 4)])
            fig.text("axes", 0, lab, px + (6 if z and v > 0 else 0), oy + 9 + cap, TICK,
                     "l" if z and v > 0 else "c")
    for v, lab in yt:
        if g.ymin < v < g.ymax:
            py = float(ax.py(v))
            fig.line("axes", 0, [(ox - 4, py), (ox + 4, py)])
            fig.text("axes", 0, lab, ox - 8, py + (-6 if z and v > 0 else cap / 2), TICK, "r")
    zs, ps = zeros_poles(s)
    zpen, ppen = curve_style(0)[0], curve_style(1)[0]
    for roots, part, pen in ((zs, "zeros", zpen), (ps, "poles", ppen)):
        seen = []
        for rt in roots:
            for k, (q, cnt) in enumerate(seen):
                if abs(q - rt) < 1e-6 * (1 + abs(rt)):
                    seen[k] = (q, cnt + 1)
                    break
            else:
                seen.append((rt, 1))
        for rt, cnt in seen:
            if not (g.xmin <= rt.real <= g.xmax and g.ymin <= rt.imag <= g.ymax):
                continue
            cx, cy = float(ax.px(rt.real)), float(ax.py(rt.imag))
            if part == "zeros":
                fig.line(part, pen, _head(0, cx, cy, 8.0))
            else:
                fig.line(part, pen, [(cx - 7, cy - 7), (cx + 7, cy + 7)])
                fig.line(part, pen, [(cx - 7, cy + 7), (cx + 7, cy - 7)])
            if cnt > 1:
                fig.text(part, pen, f"({cnt})", cx + 10, cy - 10, LEG, "l")


def size(g) -> tuple[int, int]:
    return PZ_SIZE if g.kind == "pole_zero" else XY_SIZE


def scale(g) -> float:
    return min(1.0, g.max_w / size(g)[0])


def box(g) -> tuple[int, int, int, int]:
    """Page box (x0, y0, x1, y1) the graph's strokes stay inside."""
    w, h = size(g)
    k = scale(g)
    return g.x, g.y, g.x + math.ceil(w * k), g.y + math.ceil(h * k)


def _new_parts(g) -> set:
    i = len(g.curves) - 1
    parts = {f"curve{i}", f"impulse{i}", f"legend{i}"}
    if i == 1 and _series(g)[0][0] is None:     # first overlay: curve 0's entry appears now
        parts.add("legend0")
    return parts


def strokes(g, only_new: bool = False) -> list[Ink]:
    """Page strokes of a direct-plot Graph (or, only_new, of its LAST curve
    + legend entry on the unchanged axes), each an Ink with pen + part."""
    fig = _Fig(*size(g))
    if g.kind == "pole_zero":
        if only_new:
            return []
        _pz(g, fig)
    else:
        _xy(g, fig)
    keep = _new_parts(g) if only_new else None
    k, w, h = scale(g), fig.w, fig.h
    out = []
    for part, pen, pts in fig.items:
        if keep is not None and part not in keep:
            continue
        q = []
        for x, y in pts:
            p = (int(round(min(max(x, 0.0), w) * k)) + g.x, int(round(min(max(y, 0.0), h) * k)) + g.y)
            if not q or p != q[-1]:
                q.append(p)
        if len(q) > 1:
            out.append(Ink(q, pen, part))
    return out


# ── preview sheet ────────────────────────────────────────────────────────────

DSP_SHEET = [
    {"type": "function", "title": "rect(t) and sinc(t)", "xlabel": "t", "x": [-3, 3],
     "curves": [{"expr": "rect(t)", "label": "rect(t)"}, {"expr": "sinc(t)", "label": "sinc(t)"}]},
    {"type": "stem", "title": "x[n] = 0.8^n u[n]", "n": [-3, 12],
     "curves": [{"expr": "0.8**n*u(n)", "label": None}]},
    {"type": "pole_zero", "title": "H(z) = 1/(1 − 0.9z^{-1})", "domain": "z",
     "b": [1, 0], "a": [1, -0.9]},
    {"type": "freq_response", "title": "3-point moving average", "domain": "z",
     "b": [1 / 3, 1 / 3, 1 / 3], "a": [1], "scale": "linear", "show": "mag",
     "marks": [{"x": 2 * math.pi / 3, "label": "2π/3"}]},
    {"type": "freq_response", "title": "RC low-pass (Bode)", "domain": "s", "b": [1],
     "a": [1e-3, 1], "scale": "db", "show": "mag", "marks": [{"x": 1000, "label": "ω_c"}]},
    {"type": "function", "title": "step response", "x": [-1, 5], "y": [-0.25, 1.25],
     "curves": [{"expr": "(1-exp(-t))*u(t)", "label": "y(t)"},
                {"expr": "u(t)", "label": "u(t)"}]},
]


def preview(path: str = "build/dsp_preview.png") -> list[float]:
    """Render DSP_SHEET onto one page PNG; returns per-graph ms."""
    import time

    import render_plan
    spots = [(40, 60), (730, 60), (40, 560), (730, 560), (40, 1100), (730, 1100)]
    placed, ms = [], []
    for i, (spec, (x, y)) in enumerate(zip(DSP_SHEET, spots)):
        t0 = time.perf_counter()
        g = render(spec, f"G{i + 1}", x=x, y=y, max_w=640)
        st = graph.strokes(g)
        ms.append((time.perf_counter() - t0) * 1000)
        placed.append({"strokes": st})
    render_plan.preview(placed, path)
    return ms


if __name__ == "__main__":
    if sys.argv[1:2] == ["preview"]:
        preview(sys.argv[2] if len(sys.argv) > 2 else "build/dsp_preview.png")   # warm glyphs
        out = preview(sys.argv[2] if len(sys.argv) > 2 else "build/dsp_preview.png")
        print("ms per graph (warm):", " ".join(f"{v:.0f}" for v in out))
    else:
        sys.exit(__doc__)
