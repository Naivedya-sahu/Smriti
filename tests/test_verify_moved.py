"""Sim 2026-10-04 (scroll_right_after_reply): Navy scrolled during the 1 s
e-ink wait, the render check found 94/94 strokes 'missing' and inked the
whole reply again at the old screen spot. Nearly everything missing = the
view moved, not a render miss: never redraw."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import monke

STROKES = [[(100 + 40 * i + k, 300 + 5 * k) for k in range(0, 40, 5)] for i in range(10)]


def shot(strokes):
    img = Image.new("RGB", (1404, 1872), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for s in strokes:
        d.line(s, fill=(0, 0, 0), width=4)
    return img


class VerifyMovedTest(unittest.TestCase):
    def test_everything_missing_is_a_moved_view_not_a_redraw(self):
        with patch.object(monke, "lamp") as lamp:
            monke._verify_ink(STROKES, "rm2", 2400, 3, shot=shot([]))
        lamp.assert_not_called()

    def test_one_missing_stroke_is_still_redrawn(self):
        with patch.object(monke, "lamp") as lamp:
            monke._verify_ink(STROKES, "rm2", 2400, 3, shot=shot(STROKES[1:]))
        lamp.assert_called_once()


if __name__ == "__main__":
    unittest.main()
