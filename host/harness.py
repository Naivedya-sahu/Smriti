"""
harness.py — the reading pipeline ("skills") + its eval.

Small vision models read far better when the job is split and the pixels are
prepared for them:

  layers()   context layers: FOCUS (newest strokes / a selection, cropped to 768px,
             dot-grid removed, scaled up) + OVERVIEW (whole visible screen,
             scaled down, focus boxed). Earlier turns ride in as text.
  read()     stage 1 — structured reading: {"kind","text","latex",
             "circuit","intent","summary"} as JSON. This is what the confirm
             window shows Navy (and what he may edit).
  respond()  stage 2 — text-only reply from the CONFIRMED reading, with a
             per-kind skill hint; every $$...$$ block is compiled and, on
             failure, repaired once by the model with the pdflatex error.

Eval (no tablet needed; needs the [ai] model up):
    uv run python host/harness.py eval            # host/evalpages/*.png
    uv run python host/harness.py read page.png   # one reading, JSON out
Each evalpages/<name>.png may carry <name>.json:
    {"kind": "math", "expect": ["V", "IR"]}   (keywords, case-insensitive)
"""

from __future__ import annotations

import base64
import io
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import skill

HERE = Path(__file__).resolve().parent
KINDS = ("text", "math", "circuit", "graph", "sketch", "mixed")

# ── skills: per-media hints for reading and replying ─────────────────────────
# The text lives in skill/smriti/ (kinds/<k>.md "## Read" / "## Reply",
# SKILL.md stage 1); these names are thin aliases of the loader (skill.py).
SKILLS = {k: skill.kind_hints(k) for k in KINDS}
AUTO_ASK = skill.auto_ask()

# Profiles (chosen in the Smriti app): the selected one DOMINATES reading and
# reply — it says what the page is about when the model can't tell, and
# picks the reply skill regardless of the detected kind. "auto" = neither.
# profile -> (stage-1 ask = that kind's "## Read", kind)
PROFILES = {"auto": ("", None),
            **{p: (SKILLS[k]["read"], k) for p, k in skill.profiles().items()}}

READ_SYSTEM = skill.read_system()

ACTIONS = {                      # selection-menu actions -> stage-2 goal
    "ask": "Reply to the selected content.",
    "plot": "Plot the selected function/data as a graph.",
    "draw": "Draw the selected circuit/diagram cleanly.",
    "solve": "Solve the selected problem step by step, compactly.",
    "write": "Rewrite the selected content neatly (prose or typeset maths).",
    "explain": "Explain the selected content briefly.",
}


# ── pixels ────────────────────────────────────────────────────────────────────

def clean(img: Image.Image, dark: int = 160, win: int = 9,
          min_px: int = 12, preserve_color: bool = False) -> Image.Image:
    """Binarize and drop the template dot grid: a dark pixel survives only if
    its win x win neighbourhood holds >= min_px dark pixels. Dots (~2-3 px)
    fail, pen strokes (long, 2-4 px wide) pass. Red overlay is kept.
    preserve_color retains saturated ink/highlights from an RGB screenshot."""
    rgb = np.asarray(img.convert("RGB"))
    red = (rgb[..., 0] > 150) & (rgb[..., 1] < 90) & (rgb[..., 2] < 90)
    ink = (rgb.mean(axis=2) < dark) | red
    if preserve_color:
        ink |= (np.ptp(rgb, axis=2) > 45) & (rgb.min(axis=2) < 200)
    c = np.pad(ink.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    r = win // 2
    h, w = ink.shape
    y0 = np.clip(np.arange(h) - r, 0, h)
    y1 = np.clip(np.arange(h) + r + 1, 0, h)
    x0 = np.clip(np.arange(w) - r, 0, w)
    x1 = np.clip(np.arange(w) + r + 1, 0, w)
    box = (c[y1][:, x1] - c[y0][:, x1] - c[y1][:, x0] + c[y0][:, x0])
    keep = ink & (box >= min_px)
    out = np.full((h, w, 3), 255, np.uint8)
    out[keep] = rgb[keep] if preserve_color else 0
    out[keep & red] = (220, 0, 0)
    return Image.fromarray(out)


def _fit(img: Image.Image, long_side: int, upscale: bool) -> Image.Image:
    s = long_side / max(img.size)
    if s > 1 and not upscale:
        return img
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))),
                      Image.LANCZOS)


def layers(page: Image.Image, box: tuple[int, int, int, int] | None,
           margin: int = 60, focus_px: int = 768,
           overview_px: int = 384, marks=None) -> tuple[Image.Image, Image.Image]:
    """(focus, overview). page = screenshot with the newest strokes already
    drawn red (monke._page_context). box = focus bbox in page px (newest
    strokes or a selection); None -> whole page is the focus."""
    page = page.convert("RGB")
    if box is None:
        box = (0, 0, page.width, page.height)
    x0, y0, x1, y1 = box
    fb = (max(0, x0 - margin), max(0, y0 - margin),
          min(page.width, x1 + margin), min(page.height, y1 + margin))
    if fb[2] - fb[0] < 20 or fb[3] - fb[1] < 20:
        # box (partly) off-screen — pen events past the panel edge crashed
        # the daemon here (device session 2026-09-26); read the whole page
        fb = (0, 0, page.width, page.height)
    focus = _fit(clean(page.crop(fb), preserve_color=True), focus_px, upscale=True)
    ov = clean(page, preserve_color=True)
    ImageDraw.Draw(ov).rectangle(fb, outline=(0, 0, 255), width=6)
    overview = _fit(ov, 512 if marks is not None else overview_px, upscale=False)
    if marks is not None:
        import som
        regions, free = marks
        marked = som.mark(overview, overview.height / page.height, regions, free)
        assert isinstance(marked, Image.Image)
        overview = marked
    return focus, overview


def strokes_box(strokes) -> tuple[int, int, int, int] | None:
    xs = [x for s in strokes for x, _ in s]
    ys = [y for s in strokes for _, y in s]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()


# ── stage 1: read ─────────────────────────────────────────────────────────────

def _images_msg(text: str, images: list[bytes]) -> dict:
    parts = [{"type": "text", "text": text}]
    for data in images:
        parts.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(data).decode()}})
    return {"role": "user", "content": parts}


def parse_reading(raw: str) -> dict:
    """Model output -> reading dict. Tolerates code fences / chatter; a
    non-JSON answer becomes a plain-text reading instead of an error."""
    # first complete JSON object: a greedy {.*} also swallowed trailing
    # prose with LaTeX braces -> unparsed, no confirm (device 2026-09-29)
    i = raw.find("{")
    try:
        r = json.JSONDecoder().raw_decode(raw[i:])[0] if i >= 0 else {}
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            r = json.loads(m.group(0)) if m else {}
        except ValueError:
            r = {}
    if not isinstance(r, dict) or not r:
        r = {"kind": "text", "text": raw.strip()}
    def flat(v):                       # models return lists for circuits
        return "; ".join(map(str, v)) if isinstance(v, list) else str(v or "")
    out = {k: flat(r.get(k, "")).strip()
           for k in ("kind", "text", "latex", "circuit", "intent", "summary",
                     "target", "target_kind", "target_desc")}
    out["kind"] = out["kind"] if out["kind"] in KINDS else "mixed"
    # SPP: the region the writing is about; F = the writing itself
    out["target"] = out["target"] or "F"
    out["target_kind"] = out["target_kind"] if out["target_kind"] in KINDS else ""
    return out


LAST_REQUEST: dict = {}


def read(focus: Image.Image, overview: Image.Image | None, chat,
         action: str | None = None, context: str = "",
         profile: str = "auto", page: str = "") -> dict:
    """chat = brain.chat-compatible callable(messages) -> str.
    page = the SPP <page> map (protocol.page_map), sent whole after the ask."""
    hint, pkind = PROFILES.get(profile, PROFILES["auto"])
    # auto = SHORT generic ask. A/B on real notes (2026-09-26, local 4B):
    # all five skill hints at once -> equations 0.77; short ask -> 0.84.
    # Circuit hints help circuits but hurt maths, so each PROFILE brings
    # only its own skill hint (`hint` = its kind module's "## Read").
    skills = "" if pkind else AUTO_ASK
    ask = ("Read the focus. " + (f"Selected for action '{action}'. " if action else "")
           + (f"PROFILE {profile.upper()} (dominant): {hint} " if hint else "")
           + skills +
           (f"\nEarlier in this notebook: {context[-600:]}" if context else "")
           + ("\nImage 2 tags are region ids in the <page> block."
              if page and overview is not None else "")
           + (f"\n{page}" if page else ""))
    imgs = [png(focus)] + ([png(overview)] if overview is not None else [])
    # the exact request, kept for the personal-model dataset: a training row
    # must be what the model saw at inference (research-finetune.md)
    LAST_REQUEST.clear()
    LAST_REQUEST.update(system=READ_SYSTEM, user=ask, n_images=len(imgs))
    raw = chat([{"role": "system", "content": READ_SYSTEM}, _images_msg(ask, imgs)])
    r = parse_reading(raw)
    if pkind and r["kind"] in ("mixed", "text") and pkind != "text":
        r["kind"] = pkind                  # the profile decides ambiguous pages
    return r


def read_and_reply(focus: Image.Image | None, overview: Image.Image | None, chat,
                   *, action: str | None = None, context: str = "", profile: str = "auto",
                   page: str = "", room: str = "", rules: str = "", page_log: str = "",
                   extra: str = "", on_reply=None, stop_after=None) -> tuple[dict, str]:
    """Run a merged turn. `chat` streams cumulative text to its callback."""
    hint, pkind = PROFILES.get(profile, PROFILES["auto"])
    skills = "" if pkind else AUTO_ASK
    ask = ("Read the focus. " + (f"Selected for action '{action}'. " if action else "")
           + (f"PROFILE {profile.upper()} (dominant): {hint} " if hint else "") + skills
           + (f"\nEarlier in this notebook: {context[-600:]}" if context else "")
           + ("\nImage 2 tags are region ids in the <page> block."
              if page and overview is not None else "")
           + (f"\n{page}" if page else "")
           + f"\nWriter facts: {room}{rules}"
           + (f"\n{page_log}" if page_log else ""))
    imgs = [] if focus is None else [png(focus)] + ([png(overview)] if overview is not None else [])
    kind = pkind
    system = skill.merged_system(kind) + (f"\n\nNavy's standing instructions (Smriti app):\n{extra}"
                                          if extra else "")
    LAST_REQUEST.clear()
    LAST_REQUEST.update(system=system, user=ask, n_images=len(imgs), mode="merged")
    state = {"reading": None, "reply": "", "end": 0}

    def split(text):
        i = text.find("{")
        if i < 0:
            return None
        try:
            obj, n = json.JSONDecoder().raw_decode(text[i:])
        except ValueError:
            return None
        return obj, i + n

    def feed(text):
        if state["reading"] is None:
            found = split(text)
            if found is None:
                return False
            _, state["end"] = found
            state["reading"] = parse_reading(text[:state["end"]])
            if pkind and state["reading"]["kind"] in ("mixed", "text") and pkind != "text":
                state["reading"]["kind"] = pkind
            if stop_after and stop_after(state["reading"]):
                return True
        tail = text[state["end"]:].lstrip("\r\n -")
        state["reply"] = tail
        if tail and on_reply:
            return bool(on_reply(tail))
        return False

    raw = chat([{"role": "system", "content": system}, _images_msg(ask, imgs)], on_text=feed)
    if state["reading"] is None:
        found = split(raw)
        if found is None:
            return parse_reading(raw), ""
        _, state["end"] = found
        state["reading"] = parse_reading(raw[:state["end"]])
        state["reply"] = raw[state["end"]:].lstrip("\r\n -")
    return state["reading"], state["reply"]


_PROMPT = re.compile(r"^\s*#\s*[Pp]\b\s*:?\s*")


def strip_prompt(reading: dict) -> tuple[bool, dict]:
    """'#P ...' at the start = an instruction to Monke (Navy 2026-09-30).
    Returns (is_prompt, reading without the marker)."""
    m = _PROMPT.match(reading.get("text", ""))
    if not m:
        return False, reading
    return True, dict(reading, text=reading["text"][m.end():])


def needs_confirm(reading: dict, action: str | None) -> bool:
    """Navy's rule (2026-09-26): confirm drawings + selections only."""
    return bool(action) or reading["kind"] in ("circuit", "graph", "sketch") \
        or reading["intent"] in ("solve", "plot", "draw", "refine")


def json_complete(text: str) -> bool:
    """True once text holds a complete JSON object (the reader can stop:
    run 2 streamed a whole solution + SVG after its JSON, 1078 tokens)."""
    i = text.find("{")
    if i < 0:
        return False
    try:
        json.JSONDecoder().raw_decode(text[i:])
        return True
    except ValueError:
        return False


def stream_units(text: str) -> tuple[list[str], int]:
    """Complete units of a reply still being streamed: each ends at a newline
    OUTSIDE $$..$$ and <svg>..</svg> (an inline $$x$$ stays with its line; a
    multi-line circuit waits for its closing $$). Returns (units, chars
    consumed); the rest is incomplete, wait for more text."""
    units, start, i, math, svg = [], 0, 0, False, False
    while i < len(text):
        if text.startswith("$$", i) and not svg:
            math, i = not math, i + 2
            continue
        if not math and text.startswith("<svg", i):
            svg = True
        elif svg and text.startswith("</svg>", i):
            svg, i = False, i + len("</svg>")
            continue
        if text[i] == "\n" and not math and not svg:
            if text[start:i].strip():
                units.append(text[start:i].strip())
            start = i + 1
        i += 1
    return units, start


def apply_edit(reading: dict, edited: str) -> dict:
    """Navy's confirm-window edit IS the reading: every model-derived content
    field goes, or stage 2 answers the stale summary (device 2026-09-29)."""
    # the popup shows describe(): 'text | maths: latex | circuit: ...' — split
    # it back, or the plotter can't parse text (run 13: model tikz, no graph
    # kept, the next 'add y = ...' drew a lone curve)
    f = {"text": "", "latex": "", "circuit": ""}
    parts = edited.split(" | ")
    f["text"] = parts[0].strip()
    for p in parts[1:]:
        k, _, v = p.partition(":")
        key = {"maths": "latex", "circuit": "circuit"}.get(k.strip())
        if key:
            f[key] = v.strip()
        else:
            f["text"] += " | " + p
    # range edited only in the maths part (Navy, run 13: [0,\,10] -> [0,\,15]):
    # carry it into the text the plotter reads
    if f["latex"] != reading.get("latex") and f["text"] == reading.get("text"):
        m = re.search(r"\[\s*(-?[\d.]+)\s*,(?:\\,)?\s*(-?[\d.]+)\s*\]", f["latex"])
        if m:
            f["text"] = re.sub(r"(x\s+(?:from|in)\s+\[?\s*)-?[\d.]+(\s*(?:to|,)\s*)-?[\d.]+",
                               lambda t: f"{t[1]}{m[1]}{t[2]}{m[2]}", f["text"])
    return dict(reading, **f, summary="")


def describe(reading: dict) -> str:
    """Reading as the text stage 2 (and the transcript) works from."""
    parts = [reading["text"]]
    if reading["latex"]:
        parts.append(f"maths: {reading['latex']}")
    if reading["circuit"]:
        parts.append(f"circuit: {reading['circuit']}")
    return " | ".join(p for p in parts if p)


# ── stage 2: respond + LaTeX repair ───────────────────────────────────────────

def reply_kind(reading: dict, profile: str = "auto") -> str:
    """Which kind module answers: the profile's, else the target's when
    refining a region (SPP), else the reading's own kind."""
    pkind = PROFILES.get(profile, PROFILES["auto"])[1]
    refine = reading.get("intent") == "refine" and reading.get("target_kind")
    return pkind or refine or reading["kind"]


def respond_prompt(reading: dict, action: str | None, room: str,
                   rules: str, profile: str = "auto") -> str:
    hint = SKILLS.get(reply_kind(reading, profile), SKILLS["mixed"])["reply"]
    goal = ACTIONS.get(action or "", "Reply.")
    return (f"Navy wrote ({reading['kind']}, confirmed reading): "
            f"{describe(reading)}\nHe wants: {reading['summary'] or reading['intent']}.\n"
            f"{goal} {hint} {room}{rules}")


def build_writer_prompt(reading: dict, action: str | None, room: str,
                        rules: str, profile: str = "auto", *, page: str = "",
                        page_log: str = "") -> str:
    """Build the complete stage-2 user prompt for daemon and offline replay."""
    prompt = respond_prompt(reading, action, room, rules, profile)
    if page_log:
        prompt += "\n\n" + page_log
    if page:
        prompt += "\n\n" + page
        target = reading.get("target", "F")
        if target != "F":
            line = next((ln.strip() for ln in page.splitlines()
                         if f'id="{target}"' in ln), "")
            prompt += (f"\nTarget region: {line}\nIt contains: {reading.get('target_desc', '')}"
                       if line else
                       f"\nTarget {target} is not on the page map: answer the writing itself.")
    return prompt


_BLOCK = re.compile(r"\$\$(.+?)\$\$", re.S)
_PROSE = re.compile(r"[A-Za-z][A-Za-z ,.!?'\-]*")


# Replies are inked with an ASCII stroke font: unicode symbols used to be
# DELETED ("fc = 1/(2πRC)" was inked as "1/(2 RC)" — device, 2026-09-26).
# Prose gets words, maths blocks get LaTeX commands.
_PROSE_SYM = {"π": "pi", "Ω": "ohm", "µ": "u", "μ": "u", "·": "*", "×": "x",
              "÷": "/", "≈": "~", "≤": "<=", "≥": ">=", "≠": "!=", "√": "sqrt",
              "°": " deg", "∞": "infinity", "→": "->", "←": "<-", "Δ": "delta",
              "ω": "omega", "θ": "theta", "α": "alpha", "β": "beta", "λ": "lambda",
              "τ": "tau", "φ": "phi", "σ": "sigma", "–": "-", "—": " - ", "’": "'",
              "‘": "'", "“": '"', "”": '"', "…": "...", "²": "^2", "³": "^3", "±": "+/-"}
_MATH_SYM = {"π": r"\pi ", "Ω": r"\Omega ", "µ": r"\mu ", "μ": r"\mu ", "·": r"\cdot ",
             "×": r"\times ", "÷": r"\div ", "≈": r"\approx ", "≤": r"\le ", "≥": r"\ge ",
             "≠": r"\ne ", "√": r"\sqrt ", "°": r"^\circ ", "∞": r"\infty ", "→": r"\to ",
             "Δ": r"\Delta ", "ω": r"\omega ", "θ": r"\theta ", "α": r"\alpha ",
             "β": r"\beta ", "λ": r"\lambda ", "τ": r"\tau ", "φ": r"\phi ",
             "σ": r"\sigma ", "±": r"\pm ", "²": "^2", "³": "^3", "–": "-", "—": "-"}


def to_ascii(text: str) -> str:
    """Unicode -> inkable ASCII: words in prose, LaTeX inside $$...$$."""
    parts = re.split(r"(\$\$.+?\$\$)", text, flags=re.S)
    out = []
    for i, p in enumerate(parts):
        table = _MATH_SYM if i % 2 else _PROSE_SYM
        out.append("".join(table.get(ch, ch) for ch in p))
    return "".join(out).encode("ascii", "ignore").decode()


def clean_reply(text: str) -> str:
    """Small models wrap plain prose in $$\\text{...}$$ (seen on device:
    greeting '$$\\text{Sketching circuits? Im ready.}$$'). Such a block
    would be typeset as a formula; unwrap it back to handwriting prose."""
    def unwrap(m: re.Match) -> str:
        body = m.group(1).strip()
        t = re.fullmatch(r"\\text\s*\{([^{}]*)\}", body)
        if t:
            return t.group(1)
        return body if _PROSE.fullmatch(body) and " " in body else m.group(0)
    return _BLOCK.sub(unwrap, text)


def greeting_line(text: str, max_words: int = 14) -> str:
    """The greeting is ONE short prose line, whatever the model sent: blocks
    dropped, first non-empty line, first sentence, word cap. Live test
    2026-09-30: a paragraph + tikz plot (329 strokes) for 'ONE short line'."""
    text = re.sub(r"\$\$.*?\$\$", "\n", text, flags=re.S)
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.match(r".*?[.!?](?=\s|$)|.*", line).group(0)
    return " ".join(line.split()[:max_words])


def repair_latex(reply: str, chat, compile_fn=None) -> str:
    """Compile every $$...$$ block; a failing block gets ONE model repair
    with the pdflatex error. Still failing -> left as is (the renderer then
    inks a short note, never source)."""
    if compile_fn is None:
        from tex import tex_to_image as compile_fn
    from tex import block_src

    def fix(m: re.Match) -> str:
        body = m.group(1).strip()
        try:
            compile_fn(block_src(body))
            return m.group(0)
        except OSError:
            return m.group(0)          # no pdflatex on this box: can't judge
        except Exception as e:
            err = str(e)[-400:]
        raw = chat([{"role": "user", "content":
                     "This LaTeX (compiled in a standalone doc with amsmath, "
                     "circuitikz, pgfplots) fails:\n" + body +
                     "\nError:\n" + err +
                     "\nReturn ONLY the corrected code, no $$, no fence."}])
        fixed = re.sub(r"^```\w*|```$", "", raw.strip()).strip().strip("$").strip()
        try:
            compile_fn(block_src(fixed))
            print("[harness] latex repaired", flush=True)
            return f"$${fixed}$$"
        except Exception:
            return m.group(0)

    return _BLOCK.sub(fix, reply)


# ── eval ──────────────────────────────────────────────────────────────────────

def _eval(folder: Path) -> None:
    sys.path.insert(0, str(HERE))
    from brain import chat, load_config
    pages = sorted(folder.glob("*.png"))
    if not pages:
        sys.exit(f"no eval pages in {folder}")
    score = kind_ok = 0
    for p in pages:
        exp = {}
        if p.with_suffix(".json").exists():
            exp = json.loads(p.with_suffix(".json").read_text())
        img = Image.open(p)
        focus, ov = layers(img, None)
        t0 = time.time()
        r = read(focus, ov, chat)
        dt = time.time() - t0
        blob = json.dumps(r).lower()
        want = exp.get("expect", [])
        hits = sum(w.lower() in blob for w in want)
        k = exp.get("kind")
        kind_ok += (k is None or k == r["kind"])
        score += hits / len(want) if want else 1
        print(f"{p.name:28s} {dt:5.1f}s kind={r['kind']:<8s}"
              f"{'' if k is None else ' (want ' + k + ')'}"
              f" keys {hits}/{len(want)}  | {describe(r)[:90]}")
    n = len(pages)
    print(f"\nkeywords {score / n:.0%}   kind {kind_ok}/{n}   model {load_config()['model']}")


def _selfcheck() -> None:
    """Offline logic checks: dot removal, JSON parsing, repair loop."""
    img = Image.new("RGB", (200, 120), "white")
    d = ImageDraw.Draw(img)
    for x in range(5, 200, 25):
        for y in range(5, 120, 25):
            d.rectangle((x, y, x + 2, y + 2), fill="black")      # grid dots
    d.line((20, 60, 180, 60), fill="black", width=3)             # a stroke
    d.line((20, 90, 180, 90), fill=(220, 0, 0), width=3)         # new stroke
    a = np.asarray(clean(img))
    assert a[6, 6].tolist() == [255, 255, 255], "grid dot survived"
    assert a[60, 100].tolist() == [0, 0, 0], "stroke lost"
    assert a[90, 100].tolist() == [220, 0, 0], "red stroke lost"
    r = parse_reading('```json\n{"kind":"math","latex":"V=IR","intent":"solve"}\n```')
    assert r["kind"] == "math" and r["latex"] == "V=IR"
    assert parse_reading("just words")["text"] == "just words"
    assert needs_confirm(r, None) and not needs_confirm(
        parse_reading('{"kind":"text","intent":"note"}'), None)

    def fake_compile(s):
        if "bad" in s:
            raise RuntimeError("! Undefined control sequence")
    out = repair_latex("hi $$\\bad x$$ and $$x^2$$",
                       lambda m: "x+1", fake_compile)
    assert out == "hi $$x+1$$ and $$x^2$$", out
    f, o = layers(img, (20, 55, 180, 95))
    assert max(f.size) == 768 and max(o.size) <= 384
    print("harness selfcheck ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["selfcheck"]:
        _selfcheck()
    elif a[:1] == ["eval"]:
        _eval(Path(a[1]) if len(a) > 1 else HERE / "evalpages")
    elif a[:1] == ["read"] and len(a) > 1:
        sys.path.insert(0, str(HERE))
        from brain import chat
        img = Image.open(a[1])
        f, o = layers(img, None)
        print(json.dumps(read(f, o, chat), indent=1))
    else:
        sys.exit(__doc__)
