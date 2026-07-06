"""
protocol.py — Lamp command protocol definitions
Elxnkv1.0 / integrator

All lamp commands are plain ASCII lines, newline-terminated.
This module provides builder functions and a validator so the
rest of the codebase never hand-rolls command strings.

Command reference:
    pen x y pressure     Draw pen point (x:0-1403, y:0-1871, p:0-4095)
    pen_up               End current stroke + trigger EPD partial refresh
    move x y             Reposition without drawing
    erase x y radius     Erase circle at (x,y), radius in pixels
    erase_line x0 y0 x1 y1 w   Erase along line, width w pixels
    color RRGGBB         Set draw color (hex, default 000000 = black)
    clear                Fill screen white + full refresh
    refresh              Force full GC16 refresh
    quit                 Flush + exit lamp

Batch helper:
    A Batch object accumulates commands and serializes them as one
    newline-joined string for a single send() call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

# ── Canvas constants ──────────────────────────────────────────────────────────
CANVAS_W = 1404
CANVAS_H = 1872
MAX_PRESSURE = 4095

# ── Primitive command builders ─────────────────────────────────────────────────

def pen(x: int, y: int, pressure: int) -> str:
    """Single pen point. Clamps all values to valid range."""
    x = max(0, min(x, CANVAS_W - 1))
    y = max(0, min(y, CANVAS_H - 1))
    pressure = max(0, min(pressure, MAX_PRESSURE))
    return f"pen {x} {y} {pressure}"


def pen_up() -> str:
    return "pen_up"


def move(x: int, y: int) -> str:
    x = max(0, min(x, CANVAS_W - 1))
    y = max(0, min(y, CANVAS_H - 1))
    return f"move {x} {y}"


def erase(x: int, y: int, radius: int) -> str:
    radius = max(1, radius)
    return f"erase {x} {y} {radius}"


def erase_line(x0: int, y0: int, x1: int, y1: int, width: int = 10) -> str:
    return f"erase_line {x0} {y0} {x1} {y1} {width}"


def color(r: int, g: int, b: int) -> str:
    return f"color {r:02X}{g:02X}{b:02X}"


def color_black() -> str:
    return "color 000000"


def color_white() -> str:
    return "color FFFFFF"


def clear() -> str:
    return "clear"


def refresh() -> str:
    return "refresh"


def quit_lamp() -> str:
    return "quit"


# ── Stroke helper ────────────────────────────────────────────────────────────

def stroke(points: list[tuple[int, int, int]]) -> list[str]:
    """
    Convert a list of (x, y, pressure) tuples into a complete stroke
    command sequence: pen ... pen_up.

    Automatically filters points below minimum pressure (likely hover noise).
    """
    MIN_PRESSURE = 50
    cmds = []
    for x, y, p in points:
        if p >= MIN_PRESSURE:
            cmds.append(pen(x, y, p))
    if cmds:
        cmds.append(pen_up())
    return cmds


# ── Batch ────────────────────────────────────────────────────────────────────

@dataclass
class Batch:
    """
    Accumulates lamp commands for efficient sending.
    Serialize with str(batch) or batch.encode().
    """
    _cmds: List[str] = field(default_factory=list)

    def add(self, cmd: str) -> 'Batch':
        self._cmds.append(cmd)
        return self

    def pen(self, x, y, p)           -> 'Batch': return self.add(pen(x, y, p))
    def pen_up(self)                  -> 'Batch': return self.add(pen_up())
    def move(self, x, y)              -> 'Batch': return self.add(move(x, y))
    def erase(self, x, y, r)          -> 'Batch': return self.add(erase(x, y, r))
    def erase_line(self, x0,y0,x1,y1,w=10) -> 'Batch':
        return self.add(erase_line(x0, y0, x1, y1, w))
    def color(self, r, g, b)          -> 'Batch': return self.add(color(r, g, b))
    def clear(self)                   -> 'Batch': return self.add(clear())
    def refresh(self)                 -> 'Batch': return self.add(refresh())

    def stroke(self, points: list[tuple[int,int,int]]) -> 'Batch':
        for cmd in stroke(points):
            self.add(cmd)
        return self

    def __len__(self) -> int:
        return len(self._cmds)

    def __str__(self) -> str:
        return '\n'.join(self._cmds) + '\n'

    def encode(self) -> bytes:
        return str(self).encode('ascii')

    def clear_cmds(self) -> None:
        self._cmds.clear()
