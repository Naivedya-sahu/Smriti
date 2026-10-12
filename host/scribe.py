"""
scribe.py — handwriting OCR: service (GPU box) + client (daemon).

Reads Navy's handwriting from a CLEAN stroke render (capture.render_strokes
— white page, no template dots), so the reading path never touches
screenshots. TrOCR is a line model: the page is split into text lines by
row projection, each line OCR'd, lines returned top-to-bottom.

Service (laptop/desktop, needs the `scribe` extra: uv sync --extra scribe):
    uv run python host/scribe.py serve [--port 7433]

Client (daemon side, stdlib only):
    from scribe import ocr
    ocr(png_bytes)          # -> ["line one", ...] | None (all endpoints down)

Layer test:
    uv run python host/scribe.py selfcheck      # needs the scribe extra
    uv run python host/scribe.py see page.png   # one-shot local OCR
"""

from __future__ import annotations

import io
import json
import sys
import tomllib
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODEL = "microsoft/trocr-base-handwritten"
PORT = 7433


# ── client (stdlib, runs on the daemon box) ──────────────────────────────────

def endpoints() -> list[str]:
    with open(REPO / "config.toml", "rb") as f:
        return tomllib.load(f).get("ocr", {}).get("endpoints", [])


def ocr(png: bytes, eps: list[str] | None = None,
        timeout: float = 25.0) -> list[str] | None:
    """POST the page render to the first scribe that answers.
    None = every endpoint down/failed → caller falls back to vision AI."""
    for ep in (eps if eps is not None else endpoints()):
        try:
            req = urllib.request.Request(
                ep.rstrip("/") + "/ocr", data=png,
                headers={"Content-Type": "image/png"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)["lines"]
        except Exception as e:
            print(f"[scribe] {ep} failed ({str(e)[:80]})", flush=True)
    return None


# ── engine (GPU box only — imports behind functions) ─────────────────────────

def _segment(img, min_h: int = 14, gap: int = 8, pad: int = 8) -> list:
    """Split a clean white-bg page into text-line crops by row projection.
    gap = blank rows that end a line (cursive descenders bridge small gaps)."""
    import numpy as np
    a = np.asarray(img.convert("L")) < 128
    dark = a.any(axis=1)
    lines, y0, blanks = [], None, 0
    for y, d in enumerate(dark):
        if d:
            if y0 is None:
                y0 = y
            blanks = 0
        elif y0 is not None:
            blanks += 1
            if blanks >= gap:
                if y - blanks - y0 >= min_h:
                    lines.append((y0, y - blanks))
                y0, blanks = None, 0
    if y0 is not None and len(dark) - y0 >= min_h:
        lines.append((y0, len(dark)))
    crops = []
    for y0, y1 in lines:
        cols = a[y0:y1].any(axis=0).nonzero()[0]
        if not len(cols):
            continue
        crops.append(img.crop((max(0, int(cols[0]) - pad), max(0, y0 - pad),
                               min(img.width, int(cols[-1]) + pad),
                               min(img.height, y1 + pad))))
    return crops


class _Engine:
    """Lazy TrOCR. First call loads the model (~1.4GB cached)."""
    def __init__(self):
        self.proc = self.model = self.device = None

    def _load(self):
        if self.model is not None:
            return
        import torch
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.proc = TrOCRProcessor.from_pretrained(MODEL)
        self.model = VisionEncoderDecoderModel.from_pretrained(MODEL).to(self.device)
        self.model.eval()
        print(f"[scribe] {MODEL} on {self.device}", flush=True)

    def lines(self, png: bytes) -> list[str]:
        import torch
        from PIL import Image
        self._load()
        img = Image.open(io.BytesIO(png)).convert("RGB")
        crops = _segment(img) or [img]
        px = self.proc(images=crops, return_tensors="pt").pixel_values.to(self.device)
        with torch.no_grad():
            ids = self.model.generate(px, max_new_tokens=96)
        return [t.strip() for t in
                self.proc.batch_decode(ids, skip_special_tokens=True) if t.strip()]


# ── service ──────────────────────────────────────────────────────────────────

def serve(port: int = PORT) -> None:
    import http.server
    eng = _Engine()
    eng._load()                       # fail fast, warm before first request

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self._send(200, {"ok": True, "model": MODEL, "device": eng.device}) \
                if self.path == "/health" else self.send_error(404)

        def do_POST(self):
            if self.path != "/ocr":
                self.send_error(404)
                return
            png = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                self._send(200, {"lines": eng.lines(png)})
            except Exception as e:
                self._send(500, {"error": str(e)[:300]})

        def _send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    print(f"[scribe] listening :{port}", flush=True)
    http.server.ThreadingHTTPServer(("", port), H).serve_forever()


def _selfcheck() -> None:
    """Render a known sentence with the print stroke-font pipeline, OCR it
    back, require word overlap — fails if segmentation or model breaks."""
    sys.path.insert(0, str(REPO / "host"))
    from ink import load_styles, render
    from capture import render_strokes
    text = "the quick brown fox"
    strokes = render(text, load_styles()["print"], x=40, y=40)
    buf = io.BytesIO()
    render_strokes(strokes, crop=True).save(buf, format="PNG")
    got = " ".join(_Engine().lines(buf.getvalue())).lower()
    hits = sum(w in got for w in text.split())
    assert hits >= 3, f"OCR too wrong: {got!r} ({hits}/4 words)"
    print(f"scribe selfcheck ok: {got!r} ({hits}/4 words)")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["serve"]:
        serve(int(a[a.index("--port") + 1]) if "--port" in a else PORT)
    elif a[:1] == ["selfcheck"]:
        _selfcheck()
    elif a[:1] == ["see"] and len(a) > 1:
        print("\n".join(_Engine().lines(Path(a[1]).read_bytes())))
    else:
        sys.exit(__doc__)
