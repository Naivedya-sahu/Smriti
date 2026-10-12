"""Offline Smriti reader/writer replay. No tablet, eye, ledger, or session I/O."""
# pyright: reportAttributeAccessIssue=false
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

import brain
import harness
import skill

ROOM = ("About 1500px of empty space is visible below the writing (~16 handwritten lines). "
        "Keep prose short (at most 4 lines); up to 3 small equations or drawings when asked for. ")
RULES = ("Maths, circuits and graphs only inside $$...$$ (typeset onto the paper, never show "
         "LaTeX source in prose); freeform sketches as one <svg> block.")


def parse_box(value: str) -> tuple[int, int, int, int]:
    try:
        box = tuple(int(float(x)) for x in value.split(","))
        if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError
        return box
    except ValueError:
        raise argparse.ArgumentTypeError("box must be x0,y0,x1,y1 with positive area") from None


def prepare_images(path: Path, box=None):
    page = Image.open(path).convert("RGB")
    if box:
        rgb = __import__("numpy").asarray(page).copy()
        x0, y0, x1, y1 = box
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(page.width, x1), min(page.height, y1)
        crop = rgb[y0:y1, x0:x1]
        dark = crop.min(axis=2) < 180
        crop[dark] = (220, 0, 0)
        page = Image.fromarray(rgb)
    return harness.layers(page, box)


def _expect(path: Path) -> dict:
    sidecar = path.with_suffix(".json")
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _usage() -> dict:
    u = brain.last_usage()
    return {"in": int(u.get("in", 0)), "out": int(u.get("out", 0))}


def run_case(path: Path, *, chat=brain.chat, box=None, profile="auto", variant="default",
             mode="split", model=None, no_write=False, repeat=1) -> dict:
    focus, overview = prepare_images(path, box)
    call = lambda messages: chat(messages, model=model, role="replay")
    t0 = time.perf_counter()
    if mode == "merged" and not no_write:
        merged_call = lambda messages, on_text=None: chat(
            messages, model=model, role="replay", on_text=on_text)
        reading, reply = harness.read_and_reply(
            focus, overview, merged_call, profile=profile, room=ROOM, rules=RULES)
        ms_read = round((time.perf_counter() - t0) * 1000)
        ms_write = 0
        read_usage = _usage() if chat is brain.chat else {"in": 0, "out": 0}
        write_usage = {"in": 0, "out": 0}
    else:
        reading = harness.read(focus, overview, call, profile=profile)
        ms_read = round((time.perf_counter() - t0) * 1000)
        read_usage = _usage() if chat is brain.chat else {"in": 0, "out": 0}
        reply, ms_write, write_usage = "", 0, {"in": 0, "out": 0}
    if not no_write and mode == "split":
        prompt = harness.build_writer_prompt(reading, None, ROOM, RULES, profile)
        messages = [{"role": "system", "content": skill.reply_system(harness.reply_kind(reading, profile))},
                    {"role": "user", "content": prompt}]
        t0 = time.perf_counter()
        reply = chat(messages, model=model, role="replay")
        ms_write = round((time.perf_counter() - t0) * 1000)
        write_usage = _usage() if chat is brain.chat else {"in": 0, "out": 0}
    exp = _expect(path)
    reading_blob = json.dumps(reading, ensure_ascii=False).lower()
    wants = exp.get("expect", [])
    reply_wants = exp.get("reply_expect", [])
    hits = sum(str(w).lower() in reading_blob for w in wants)
    hits += sum(str(w).lower() in reply.lower() for w in reply_wants)
    possible = len(wants) + len(reply_wants)
    words = len(reply.split())
    directives = [ln for ln in reply.splitlines() if ln.lstrip().startswith("@")]
    return {"page": str(path), "variant": variant, "mode": mode, "repeat": repeat,
            "reading": reading, "reply": reply, "ms_read": ms_read, "ms_write": ms_write,
            "tokens_in": read_usage["in"] + write_usage["in"],
            "tokens_out": read_usage["out"] + write_usage["out"],
            "kind_ok": exp.get("kind") in (None, reading["kind"]),
            "keyword_hits": hits / possible if possible else 1.0,
            "reply_words": words,
            "word_cap_violation": bool(exp.get("max_words") and words > exp["max_words"]),
            "directives": directives,
            "non_ascii_drops": sum(ord(ch) > 127 for ch in reply)}


def _p90(values):
    values = sorted(values)
    return values[max(0, math.ceil(0.9 * len(values)) - 1)] if values else 0


def summary_markdown(rows: list[dict]) -> str:
    lines = ["# Smriti replay", "", "| Variant | Mode | Runs | Mean ms | P90 ms | Tokens in/out | Kind | Keywords | Word-cap | Non-ASCII |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    keys = sorted({(r["variant"], r["mode"]) for r in rows})
    for variant, mode in keys:
        rs = [r for r in rows if (r["variant"], r["mode"]) == (variant, mode)]
        lat = [r["ms_read"] + r["ms_write"] for r in rs]
        mean = round(statistics.fmean(lat)) if lat else 0
        kind = sum(r["kind_ok"] for r in rs) / len(rs)
        keywords = statistics.fmean(r["keyword_hits"] for r in rs)
        lines.append(f"| {variant} | {mode} | {len(rs)} | {mean} | {_p90(lat)} | "
                     f"{sum(r['tokens_in'] for r in rs)}/{sum(r['tokens_out'] for r in rs)} | "
                     f"{kind:.0%} | {keywords:.0%} | {sum(r['word_cap_violation'] for r in rs)} | "
                     f"{sum(r['non_ascii_drops'] for r in rs)} |")
    return "\n".join(lines) + "\n"


def _fake_chat(path: str):
    spec = importlib.util.spec_from_file_location("smriti_fake_brain", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load fake brain: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.chat


def _render(path: Path, reply: str, out: Path, variant: str):
    import monke
    from write import load_styles
    page = Image.open(path).convert("RGB")
    strokes = monke._layout(reply, load_styles()["cursive"], page.height + 60)
    h = max((y for s in strokes for _, y in s), default=page.height) + 40
    canvas = Image.new("RGB", (max(1404, page.width), h), "white")
    canvas.paste(page, (0, 0))
    draw = ImageDraw.Draw(canvas)
    for stroke in strokes:
        if len(stroke) > 1:
            draw.line(stroke, fill="black", width=3)
    canvas.save(out / f"{path.stem}.{variant}.png")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pages", nargs="*", type=Path)
    p.add_argument("--box", type=parse_box)
    p.add_argument("--profile", default="auto")
    p.add_argument("--persona", default="monke")
    p.add_argument("--variant", action="append", default=[])
    p.add_argument("--mode", choices=("split", "merged", "both"), default="split")
    p.add_argument("--model")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--render", action="store_true")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--out", type=Path)
    p.add_argument("--from-train", type=Path)
    p.add_argument("--allow-claude", action="store_true")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.model in brain.CLAUDE_ALIASES and not args.allow_claude:
        raise SystemExit("Claude replay refused; pass --allow-claude to spend the 5 h window")
    pages = list(args.pages)
    if args.from_train:
        pages += sorted(args.from_train.glob("*.png"))
    if not pages:
        raise SystemExit("no input PNGs")
    variants = []
    for raw in args.variant or ["default="]:
        name, _, folder = raw.partition("=")
        variants.append((name, folder))
    modes = ("split", "merged") if args.mode == "both" else (args.mode,)
    out = args.out or Path("runs/replay") / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    chat = _fake_chat(os.environ["SMRITI_FAKE_BRAIN"]) if os.environ.get("SMRITI_FAKE_BRAIN") else brain.chat
    rows = []
    for name, folder in variants:
        if folder:
            os.environ["SMRITI_SKILL_DIR"] = folder
            skill._CACHE.clear()
        for mode in modes:
            for n in range(1, args.repeat + 1):
                for page in pages:
                    row = run_case(page, chat=chat, box=args.box, profile=args.profile,
                                   variant=name, mode=mode, model=args.model,
                                   no_write=args.no_write, repeat=n)
                    rows.append(row)
                    if args.render and row["reply"]:
                        _render(page, row["reply"], out, name)
    with (out / "results.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out / "summary.md").write_text(summary_markdown(rows), encoding="utf-8")
    print(f"{len(rows)} replay rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
