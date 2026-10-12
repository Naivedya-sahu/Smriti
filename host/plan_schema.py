"""
plan_schema.py — Smriti render-plan contract v1: the JSON twin of the text
grammar (skill/smriti/SKILL.md, Stage 2 "Plan mode").

    SCHEMA / FORMAT        strict-safe JSON Schema; FORMAT = the Responses API
                           text.format object brain._direct sends (byte-stable)
    SWITCH                 the one line appended to the writer's system prompt
    validate(obj, taken)   -> (blocks, problems)  deterministic canonicalizer:
                           coerce / trim / clamp, per-block fallback, never raises
    block(raw, taken)      -> (blocks, problems)  one plan element (plan_stream)
    header(obj)            -> {"place": str|None, "outline": {...}|None}
    is_plan(text) / as_text(raw, taken)  a JSON reply -> the text grammar
                           (log, history, the whole-reply path)

Strict rules (OpenAI Structured Outputs): object root, every key required,
additionalProperties false, nullable = type [..., "null"], anyOf only below
the root. The model picks ROLES, never pen enums: render_plan.ROLE_COLOR maps
them. Graph blocks carry the shared GraphSpec (host/plots.py, Lane C) as
`plot`, verbatim. No model repair: a block that fails canonicalization falls
back deterministically (graph -> its expressions as prose) or is dropped
with a reason.
"""

from __future__ import annotations

import json
import math
import re

ROLES = ("body", "answer", "highlight")
TYPES = ("text", "latex", "circuit", "graph", "diagram")
PLOT_TYPES = ("function", "stem", "pole_zero", "freq_response")
MAX_BLOCKS, MAX_TEXT, MAX_TEX, MAX_CURVES = 16, 600, 4000, 4
MAX_VALUES, MAX_PZ, MAX_COEF, MAX_MARKS, MAX_STEPS = 256, 32, 32, 8, 6


def _o(**props) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": list(props), "properties": props}


def _one(v: str) -> dict:
    return {"type": "string", "enum": [v]}


def _null(*vals) -> dict:
    return {"type": ["string", "null"], "enum": [*vals, None]}


_S0 = {"type": ["string", "null"]}
_NUM = {"type": "number"}
_NUMS = {"type": ["array", "null"], "items": _NUM}
_PAIRS = {"type": ["array", "null"], "items": {"type": "array", "items": _NUM},
          "description": "[[re, im], ...]"}

# the shared GraphSpec (Lane C, host/plots.py) — key order = emission order
PLOT = _o(
    type={"type": "string", "enum": list(PLOT_TYPES)},
    title=_S0, xlabel=_S0, ylabel=_S0,
    x={**_NUMS, "description": "[min, max] of the horizontal axis"},
    y={**_NUMS, "description": "[min, max], null = auto"},
    curves={"type": ["array", "null"], "items": _o(
        expr={"type": "string", "description": "real numpy-like expression in t (or x; n for "
              "stem): + - * / ** ( ), sin cos exp abs sqrt pi u(t) rect(t) tri(t) sinc(t)"},
        label=_S0)},
    n={**_NUMS, "description": "stem: [nmin, nmax]"},
    values={**_NUMS, "description": "stem: x[n] over n"},
    zeros=_PAIRS, poles=_PAIRS,
    b={**_NUMS, "description": "freq_response numerator coefficients"},
    a={**_NUMS, "description": "freq_response denominator coefficients"},
    domain=_null("z", "s"), scale=_null("linear", "db"), show=_null("mag", "phase"),
    marks={"type": ["array", "null"], "items": _o(x=_NUM, label=_S0),
           "description": "vertical marker lines, e.g. a resonance"})

_ROLE = {"type": "string", "enum": list(ROLES)}
_TEX = {"type": "string", "description": "LaTeX body, no $$ delimiters"}
TEXT = _o(type=_one("text"), role=_ROLE,
          text={"type": "string", "description": "one short ASCII prose line, no LaTeX"})
LATEX = _o(type=_one("latex"), role=_ROLE, tex=_TEX)
CIRCUIT = _o(type=_one("circuit"),
             tex={"type": "string", "description": "\\begin{circuitikz}...\\end{circuitikz}"})
GRAPH = _o(type=_one("graph"), id={**_S0, "description": "G1, G2 ... or null"}, plot=PLOT)
DIAGRAM = _o(type=_one("diagram"), title=_S0,
             steps={"type": ["array", "null"], "items": {"type": "string"},
                    "description": "flow boxes in order (drawn left to right)"},
             svg={**_S0, "description": "a whole <svg> document, or null to draw the steps"})

SCHEMA = _o(
    v={"type": "integer", "enum": [1]},
    outline=_o(text_lines={"type": "integer"},
               figures={"type": "array",
                        "items": {"type": "string", "enum": ["latex", "circuit", "graph",
                                                             "diagram"]}}),
    place={**_S0, "description": "SPP placement, e.g. 'below N2', or null"},
    blocks={"type": "array", "items": {"anyOf": [TEXT, LATEX, CIRCUIT, GRAPH, DIAGRAM]}})

NAME = "smriti_plan"
FORMAT = {"type": "json_schema", "name": NAME, "strict": True, "schema": SCHEMA}
SWITCH = ("Plan mode is ON: reply with the smriti_plan JSON (Stage 2, Plan mode) - "
          "the same content as the text grammar, one block per line/equation/drawing.")


# ── coercion helpers (pure, never raise) ─────────────────────────────────────

def _str(v, cap: int) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    return str(v).strip()[:cap]


def _str0(v, cap: int = 80) -> str | None:
    return _str(v, cap) or None


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v.strip()) if isinstance(v, str) else float(v)
    except (TypeError, ValueError, AttributeError):
        return None
    return f if math.isfinite(f) else None


def _nums(v, cap: int) -> list | None:
    if isinstance(v, str):
        v = re.split(r"[\s,;]+", v.strip(" []()"))
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return None
    out = [f for f in (_num(x) for x in v[:cap]) if f is not None]
    return out or None


def _range(v) -> list | None:
    if isinstance(v, dict):
        v = [v.get("from", v.get("min")), v.get("to", v.get("max"))]
    got = _nums(v, 4)
    if not got or len(got) < 2:
        return None
    lo, hi = sorted(got[:2])
    return [lo, hi] if hi > lo else None


def _pairs(v) -> list | None:
    if not isinstance(v, (list, tuple)):
        return None
    out = []
    for p in v[:MAX_PZ]:
        if isinstance(p, dict):
            p = [p.get("re", p.get("real")), p.get("im", p.get("imag", 0))]
        if isinstance(p, (list, tuple)) and p:
            re_, im = _num(p[0]), _num(p[1]) if len(p) > 1 else 0.0
        else:
            re_, im = _num(p), 0.0
        if re_ is not None and im is not None:
            out.append([re_, im])
    return out or None


def _enum(v, allowed, alias: dict | None = None) -> str | None:
    if not isinstance(v, str):
        return None
    k = v.strip().lower().replace("-", "_").replace(" ", "_")
    k = (alias or {}).get(k, k)
    return k if k in allowed else None


_TYPE_ALIAS = {"prose": "text", "paragraph": "text", "line": "text", "note": "text",
               "equation": "latex", "math": "latex", "maths": "latex", "tex": "latex",
               "formula": "latex", "plot": "graph", "chart": "graph", "schematic": "circuit",
               "circuitikz": "circuit", "svg": "diagram", "flow": "diagram",
               "flowchart": "diagram", "block_diagram": "diagram"}
_PLOT_ALIAS = {"time": "function", "fn": "function", "func": "function",
               "continuous": "function", "line": "function", "discrete": "stem",
               "sequence": "stem", "pz": "pole_zero", "polezero": "pole_zero",
               "pole_zero_plot": "pole_zero", "poles_zeros": "pole_zero",
               "bode": "freq_response", "frequency_response": "freq_response",
               "freq": "freq_response", "magnitude": "freq_response"}
_ROLE_ALIAS = {"result": "answer", "final": "answer", "final_answer": "answer",
               "prose": "body", "equation": "body", "note": "body", "step": "body",
               "figure": "body", "emphasis": "highlight", "key": "highlight"}


def _role(kind: str, raw) -> str:
    """plan role -> render_plan role (the one place colour comes from)."""
    if kind in ("graph", "circuit", "diagram"):
        return kind
    r = _enum(raw, ROLES, _ROLE_ALIAS) or "body"
    if r != "body":
        return r
    return "prose" if kind == "text" else "equation"


def _unwrap(tex: str) -> str:
    tex = tex.strip()
    for a, b in (("$$", "$$"), ("\\[", "\\]"), ("$", "$")):
        if tex.startswith(a) and tex.endswith(b) and len(tex) > len(a) + len(b):
            tex = tex[len(a):-len(b)].strip()
    return tex


def _expr(v) -> str:
    s = _str(v, 300).replace("$", "").replace("^", "**").replace("\u2212", "-")
    s = re.sub(r"\\(?=[a-zA-Z])", "", s)                 # \sin -> sin, \pi -> pi
    m = re.match(r"^\s*[A-Za-z_|][\w|()\[\],. ]{0,24}?=(?!=)\s*", s)
    return (s[m.end():] if m else s).strip()


# ── the GraphSpec canonicalizer ──────────────────────────────────────────────

def plot(raw) -> tuple[dict | None, list[str]]:
    """Any model plot -> the exact GraphSpec dict (every key present), or
    (None, why) when the type's data is missing."""
    if not isinstance(raw, dict):
        return None, ["plot is not an object"]
    why = []
    kind = _enum(raw.get("type"), PLOT_TYPES, _PLOT_ALIAS)
    curves = []
    for c in (raw.get("curves") or [])[:8] if isinstance(raw.get("curves"), list) else []:
        e, lab = (c.get("expr"), c.get("label")) if isinstance(c, dict) else (c, None)
        if (e := _expr(e)):
            curves.append({"expr": e, "label": _str0(lab, 40)})
    if len(curves) > MAX_CURVES:
        why.append(f"{len(curves)} curves -> first {MAX_CURVES}")
        curves = curves[:MAX_CURVES]
    marks = []
    for m in (raw.get("marks") or [])[:MAX_MARKS] if isinstance(raw.get("marks"), list) else []:
        x = _num(m.get("x")) if isinstance(m, dict) else _num(m)
        if x is not None:
            marks.append({"x": x, "label": _str0(m.get("label"), 30) if isinstance(m, dict)
                          else None})
    spec = {"type": kind, "title": _str0(raw.get("title")),
            "xlabel": _str0(raw.get("xlabel"), 40), "ylabel": _str0(raw.get("ylabel"), 40),
            "x": _range(raw.get("x")), "y": _range(raw.get("y")), "curves": curves or None,
            "n": _range(raw.get("n")), "values": _nums(raw.get("values"), MAX_VALUES),
            "zeros": _pairs(raw.get("zeros")), "poles": _pairs(raw.get("poles")),
            "b": _nums(raw.get("b"), MAX_COEF), "a": _nums(raw.get("a"), MAX_COEF),
            "domain": _enum(raw.get("domain"), ("z", "s")),
            "scale": _enum(raw.get("scale"), ("linear", "db"), {"log": "db", "decibel": "db"}),
            "show": _enum(raw.get("show"), ("mag", "phase"),
                          {"magnitude": "mag", "angle": "phase"}),
            "marks": marks or None}
    if spec["n"]:
        spec["n"] = [math.floor(spec["n"][0]), math.ceil(spec["n"][1])]
    if kind is None:                       # infer from the data the model did send
        kind = ("pole_zero" if spec["zeros"] or spec["poles"] else
                "freq_response" if spec["b"] else
                "stem" if spec["values"] else "function" if curves else None)
        if kind is None:
            return None, why + [f"plot type {raw.get('type')!r} with no data"]
        why.append(f"plot type {raw.get('type')!r} -> {kind}")
        spec["type"] = kind
    need = {"function": spec["curves"], "stem": spec["values"] or spec["curves"],
            "pole_zero": spec["zeros"] or spec["poles"], "freq_response": spec["b"]}[kind]
    if not need:
        return None, why + [f"{kind} plot without its data"]
    if kind == "freq_response" and not spec["a"]:
        spec["a"] = [1.0]
    return spec, why


# ── one block ────────────────────────────────────────────────────────────────

def _label(want, taken: set) -> str:
    want = _str(want, 8).upper().replace(" ", "")
    if re.fullmatch(r"G[1-9]\d?", want) and want not in taken:
        taken.add(want)
        return want
    n = 1
    while f"G{n}" in taken:
        n += 1
    taken.add(f"G{n}")
    return f"G{n}"


def block(raw, taken: set | None = None) -> tuple[list, list[str]]:
    """One plan element -> ([render_plan.Block], problems). 0..n blocks: a
    text field holding $$/<svg> splits through render_plan.parse; a broken
    graph falls back to its expressions as prose; unknown -> dropped."""
    import render_plan as rp
    taken = set() if taken is None else taken
    if isinstance(raw, str):
        raw = {"type": "text", "text": raw}
    if not isinstance(raw, dict):
        return [], [f"block is {type(raw).__name__}, not an object -> dropped"]
    why: list[str] = []
    kind = _enum(raw.get("type") or raw.get("kind"), TYPES, _TYPE_ALIAS)
    if kind is None:                       # infer from the fields present
        kind = next((k for k, f in (("graph", "plot"), ("circuit", "circuit"),
                                    ("diagram", "svg"), ("diagram", "steps"),
                                    ("latex", "tex"), ("latex", "latex"), ("text", "text"))
                     if raw.get(f)), None)
        if kind is None:
            return [], [f"unknown block type {raw.get('type')!r} -> dropped"]
        why.append(f"type {raw.get('type')!r} -> {kind}")

    def mk(k, role, src, **kw):
        return rp.Block(k, role, rp.ROLE_COLOR[role], src, reason="; ".join(why), **kw)

    if kind == "text":
        text = _str(raw.get("text") if raw.get("text") is not None else raw.get("tex"),
                    MAX_TEXT * 2)
        text = re.sub(r"^#+\s*|\*\*|__|`", "", text, flags=re.M).strip()
        if len(text) > MAX_TEXT:
            why.append(f"text cut to {MAX_TEXT} chars")
            text = text[:MAX_TEXT].rsplit(" ", 1)[0]
        if not text:
            return [], why + ["empty text -> dropped"]
        role = _role("text", raw.get("role"))
        if "$$" in text or "<svg" in text or "\\begin{" in text:
            got, skipped = rp.parse(text, taken)
            taken.update(b.label for b in got if b.label)
            why.append("text held blocks -> split")
            out = [b if b.kind in rp.VISUAL or role in ("prose", "equation") else
                   rp.Block(b.kind, role, rp.ROLE_COLOR[role], b.src, b.label, b.curves,
                            "; ".join(why)) for b in got]
            return out, why + [f"skipped {s['reason']}" for s in skipped]
        return [mk("text", role, text)], why

    if kind in ("latex", "circuit"):
        tex = _unwrap(_str(raw.get("tex") or raw.get("latex") or raw.get("text"), MAX_TEX + 1))
        if len(tex) > MAX_TEX:
            return [], why + [f"{kind} source over {MAX_TEX} chars -> dropped"]
        if not tex:
            return [], why + [f"empty {kind} -> dropped"]
        sub = rp._kind(tex)
        if kind == "circuit" or sub == "circuit":
            if "\\begin{" not in tex:
                if "\\draw" not in tex:
                    return [], why + ["circuit without \\draw -> dropped (never ink source)"]
                tex = "\\begin{circuitikz}" + tex + "\\end{circuitikz}"
                why.append("wrapped in circuitikz")
            return [mk("circuit", "circuit", tex)], why
        if sub == "graph":                 # model pgfplots: the text path's restyle
            label = _label(None, taken)
            src, curves, w = rp._restyle(tex, label)
            return [mk("graph", "graph", src, label=label, curves=curves)], why + w
        if sub == "diagram":
            return [mk("diagram", "diagram", tex)], why
        role = "answer" if "\\boxed" in tex and raw.get("role") != "highlight" else \
            _role("latex", raw.get("role"))
        return [mk("latex", role, tex)], why

    if kind == "diagram":
        svg = _str(raw.get("svg"), 20000)
        m = re.search(r"<svg\b.*?</svg>", svg, re.S | re.I)
        if m:
            return [mk("diagram", "diagram", m.group(0))], why
        steps = raw.get("steps") or raw.get("nodes")
        steps = [(str(s.get("label") or s.get("text") or "") if isinstance(s, dict) else str(s))
                 for s in steps] if isinstance(steps, list) else []
        if svg:
            why.append("unclosed <svg> -> boxes from steps")
        if not any(s.strip() for s in steps):
            return [], why + ["diagram without svg or steps -> dropped"]
        return [mk("diagram", "diagram",
                   rp.fallback_svg(_str(raw.get("title"), 60) or "Diagram", steps))], why

    src_plot = raw.get("plot") if isinstance(raw.get("plot"), dict) else raw
    spec, w = plot(src_plot)
    why += w
    label = _label(raw.get("id") or raw.get("label"), taken)
    if spec is None:                       # deterministic fallback: say it, don't draw it
        found = src_plot.get("curves") if isinstance(src_plot.get("curves"), list) else []
        exprs = [e for e in (_expr(c.get("expr") if isinstance(c, dict) else c)
                             for c in found) if e]
        taken.discard(label)
        if not exprs:
            return [], why + ["graph without data -> dropped"]
        return [mk("text", "prose", f"{label}: " + "; ".join(exprs)[:MAX_TEXT])], \
            why + ["graph -> its expressions as prose"]
    n = len(spec["curves"] or ()) or 1
    curves = tuple(rp.CURVES[i % len(rp.CURVES)] for i in range(n))
    src = json.dumps(spec, separators=(",", ":"))
    return [mk("graph", "graph", src, label=label, curves=curves, spec=spec)], why


# ── the whole plan ───────────────────────────────────────────────────────────

def loads(text) -> object | None:
    """The plan in a reply: a JSON object/array (a ```json fence is
    tolerated, trailing junk ignored). None = not JSON (text grammar)."""
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
    i = min((k for k in (s.find("{"), s.find("[")) if k >= 0), default=-1)
    if i < 0 or s[:i].strip():
        return None
    try:
        return json.JSONDecoder().raw_decode(s[i:])[0]
    except ValueError:
        return None


def is_plan(text) -> bool:
    """A reply that opens as a JSON object (after an optional ```json fence)
    is a plan; the text grammar never starts with '{' (render_plan's
    json-steps replies still parse: no 'blocks' key -> text path)."""
    s = (text or "").lstrip()
    if s.startswith("```"):
        s = s[3:].lstrip("json").lstrip() if "\n" in s else ""
    return s.startswith("{")


def header(obj) -> dict:
    obj = loads(obj) if isinstance(obj, str) else obj
    if not isinstance(obj, dict):
        return {"place": None, "outline": None}
    place = _str0(obj.get("place"), 60)
    place = re.sub(r"^@?place\s+", "", place, flags=re.I) if place else None
    o = obj.get("outline")
    outline = None
    if isinstance(o, dict):
        figs = o.get("figures") if isinstance(o.get("figures"), list) else []
        tl = _num(o.get("text_lines"))
        outline = {"text_lines": max(0, min(40, int(tl or 0))),
                   "figures": [f for f in (_enum(f, TYPES[1:], _TYPE_ALIAS) for f in figs[:12])
                               if f]}
    return {"place": place or None, "outline": outline}


def validate(obj, taken=()) -> tuple[list, list[str]]:
    """A plan (dict, bare block list, or JSON text) -> (Blocks in order,
    problems). Deterministic; never raises; caps at MAX_BLOCKS."""
    try:
        obj = loads(obj)
        problems: list[str] = []
        if isinstance(obj, dict):
            if obj.get("v", 1) != 1:
                problems.append(f"plan v={obj.get('v')!r}, read as v1")
            items = obj.get("blocks")
            if not isinstance(items, list):
                one = obj if any(k in obj for k in ("type", "text", "tex", "plot")) else None
                if one is None:
                    return [], problems + ["no blocks"]
                items = [one]
        elif isinstance(obj, list):
            items = obj
        else:
            return [], ["not a JSON plan"]
        if len(items) > MAX_BLOCKS:
            problems.append(f"{len(items)} blocks -> first {MAX_BLOCKS}")
            items = items[:MAX_BLOCKS]
        out, seen = [], set(taken or ())
        for i, raw in enumerate(items):
            try:
                got, why = block(raw, seen)
            except Exception as e:         # canonicalizer bug: drop the block, keep the plan
                got, why = [], [f"canonicalizer: {type(e).__name__}: {str(e)[:60]}"]
            out += got
            problems += [f"blocks[{i}]: {w}" for w in why]
        return out, problems
    except Exception as e:
        return [], [f"validate: {type(e).__name__}: {str(e)[:80]}"]


# ── back to the text grammar ─────────────────────────────────────────────────

def block_text(b) -> str:
    """One Block in the text grammar: prose, $$ body $$, or <svg>."""
    import render_plan as rp
    if b.kind == "text" or b.kind == "diagram":
        return b.src
    if b.kind == "graph" and getattr(b, "spec", None) is not None:
        try:
            return "$$" + rp.graph_tex(b.spec, b.label) + "$$"
        except Exception:
            exprs = "; ".join(c["expr"] for c in b.spec.get("curves") or ())
            return f"{b.label}: {exprs}" if exprs else ""
    return "$$" + b.src + "$$"


def to_text(blocks, place: str | None = None) -> str:
    lines = [f"@place {place}"] if place else []
    return "\n".join(lines + [t for t in (block_text(b) for b in blocks) if t])


def as_text(raw: str, taken=()) -> str:
    """A JSON plan reply -> the same reply in the text grammar; any other
    reply unchanged (a JSON object with no blocks stays text: render_plan
    still reads json steps)."""
    if not is_plan(raw):
        return raw
    obj = loads(raw)
    if not isinstance(obj, dict) or not isinstance(obj.get("blocks"), list):
        return raw
    blocks, _ = validate(obj, taken)
    return to_text(blocks, header(obj)["place"])
