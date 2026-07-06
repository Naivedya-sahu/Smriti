"""
write.py — dev-path writer: text → strokes → lamp over TCP (host side).

    python host/write.py "hello monke" --style cursive [--host 10.11.99.1] [--clear]

ponytail: plain socket, no reconnect logic — dev tool, rerun on failure.
Device-side production path is device/smriti_write.py via vellum.
"""

from __future__ import annotations

import argparse
import socket

import protocol
from ink import load_styles, render


def send(strokes, pressure: int, host: str, port: int = 33334, clear: bool = False) -> int:
    b = protocol.Batch()
    if clear:
        b.clear()
    for s in strokes:
        b.stroke([(x, y, pressure) for x, y in s])
    with socket.create_connection((host, port), timeout=5) as sk:
        sk.sendall(b.encode())
    return len(b)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--style", default="cursive")
    ap.add_argument("--host", default="10.11.99.1")
    ap.add_argument("--x", type=int, default=100)
    ap.add_argument("--y", type=int, default=150)
    ap.add_argument("--clear", action="store_true")
    a = ap.parse_args()
    style = load_styles()[a.style]
    strokes = render(a.text, style, a.x, a.y)
    n = send(strokes, style.get("pressure", 2400), a.host, clear=a.clear)
    print(f"sent {n} commands, {len(strokes)} strokes")


if __name__ == "__main__":
    main()
