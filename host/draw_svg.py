"""
draw_svg.py — ink an SVG file onto the reMarkable canvas over ssh.

    # preview first (no device), then draw:
    uv run python host/draw_svg.py mydrawing.svg --out preview.png
    uv run python host/draw_svg.py mydrawing.svg --rm2 rm2-smriti

Placement/size in page pixels (page is 1404x1872):
    --x --y        top-left corner (default 200,300)
    --max-w --max-h  bounding box the drawing is scaled to fit (default 1000x1200)

Same ink path as write.py: svg -> strokes (host/svg.py) -> lamp pen-injection,
so it's real, saved notebook ink. A notebook page must be OPEN on the tablet.

Supported SVG: line, rect, circle, ellipse, polyline, polygon, path
(M L H V C Q Z). No <g>/transform/<text>/styles — flatten those in your editor
first (Inkscape: Path > Object to Path, and ungroup) if the preview looks off.
"""

from __future__ import annotations

import argparse
import subprocess

from deps import ssh_argv
from pathlib import Path

from svg import svg_to_strokes
from write import to_lamp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("svg", help="path to an .svg file")
    ap.add_argument("--rm2", default="rm2-smriti", help="ssh host alias")
    ap.add_argument("--lamp", default="/home/root/.vellum/bin/lamp")
    ap.add_argument("--x", type=int, default=200)
    ap.add_argument("--y", type=int, default=300)
    ap.add_argument("--max-w", type=int, default=1000)
    ap.add_argument("--max-h", type=int, default=1200)
    ap.add_argument("--pressure", type=int, default=2400)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--out", help="save a PNG preview instead of inking on the tablet")
    a = ap.parse_args()

    svg = Path(a.svg).read_text(encoding="utf-8")
    strokes = svg_to_strokes(svg, x=a.x, y=a.y, max_w=a.max_w, max_h=a.max_h)
    if not strokes:
        raise SystemExit("no drawable elements found — see supported-SVG note in "
                         "the docstring (flatten <g>/transforms/paths first)")
    if a.out:
        from capture import render_strokes
        render_strokes(strokes, crop=True).save(a.out)
        print(f"{a.out} ({len(strokes)} strokes)")
        return
    cmds = to_lamp(strokes, a.pressure, a.step)
    subprocess.run([*ssh_argv(a.rm2), a.lamp], input=cmds.encode("ascii"), check=True)
    print(f"drawn — {len(strokes)} strokes, {cmds.count(chr(10))} lamp commands")


if __name__ == "__main__":
    main()
