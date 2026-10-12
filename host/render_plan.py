"""
render_plan.py — a model reply -> an ordered plan of render Blocks.

Slice 1 of the tool-calling middleware: pure, deterministic, serializable.

    normalize(text)          -> [Block]   kinds: text | latex | graph | circuit | diagram
    parse(text)              -> ([Block], skipped)   skipped = [{reason, excerpt}]
    route(request)           -> diagram | graph | circuit | text   (bug #4)
    enforce_visual(req, txt) -> (reply, why)  prose-only answer to a diagram ask
                                              -> boxes-and-arrows fallback
    tex_source(body)         -> what pdflatex compiles for one $$ body
                                (curve roles + dash, answer in red)
    graph_tex(spec, label)   -> pgfplots for a GraphSpec (the plan's graph
                                block) when host/plots.py is missing or rejects it
    plot_graph(b) / graph_batches(g)  GraphSpec -> plots.render Graph -> strokes per pen
    layout / preview / session_entries  — the offline render + placement path

Colours are xochitl pen enums (graph.COLOR_NAMES). The RM2 panel shows blue
and red as greys, so curves also differ by dash pattern (no dotted styles:
dots do not survive skeleton -> trace, see graph.py).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field

import protocol
from tex import GRAY_KEY, block_src

ROLE_COLOR = {"prose": 0, "equation": 0, "graph": 0, "circuit": 0, "diagram": 0,
              "answer": 7, "highlight": 6}
CURVES = ((6, "solid"), (7, "dashed"), (1, "dash dot"))     # (pen, dash) per curve, cycled
TEX_COLOR = {0: "black", 6: "blue", 7: "red", 1: GRAY_KEY}
VISUAL = ("graph", "circuit", "diagram")
MAX_STEPS, MAX_LABEL = 6, 24                                 # the fallback diagram's bounds


@dataclass(frozen=True)
class Block:
    kind: str                # text | latex | graph | circuit | diagram
    role: str                # prose | equation | answer | graph | circuit | diagram
    color: int               # pen enum of the block (a graph's axes; curves below)
    src: str                 # prose, a $$ body (LaTeX), or an <svg> document
    label: str = ""          # G1, G2 ... for graphs (session-unique)
    curves: tuple = ()       # graph: ((pen, dash), ...) in plot order
    reason: str = ""         # what the normalizer repaired or fell back on
    # graph from a JSON plan: the canonical GraphSpec (plan_schema.plot); src = its JSON
    spec: dict | None = field(default=None, hash=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["curves"] = [list(c) for c in self.curves]
        return d


# ── tokens ────────────────────────────────────────────────────────────────────

_TOKEN = re.compile(
    r"```(?P<lang>[\w-]*)[ \t]*\n(?P<fence>.*?)```"
    r"|\$\$(?P<math>.+?)\$\$"
    r"|(?P<svg><svg\b.*?</svg>)"
    r"|(?P<env>\\begin\{(?P<envname>circuitikz|tikzpicture)\}.*?\\end\{(?P=envname)\})"
    r"|\\\[(?P<disp>.+?)\\\]", re.S | re.I)
_OPENERS = (("```", "unclosed ```"), ("$$", "unclosed $$"), ("<svg", "unclosed <svg"),
            ("\\begin{circuitikz}", "unclosed circuitikz"),
            ("\\begin{tikzpicture}", "unclosed tikzpicture"))
_ANSWER_HEAD = re.compile(r"^\W*(final\s+answers?|answers?|results?)\b", re.I)
_AXIS = re.compile(r"\\begin\{(?:axis|semilogxaxis|semilogyaxis|loglogaxis|polaraxis)\}")


def _kind(body: str) -> str:
    if "circuitikz" in body:
        return "circuit"
    if _AXIS.search(body):
        return "graph"
    if "tikzpicture" in body:
        return "diagram"
    return "latex"


def _inline(text: str, m: re.Match) -> bool:
    """A $$..$$ with prose on its line reads as text (monke._segments rule)."""
    before = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
    end = text.find("\n", m.end())
    after = text[m.end():end if end >= 0 else len(text)]
    return bool(before.strip() or after.strip()) and "\\begin" not in m.group("math")


class _Plan:
    def __init__(self, taken):
        self.blocks, self.skipped = [], []
        self.taken, self.n, self.answer = set(taken or ()), 0, False

    def skip(self, reason: str, raw: str) -> None:
        self.skipped.append({"reason": reason, "excerpt": " ".join(raw.split())[:40]})

    def add(self, kind: str, role: str, src: str, **kw) -> None:
        if kind in VISUAL:
            self.answer = False                  # an answer section ends at a drawing
        self.blocks.append(Block(kind, role, ROLE_COLOR[role], src, **kw))

    def label(self) -> str:
        while True:
            self.n += 1
            if f"G{self.n}" not in self.taken:
                return f"G{self.n}"

    def text(self, seg: str) -> None:
        masked = re.sub(r"\$\$.+?\$\$", lambda m: " " * len(m.group()), seg, flags=re.S)
        cut = min(((i, why) for o, why in _OPENERS if (i := masked.find(o)) >= 0),
                  default=None)
        if cut:
            seg, rest = seg[:cut[0]], seg[cut[0]:]
            self.skip(cut[1], rest)
        seg = seg.strip()
        if not seg:
            return
        if seg.startswith("{") and seg.endswith("}"):
            return self.json(seg)
        if _ANSWER_HEAD.match(seg):
            self.answer = True
        self.add("text", "answer" if self.answer else "prose", seg)

    def tex(self, body: str) -> None:
        body = body.strip()
        if not body:
            return self.skip("empty $$ block", "$$$$")
        kind = _kind(body)
        if kind == "graph":
            label = self.label()
            src, curves, why = _restyle(body, label)
            return self.add("graph", "graph", src, label=label, curves=curves,
                            reason="; ".join(why))
        if kind == "latex":
            answer = self.answer or "\\boxed" in body
            return self.add("latex", "answer" if answer else "equation", body)
        self.add(kind, kind, body)

    def svg(self, doc: str) -> None:
        self.add("diagram", "diagram", doc.strip())

    def json(self, raw: str) -> None:
        try:
            obj = json.loads(raw)
        except ValueError as e:
            return self.skip(f"json: {str(e)[:60]}", raw)
        if isinstance(obj, dict):
            steps = obj.get("steps") or obj.get("nodes")
            if isinstance(steps, list) and steps:
                labels = [s.get("label") or s.get("text") or "" if isinstance(s, dict) else str(s)
                          for s in steps]
                return self.add("diagram", "diagram",
                                fallback_svg(str(obj.get("title") or "Diagram"), labels),
                                reason="json steps -> boxes")
            if isinstance(obj.get("latex"), str):
                return self.tex(obj["latex"])
            if isinstance(obj.get("text"), str):
                return self.text(obj["text"])
        self.skip("json: no steps/latex/text", raw)

    def fence(self, lang: str, body: str) -> None:
        lang = lang.lower()
        if lang == "json" or (not lang and body.lstrip().startswith("{")):
            return self.json(body.strip())
        if lang in ("svg", "xml", "html") or "<svg" in body:
            m = re.search(r"<svg\b.*?</svg>", body, re.S | re.I)
            return self.svg(m.group(0)) if m else self.skip("unclosed <svg", body)
        if lang in ("latex", "tex", "tikz", "circuitikz") or "\\begin{" in body:
            inner = body.strip()
            for a, b in (("$$", "$$"), ("\\[", "\\]")):
                if inner.startswith(a) and inner.endswith(b) and len(inner) > len(a) + len(b):
                    inner = inner[len(a):-len(b)]
            return self.tex(inner)
        if body.strip():
            self.add("text", "prose", body.strip(), reason=f"code fence {lang or '(plain)'}")


def parse(text: str, taken=()) -> tuple[list[Block], list[dict]]:
    """Reply -> (blocks in reply order, skipped). Never raises on model text:
    a malformed part is skipped with its reason; LaTeX/SVG source is never
    turned into a text block. `taken` = graph labels already in the session."""
    _, body = protocol.parse_directives(text or "")
    plan, pos = _Plan(taken), 0
    for m in _TOKEN.finditer(body):
        if m.group("math") is not None and _inline(body, m):
            continue                          # stays inside the prose around it
        plan.text(body[pos:m.start()])
        if m.group("fence") is not None:
            plan.fence(m.group("lang"), m.group("fence"))
        elif m.group("math") is not None:
            plan.tex(m.group("math"))
        elif m.group("svg") is not None:
            plan.svg(m.group("svg"))
        elif m.group("env") is not None:
            plan.tex(m.group("env"))
        else:
            plan.tex(m.group("disp"))
        pos = m.end()
    plan.text(body[pos:])
    return plan.blocks, plan.skipped


def normalize(text: str, taken=()) -> list[Block]:
    """Reply -> ordered Blocks (parse() without the skip record)."""
    return parse(text, taken)[0]


# ── graph curves: role colour + dash per \addplot ────────────────────────────

_COLORS = {"black", "white", "red", "green", "blue", "cyan", "magenta", "yellow", "gray",
           "grey", "darkgray", "lightgray", "brown", "lime", "olive", "orange", "pink",
           "purple", "teal", "violet"}
_PEN_WORDS = {"solid", "dotted", "densely dotted", "loosely dotted", "dashed", "densely dashed",
              "loosely dashed", "dash dot", "densely dash dot", "loosely dash dot", "dash dot dot",
              "densely dash dot dot", "loosely dash dot dot", "dashdotted", "densely dashdotted",
              "loosely dashdotted", "dashdotdotted", "densely dashdotdotted",
              "loosely dashdotdotted", "ultra thin", "very thin", "thin", "semithick", "thick",
              "very thick", "ultra thick"}
_PEN_KEYS = {"color", "draw", "line width", "dash pattern"}
_MARKER = re.compile(r"only marks|forget plot|draw\s*=\s*none|[xy]comb")


def _close(s: str, i: int) -> int:
    """Index of the ']' closing the '[' at s[i] (braces nest); -1 if none."""
    depth = 0
    for j in range(i, len(s)):
        ch = s[j]
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return j if ch == "]" else -1
    return -1


def _opts(s: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in s:
        depth += ch in "{[(" and 1 or (-1 if ch in "}])" else 0)
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return [p.strip() for p in parts + [cur] if p.strip()]


def _pen_opt(o: str) -> bool:
    key = o.split("=", 1)[0].strip().lower()
    if "=" in o:
        return key in _PEN_KEYS
    base = re.fullmatch(r"([a-z]+)(?:!\d+(?:\.\d+)?(?:![a-z]+)?)*", key)
    return key in _PEN_WORDS or bool(base and base.group(1) in _COLORS)


def _restyle(src: str, label: str = "") -> tuple[str, tuple, list[str]]:
    out, curves, why, pos = [], [], [], 0
    for m in re.finditer(r"\\addplot(\+?)", src):
        if m.start() < pos:
            continue
        j = m.end()
        while j < len(src) and src[j] in " \t\r\n":
            j += 1
        if j < len(src) and src[j].isalnum():          # \addplot3, \addplotfoo: not ours
            continue
        opts, end = "", m.end()
        if j < len(src) and src[j] == "[":
            k = _close(src, j)
            if k < 0:
                why.append("unbalanced \\addplot options kept as written")
                continue
            opts, end = src[j + 1:k], k + 1
        if _MARKER.search(opts):
            continue                                   # a marker, not a curve
        pen, dash = CURVES[len(curves) % len(CURVES)]
        curves.append((pen, dash))
        keep = [o for o in _opts(opts) if not _pen_opt(o)]
        out.append(src[pos:m.start()] + "\\addplot["
                   + ", ".join([TEX_COLOR[pen], "thick", dash] + keep) + "]")
        pos = end
    src = "".join(out) + src[pos:]
    if not curves:
        why.append("no curves to style")
    if label:
        src = _title(src, label)
    return src, tuple(curves), why


def _title(src: str, label: str) -> str:
    m = _AXIS.search(src)
    if not m:
        return src
    j = m.end()
    if j < len(src) and src[j] == "[" and (k := _close(src, j)) > 0:
        opts = _opts(src[j + 1:k])
        t = next((i for i, o in enumerate(opts) if o.split("=", 1)[0].strip() == "title"), None)
        if t is None:
            opts.insert(0, f"title={{{label}}}")
        else:
            old = opts[t].split("=", 1)[1].strip()
            old = old[1:-1] if old.startswith("{") and old.endswith("}") else old
            if not old.startswith(label):
                opts[t] = f"title={{{label}: {old}}}"
        return src[:j] + "[" + ", ".join(opts) + "]" + src[k + 1:]
    return src[:j] + f"[title={{{label}}}]" + src[j:]


def restyle(src: str, label: str = "") -> str:
    """A pgfplots body with each curve's pen set by its role (blue solid, red
    dashed, gray dash-dot, cycled) whatever the model wrote; markers (only
    marks / forget plot) untouched. Idempotent."""
    return _restyle(src, label)[0]


def tex_source(body: str, color: int | None = None) -> str:
    """One $$ body as pdflatex compiles it: graphs restyled, an answer
    (\\boxed, or color=7 from the plan) typeset in red."""
    kind = _kind(body.strip())
    if kind == "graph":
        body = restyle(body)
    src = block_src(body)
    if color is None:
        color = 7 if kind == "latex" and "\\boxed" in body else 0
    if kind == "latex" and color:
        src = "{\\color{%s}%s}" % (TEX_COLOR.get(color, "black"), src)
    return src


# ── the diagram route (bug #4) ───────────────────────────────────────────────

_PLOT = re.compile(r"\bplot|\by\s*=|\b[fgh]\s*\(\s*[xt]\s*\)|\bvs\.?(?=\s)|\bversus\b"
                   r"|\bcurves?\b|\baxes\b|\bwaveforms?\b", re.I)
_FLOW = re.compile(r"\b(algorithms?|flow|steps?|process(?:es)?|pipelines?|procedures?|workflows?"
                   r"|state\s+machines?|architecture|lifecycle|hierarchy|phasors?|block)\b", re.I)
_DIAGRAM = re.compile(r"\b(flow\s*charts?|flowcharts?|block\s+diagrams?|phasor\s+diagrams?"
                      r"|state\s+diagrams?|diagrams?|mind\s*maps?)\b", re.I)
_DRAW = re.compile(r"\b(draw|sketch|represent|visuali[sz]e|illustrate|depict|map\s+out)\b", re.I)
_NOUN = re.compile(r"\b(graphs?|charts?|diagrams?|flowcharts?|pictures?|figures?|boxes|arrows)\b",
                   re.I)
_ABOUT = re.compile(r"^\W*(explain|describe|what|why|how\s+does|interpret|read)\b", re.I)
_CIRCUIT = re.compile(r"\b(circuits?|schematics?)\b", re.I)


def _request(request) -> str:
    if isinstance(request, dict):
        return " ".join(str(request.get(k) or "") for k in ("text", "summary")).strip()
    return str(request or "")


def route(request) -> str:
    """What the request must yield: 'graph' (function/data plot — Python's
    graph route), 'diagram' (flow / algorithm / block / phasor drawing),
    'circuit', or 'text'. request = Navy's words, or a reading dict (its
    text + summary; the reader's own kind is not trusted: it called the
    bug #4 ask 'text')."""
    t = _request(request)
    if not t.strip():
        return "text"
    if _PLOT.search(t):
        return "graph"
    if _FLOW.search(t) and (_DRAW.search(t) or _NOUN.search(t)):
        return "diagram"
    if _DIAGRAM.search(t) and _DRAW.search(t) and not _ABOUT.match(t):
        return "diagram"
    if _CIRCUIT.search(t) and _DRAW.search(t):
        return "circuit"
    if re.search(r"\bgraphs?\b", t, re.I):
        return "graph"
    return "text"


DIAGRAM_ASK = ("Answer with ONE <svg> diagram (boxes, arrows, <text> labels), "
               "not with a description of one.")


def _clean(label: str) -> str:
    s = re.sub(r"[*_`$<>\"]", "", str(label)).replace("&", " and ")
    s = " ".join(s.encode("ascii", "ignore").decode().split()).strip(" .;:,-")
    if len(s) > MAX_LABEL:
        s = s[:MAX_LABEL + 1].rsplit(" ", 1)[0] if " " in s[:MAX_LABEL + 1] else s[:MAX_LABEL]
        s = s[:MAX_LABEL].rstrip(" .;:,-")
    return s


def steps_of(text: str) -> list[str]:
    """Steps named in prose: numbered (1. / 1) / Step 1:), bullets, or an
    A -> B -> C chain. [] when there are none."""
    text = text or ""
    found = re.findall(r"(?:^|(?<=\s))(?:step\s*)?\d{1,2}\s*[.):]\s+(.+?)"
                       r"(?=\s+(?:step\s*)?\d{1,2}\s*[.):]\s|\n|$)", text, re.I | re.S)
    if len(found) < 2:
        found = re.findall(r"^\s*[-*\u2022]\s+(.+)$", text, re.M)
    if len(found) < 2:
        chain = max((ln for ln in text.splitlines() if "->" in ln or "\u2192" in ln),
                    key=len, default="")
        found = re.split(r"\s*(?:->|\u2192|=>)\s*", chain) if chain else []
    out = [s for s in (_clean(f) for f in found) if s]
    return out[:MAX_STEPS]


def _title_of(request: str) -> str:
    t = _request(request)
    m = _DIAGRAM.search(t) or _FLOW.search(t)
    return _clean(m.group(0).title()) if m else "Diagram"


def fallback_svg(title: str, steps: list[str]) -> str:
    """Bounded, deterministic flow: a title, <= MAX_STEPS boxes three per
    row, an arrow between consecutive boxes. Fewer than two steps ->
    Input -> (step | Process) -> Output. 1100 units wide: svg.py scales it
    to ~1.1x, labels stay inside their boxes."""
    steps = [s for s in (_clean(s) for s in steps) if s][:MAX_STEPS]
    if len(steps) < 2:
        steps = ["Input", steps[0] if steps else "Process", "Output"]
    bw, bh, gap_y, top = 320, 80, 90, 70
    cols = min(3, len(steps))
    pitch = (1100 - bw) // (cols - 1)
    pos = [((i % 3) * pitch, top + (i // 3) * (bh + gap_y)) for i in range(len(steps))]
    rows = (len(steps) + 2) // 3
    el = [f'<svg viewBox="0 0 1100 {top + rows * bh + (rows - 1) * gap_y}" '
          f'xmlns="http://www.w3.org/2000/svg">',
          f'<text x="550" y="40" font-size="34" text-anchor="middle">{_clean(title) or "Diagram"}</text>']
    for (x, y), s in zip(pos, steps):
        el.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}"/>')
        el.append(f'<text x="{x + bw // 2}" y="{y + bh // 2 + 9}" font-size="24" '
                  f'text-anchor="middle">{s}</text>')
    for (x0, y0), (x1, y1) in zip(pos, pos[1:]):
        if y1 == y0:                                  # same row: left to right
            cy = y0 + bh // 2
            el.append(f'<polyline points="{x0 + bw},{cy} {x1},{cy}"/>')
            el.append(f'<polygon points="{x1 - 16},{cy - 10} {x1},{cy} {x1 - 16},{cy + 10}"/>')
        else:                                         # next row: down, back left, down
            ax, bx, mid = x0 + bw // 2, x1 + bw // 2, y0 + bh + gap_y // 2
            el.append(f'<polyline points="{ax},{y0 + bh} {ax},{mid} {bx},{mid} {bx},{y1}"/>')
            el.append(f'<polygon points="{bx - 10},{y1 - 16} {bx},{y1} {bx + 10},{y1 - 16}"/>')
    return "\n".join(el + ["</svg>"])


def enforce_visual(request, reply: str) -> tuple[str, str]:
    """The mandatory visual-artifact gate: a diagram request whose reply has
    no drawing (graph / circuit / diagram block) gets the fallback diagram
    built from the steps the prose names. Returns (reply, why); why == ""
    when the reply passes untouched."""
    if route(request) != "diagram":
        return reply, ""
    blocks = normalize(reply)
    if any(b.kind in VISUAL for b in blocks):
        return reply, ""
    prose = " ".join(b.src for b in blocks if b.kind == "text")
    steps = steps_of(prose)
    why = "prose-only reply" if blocks else "empty reply"
    return (fallback_svg(_title_of(request), steps),
            f"{why} to a diagram request -> fallback diagram ({max(3, len(steps))} boxes)")


# ── GraphSpec (plan graph blocks): plots.render, else sampled pgfplots ───────

def _step(v):
    import numpy as np
    return np.where(v >= 0, 1.0, 0.0)


def _funcs() -> dict:
    import numpy as np
    return {"sin": np.sin, "cos": np.cos, "tan": np.tan, "exp": np.exp, "log": np.log,
            "ln": np.log, "log10": np.log10, "sqrt": np.sqrt, "abs": np.abs,
            "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan, "arctan": np.arctan,
            "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh, "sign": np.sign,
            "sgn": np.sign, "floor": np.floor, "ceil": np.ceil, "u": _step, "step": _step,
            "heaviside": _step, "rect": lambda v: np.where(np.abs(v) <= 0.5, 1.0, 0.0),
            "tri": lambda v: np.maximum(1 - np.abs(v), 0.0), "sinc": np.sinc,
            "max": np.maximum, "min": np.minimum, "pow": np.power}


_VARS = ("t", "x", "n", "w", "omega", "f", "k")
_ETOK = re.compile(r"\s*(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?|[A-Za-z_]\w*"
                   r"|\*\*|[-+*/^(),])")


def sample(expr: str, xs):
    """A GraphSpec expr over xs. Whitelist tokenizer (numbers, one variable
    t|x|n|w|omega|f|k, pi, e, _funcs names) — model text is never eval'd raw."""
    import numpy as np
    out, pos, s, funcs = [], 0, expr.strip(), _funcs()
    while pos < len(s):
        m = _ETOK.match(s, pos)
        if not m:
            raise ValueError(f"bad token at {s[pos:pos + 8]!r}")
        t, pos = m.group(1), m.end()
        if t[0].isalpha() or t[0] == "_":
            tl = t.lower()
            if tl in _VARS:
                out.append("X")
            elif tl in ("pi", "e"):
                out.append(tl)
            elif tl in funcs:
                out.append(f"F['{tl}']")
            else:
                raise ValueError(f"unknown name {t!r}")
        else:
            out.append("**" if t == "^" else t)
    with np.errstate(all="ignore"):
        y = eval(" ".join(out), {"__builtins__": {}},           # noqa: S307 — whitelisted
                 {"X": xs, "pi": math.pi, "e": math.e, "F": funcs})
    return np.broadcast_to(np.asarray(y, dtype=float), np.shape(xs))


_TEX_SYM = {"\u03c9": r"$\omega$", "\u03a9": r"$\Omega$", "\u00b5": r"$\mu$", "\u03bc": r"$\mu$",
            "\u03c0": r"$\pi$", "\u00b0": r"$^\circ$", "\u03b8": r"$\theta$", "\u03c6": r"$\phi$",
            "\u2220": r"$\angle$"}


def _tex_text(s) -> str:
    """Axis/title text for pgfplots: $..$ kept as maths, the rest escaped."""
    s = "".join(_TEX_SYM.get(ch, ch) for ch in str(s or "")).encode("ascii", "ignore").decode()
    parts = s.split("$")
    if len(parts) % 2 == 0:                       # an unpaired $: all text
        parts = [s.replace("$", "")]
    esc = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "_": r"\_", "^": r"\^{}",
           "&": r"\&", "%": r"\%", "#": r"\#", "~": r"\~{}"}
    return "$".join(p if i % 2 else "".join(esc.get(c, c) for c in p)
                    for i, p in enumerate(parts))


def _coords(xs, ys) -> str:
    return " ".join(f"({x:.5g},{y:.5g})" if math.isfinite(y) else f"({x:.5g},nan)"
                    for x, y in zip(xs, ys))


def _yrange(spec, ys_all) -> tuple[float, float]:
    import numpy as np
    if spec.get("y"):
        return spec["y"][0], spec["y"][1]
    v = np.concatenate([np.ravel(y) for y in ys_all]) if ys_all else np.zeros(1)
    v = v[np.isfinite(v)]
    lo, hi = (float(v.min()), float(v.max())) if v.size else (-1.0, 1.0)
    pad = (hi - lo) * 0.1 or 1.0
    return lo - pad, hi + pad


def graph_tex(spec: dict, label: str = "") -> str:
    """A canonical GraphSpec -> pgfplots, sampled here (no pgfplots maths to
    break). Curves are plain \\addplot, so restyle() gives each its role pen
    + dash; marks / stems / poles / zeros / the unit circle stay black.
    Raises ValueError on an expression it cannot sample."""
    import numpy as np
    kind = spec["type"]
    title = ": ".join(t for t in (label, _tex_text(spec.get("title") or "")) if t)
    xl = spec.get("xlabel") or {"function": "$t$", "stem": "$n$", "pole_zero": "Re",
                                "freq_response": r"$\omega$"}[kind]
    yl = spec.get("ylabel") or {"function": "", "stem": "$x[n]$", "pole_zero": "Im",
                                "freq_response": "|H|"}[kind]
    body, opts = [], []
    if kind == "function":
        x0, x1 = spec.get("x") or (-5.0, 5.0)
        xs = np.linspace(x0, x1, 400)
        ys = [sample(c["expr"], xs) for c in spec["curves"]]
        y0, y1 = _yrange(spec, ys)
        span = y1 - y0
        for c, y in zip(spec["curves"], ys):
            y = np.clip(y, y0 - span, y1 + span)          # a pole doesn't blow the axis up
            body.append(r"\addplot[no markers] coordinates {%s};" % _coords(xs, y))
            if c.get("label"):
                body.append(r"\addlegendentry{%s}" % _tex_text(c["label"]))
    elif kind == "stem":
        vals = spec.get("values")
        n0, n1 = spec.get("n") or (0, max(len(vals or [0]) - 1, 1))
        ns = np.arange(int(n0), int(n1) + 1, dtype=float)[:512]
        if vals:
            ys = np.array([vals[i] if i < len(vals) else 0.0 for i in range(len(ns))])
        else:
            ys = sample(spec["curves"][0]["expr"], ns)
        x0, x1 = ns[0] - 0.5, ns[-1] + 0.5
        y0, y1 = _yrange(spec, [ys, np.zeros(1)])
        body.append(r"\addplot[ycomb, mark=*] coordinates {%s};" % _coords(ns, ys))
    elif kind == "pole_zero":
        pts = (spec.get("zeros") or []) + (spec.get("poles") or [])
        r = max([1.2 if spec.get("domain") == "z" else 0.5] +
                [abs(p[0]) * 1.2 + 0.2 for p in pts] + [abs(p[1]) * 1.2 + 0.2 for p in pts])
        x0, x1, y0, y1 = -r, r, -r, r
        opts.append("axis equal image")
        if spec.get("domain") == "z":
            body.append(r"\addplot[forget plot, domain=0:360, samples=90, thin] "
                        r"({cos(x)}, {sin(x)});")
        for key, mark in (("zeros", "o"), ("poles", "x")):
            if spec.get(key):
                body.append(r"\addplot[only marks, mark=%s, mark size=4pt, thick] coordinates {%s};"
                            % (mark, " ".join(f"({p[0]:.5g},{p[1]:.5g})" for p in spec[key])))
    else:                                         # freq_response
        z = spec.get("domain") != "s"
        x0, x1 = spec.get("x") or ((0.0, math.pi) if z else (0.0, 100.0))
        w = np.linspace(x0, x1, 400)
        b, a = np.asarray(spec["b"], float), np.asarray(spec.get("a") or [1.0], float)
        with np.errstate(all="ignore"):
            if z:
                zi = np.exp(-1j * w)
                h = np.polyval(b[::-1], zi) / np.polyval(a[::-1], zi)
            else:
                h = np.polyval(b, 1j * w) / np.polyval(a, 1j * w)
            if spec.get("show") == "phase":
                ys, yl = np.angle(h), spec.get("ylabel") or "phase (rad)"
            elif spec.get("scale") == "db":
                ys, yl = 20 * np.log10(np.abs(h)), spec.get("ylabel") or "|H| (dB)"
            else:
                ys = np.abs(h)
        y0, y1 = _yrange(spec, [ys])
        body.append(r"\addplot[no markers] coordinates {%s};" % _coords(w, ys))
    for m in spec.get("marks") or ():
        body.append(r"\draw[dashed] (axis cs:%.5g,%.5g) -- (axis cs:%.5g,%.5g);"
                    % (m["x"], y0, m["x"], y1))
        if m.get("label"):
            body.append(r"\node[above, font=\small] at (axis cs:%.5g,%.5g) {%s};"
                        % (m["x"], y1, _tex_text(m["label"])))
    opts = ([f"title={{{title}}}"] if title else []) + [
        f"xlabel={{{_tex_text(xl)}}}", f"ylabel={{{_tex_text(yl)}}}", "width=11cm", "height=7cm",
        "axis lines=left", "legend pos=north east", "legend style={draw=none,fill=none}",
        "unbounded coords=jump", "clip mode=individual", "scaled ticks=false",
        "tick label style={/pgf/number format/fixed}",
        f"xmin={x0:.5g}", f"xmax={x1:.5g}", f"ymin={y0:.5g}", f"ymax={y1:.5g}"] + opts
    return (r"\begin{tikzpicture}\begin{axis}[" + ", ".join(opts) + "]"
            + "".join(body) + r"\end{axis}\end{tikzpicture}")


def plot_graph(b: Block):
    """(graph.Graph from Lane C's plots.render, why). (None, why) when
    host/plots.py is missing, rejects the spec, or fails: the caller then
    draws graph_tex() through LaTeX instead."""
    try:
        import plots
    except ImportError:
        return None, "plots.py not available"
    try:
        why = plots.validate(b.spec)
        if why is not None:
            return None, f"plots.validate: {why}"
        g = plots.render(b.spec, b.label or None)
        if getattr(g, "label", "") != b.label and b.label:
            g.label = b.label
        return g, ""
    except Exception as e:
        return None, f"plots: {type(e).__name__}: {str(e)[:100]}"


def graph_batches(g, strokes_fn=None) -> list[tuple[list, int]]:
    """[(strokes, pen)] of a graph.Graph: axes + curve 1 in pen g.color(0),
    each later curve in its own pen (graph.strokes only_new, _plot's overlay
    method); one batch in pen g.color(0) when per-curve diffing fails."""
    import graph
    fn = strokes_fn or graph.strokes
    # Direct GraphSpec plots attach a pen to every stroke: axes/text stay black,
    # curves keep their own blue/red/gray cue. Do not recolour the whole first
    # batch blue merely because its first curve is blue.
    if getattr(g, "kind", "tex") != "tex":
        groups = {}
        for stroke in fn(g):
            groups.setdefault(getattr(stroke, "color", 0), []).append(stroke)
        if groups:
            return [(strokes, pen) for pen, strokes in groups.items()]
    import copy
    curves = list(getattr(g, "curves", None) or [])
    if len(curves) > 1:
        try:
            out = []
            for i in range(len(curves)):
                gi = copy.copy(g)
                gi.curves = curves[:i + 1]
                out.append((fn(gi, only_new=i > 0), g.color(i)))
            return out
        except Exception:
            pass
    return [(fn(g), g.color(0) if curves else 0)]


# ── offline render + placement ───────────────────────────────────────────────

FIG_W = {"graph": 580, "circuit": 560, "diagram": 1100}
TEXT_W = 1100
PEN_RGB = {0: (0, 0, 0), 1: (128, 128, 128), 6: (0, 0, 230), 7: (220, 0, 0)}


def _style() -> dict:
    from ink import load_styles
    return dict(load_styles()["print"], size=44)


def render_block(b: Block, style: dict | None = None) -> list:
    """Strokes of one block at the origin, each Tinted with its pen."""
    from ink import Tinted
    if b.kind == "text":
        from ink import render
        return [Tinted(s, b.color) for s in render(b.src, style or _style(), 0, 0, TEXT_W)]
    if b.kind == "graph" and b.spec is not None:
        g, _ = plot_graph(b)
        if g is not None:
            try:
                g.x, g.y, g.max_w = 0, 0, FIG_W["graph"]
                return [Tinted(s, pen) for st, pen in graph_batches(g) for s in st]
            except Exception:
                pass                                   # -> the sampled pgfplots below
        from tex import tex_to_strokes
        return tex_to_strokes(tex_source(graph_tex(b.spec, b.label)), x=0, y=0,
                              max_w=FIG_W["graph"])
    if b.src.lstrip().startswith("<svg"):
        from svg import svg_to_strokes
        return [Tinted(s, b.color) for s in svg_to_strokes(b.src, 0, 0, FIG_W["diagram"])]
    from tex import tex_to_strokes
    return tex_to_strokes(tex_source(b.src, b.color if b.kind == "latex" else None),
                          x=0, y=0, max_w=FIG_W.get(b.kind, TEXT_W))


def _box(strokes) -> tuple[int, int, int, int]:
    xs = [x for s in strokes for x, _ in s]
    ys = [y for s in strokes for _, y in s]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def layout(blocks: list[Block], mask, floor: int = 0, bottom: int | None = None,
           style: dict | None = None, render=render_block) -> tuple[list[dict], list[dict]]:
    """Render every block and pack it into the page's empty space (mask =
    1872x1404 ink, True = busy), in reply order, figures two per row.
    -> (placed [{block, box, strokes, scale, why}], overflow [{kind, label, reason}])."""
    import place
    occ = place.occupancy(mask)
    drawn, overflow = [], []
    for b in blocks:
        try:
            st = [s for s in render(b, style) if s]
        except Exception as e:                         # a broken block: skipped, recorded
            overflow.append({"kind": b.kind, "label": b.label,
                             "reason": f"render failed: {str(e).splitlines()[0][:120]}"})
            continue
        if not st:
            overflow.append({"kind": b.kind, "label": b.label, "reason": "nothing to ink"})
            continue
        drawn.append((b, st))
    sizes = [(bx[2] - bx[0] + 2, bx[3] - bx[1] + 2) for bx in (_box(st) for _, st in drawn)]
    pair = [i > 0 and b.kind in VISUAL and drawn[i - 1][0].kind in VISUAL
            for i, (b, _) in enumerate(drawn)]
    spots = place.pack(sizes, occ, floor, bottom or place.H, pair)
    placed = []
    for (b, st), spot in zip(drawn, spots):
        if spot is None:
            overflow.append({"kind": b.kind, "label": b.label,
                             "reason": "no room on this page (stays in the log)"})
            continue
        moved = place.shift(st, spot.x, spot.y, spot.k)
        placed.append({"block": b, "box": _box(moved), "strokes": moved,
                       "scale": spot.k, "why": spot.why})
    return placed, overflow


def session_entries(placed: list[dict]) -> dict:
    """label -> {type, box, curves} for each placed graph: what the session
    (and the SPP page map) can name and target."""
    return {p["block"].label: {"type": "graph", "box": p["box"],
                               "curves": [list(c) for c in p["block"].curves]}
            for p in placed if p["block"].kind == "graph" and p["block"].label}


def preview(placed: list[dict], path, mask=None) -> None:
    """The page as it would ink: existing ink grey, strokes in pen colours."""
    from pathlib import Path

    import numpy as np
    from PIL import Image, ImageDraw
    a = np.full((1872, 1404, 3), 255, np.uint8)
    if mask is not None:
        a[np.asarray(mask, bool)] = (150, 150, 150)
    img = Image.fromarray(a)
    d = ImageDraw.Draw(img)
    for p in placed:
        for s in p["strokes"]:
            rgb = PEN_RGB.get(getattr(s, "color", 0), (0, 0, 0))
            if len(s) > 1:
                d.line([tuple(q) for q in s], fill=rgb, width=3)
            elif s:
                x, y = s[0]
                d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=rgb)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
