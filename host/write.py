"""
write.py — dev-path writer: text → strokes → lamp pen-injection over ssh.

    python host/write.py "hello monke" --style cursive [--rm2 rm2]

Pipes lamp commands to the lamp binary's stdin on the device (grammar:
"pen down x y [pressure]" / "pen move x y" / "pen up"). Same output path
as device/smriti-write; this one renders host-side (full Pillow pipeline,
proper cursive shaping) instead of replaying compiled .sf stroke-fonts.

ponytail: subprocess ssh per call, no persistent session — dev tool.
"""

from __future__ import annotations

import argparse
import subprocess

from deps import ssh_argv

from ink import load_styles, render


def decimate(s, step: int = 3, tol2: int = 4) -> list:
    """Points of path s to send: every step-th (the command budget on
    straight lines) plus any bend whose omission would shift the path more
    than sqrt(tol2) px. step 0 = bends only (same coverage on device, 54%
    fewer lamp moves at 1.6 ms each; 2026-09-30)."""
    if step < 0:
        raise ValueError("step must not be negative")
    indices = list(range(0, len(s), step or len(s)))
    if indices[-1] != len(s) - 1:
        indices.append(len(s) - 1)
    keep = set(indices)
    spans = list(zip(indices, indices[1:]))
    while spans:
        a, b = spans.pop()
        if b - a < 2:
            continue
        x0, y0 = s[a]
        dx, dy = s[b][0] - x0, s[b][1] - y0
        length2 = dx * dx + dy * dy

        def deviation2(i):
            px, py = s[i][0] - x0, s[i][1] - y0
            if not length2:
                return px * px + py * py
            projection = px * dx + py * dy
            if projection <= 0:
                return px * px + py * py
            if projection >= length2:
                return (px - dx) ** 2 + (py - dy) ** 2
            cross = px * dy - py * dx
            return cross * cross / length2
        turn = max(range(a + 1, b), key=deviation2)
        if deviation2(turn) > tol2:
            keep.add(turn)
            spans.extend(((a, turn), (turn, b)))
    return [s[i] for i in sorted(keep)]


def to_lamp(strokes, pressure: int, step: int = 0) -> str:
    if step < 0:
        raise ValueError("step must not be negative")
    out = []
    for s in strokes:
        if not s:
            continue
        pts = decimate(s, step)
        x, y = pts[0]
        out.append(f"pen down {x} {y} {pressure}")
        out.extend(f"pen move {x} {y}" for x, y in pts[1:])
        out.append("pen up")
    return "\n".join(out) + "\n" if out else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--style", default="cursive")
    ap.add_argument("--rm2", default="rm2", help="ssh host alias")
    ap.add_argument("--lamp", default="/home/root/.vellum/bin/lamp")
    ap.add_argument("--x", type=int, default=100)
    ap.add_argument("--y", type=int, default=150)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--out", help="render to a PNG instead of inking on the "
                                  "tablet — preview a font/size without a device")
    a = ap.parse_args()
    style = load_styles()[a.style]
    # \n in the text becomes real line breaks; render wraps at max_w too.
    strokes = render(a.text.replace("\\n", "\n"), style, a.x, a.y)
    if a.out:
        from capture import render_strokes
        render_strokes(strokes, crop=True).save(a.out)
        print(a.out)
        return
    cmds = to_lamp(strokes, style.get("pressure", 2400), a.step)
    subprocess.run([*ssh_argv(a.rm2), a.lamp], input=cmds.encode("ascii"), check=True)
    print(f"sent {cmds.count(chr(10))} commands")


if __name__ == "__main__":
    main()
