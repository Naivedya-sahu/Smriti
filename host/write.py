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

from ink import load_styles, render


def to_lamp(strokes, pressure: int, step: int = 3) -> str:
    out = []
    for s in strokes:
        pts = s[::step] + ([s[-1]] if (len(s) - 1) % step else [])
        x, y = pts[0]
        out.append(f"pen down {x} {y} {pressure}")
        out.extend(f"pen move {x} {y}" for x, y in pts[1:])
        out.append("pen up")
    return "\n".join(out) + "\n"


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
    strokes = render(a.text, style, a.x, a.y)
    if a.out:
        from capture import render_strokes
        render_strokes(strokes, crop=True).save(a.out)
        print(a.out)
        return
    cmds = to_lamp(strokes, style.get("pressure", 2400), a.step)
    subprocess.run(["ssh", a.rm2, a.lamp], input=cmds.encode("ascii"), check=True)
    print(f"sent {cmds.count(chr(10))} commands")


if __name__ == "__main__":
    main()
