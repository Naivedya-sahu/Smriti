"""
lampmark.py — host-clock injection windows for pendump's stroke tagging.

    lampmark.mark(t0, t1, n_strokes, note="")

appends one JSON line {"t0", "t1", "n_strokes", "note"} (host time.time())
to build/pendump/lamp-windows.jsonl. Host side only — nothing touches the
tablet's disk. One small append per lamp batch; never raises (a tagging aid
must never break inking). SMRITI_LAMPMARK overrides the path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PATH = Path(os.environ.get("SMRITI_LAMPMARK") or
            Path(__file__).resolve().parents[1] / "build" / "pendump" / "lamp-windows.jsonl")


def mark(t0: float, t1: float, n_strokes: int, note: str = "", *, path=None) -> None:
    try:
        p = Path(path) if path else PATH
        line = json.dumps({"t0": round(float(t0), 3), "t1": round(float(t1), 3),
                           "n_strokes": int(n_strokes), "note": str(note)}) + "\n"
        try:
            f = open(p, "a", encoding="utf-8")
        except FileNotFoundError:
            p.parent.mkdir(parents=True, exist_ok=True)
            f = open(p, "a", encoding="utf-8")
        with f:
            f.write(line)
    except Exception:
        pass


def read(path=None) -> list[dict]:
    """All marks in order; unreadable file or bad lines are skipped."""
    out = []
    try:
        with open(Path(path) if path else PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    float(d["t0"]), float(d["t1"])
                    out.append(d)
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    return out
