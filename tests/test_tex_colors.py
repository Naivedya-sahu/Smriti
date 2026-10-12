"""Run 14: '\addplot[blue]' inked black — the tex path traced a grey
image. Dark pixels are split by hue; each stroke carries its xochitl pen
colour (0 black, 6 blue, 7 red) and the streamer switches pen per colour."""
# pyright: reportMissingImports=false

import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import ink
import monke


class ColorMaskTest(unittest.TestCase):
    def test_split_by_hue(self):
        rgb = np.full((50, 90, 3), 255, np.uint8)
        rgb[10, 5:25] = (0, 0, 0)
        rgb[20, 30:50] = (0, 0, 255)
        rgb[30, 60:80] = (255, 0, 0)
        m = ink.color_masks(rgb)
        self.assertEqual(sorted(m), [0, 6, 7])
        self.assertTrue(m[0][10, 10] and m[6][20, 40] and m[7][30, 70])
        self.assertFalse(m[0][20, 40] or m[0][30, 70])


class StreamPenColorTest(unittest.TestCase):
    def test_coloured_strokes_switch_pen_and_restore_black(self):
        blue = monke.Drawn([(100, 400), (300, 400)]); blue.color = 6
        black = monke.Drawn([(100, 450), (300, 450)])
        calls = []
        with (patch.object(monke, "lamp", lambda c, h: calls.append("lamp")),
              patch.object(monke, "pen_color", lambda h, c: calls.append(c) or True),
              patch.object(monke, "qml_eval", return_value="2"),
              patch.object(monke, "_same_page", return_value=True),
              patch.object(monke, "_on_monke", return_value=contextlib.nullcontext()),
              patch.object(monke, "_layout", return_value=[black, blue]),
              patch.object(monke, "_verify_ink"), patch.object(monke, "_shot", return_value=None),
              patch.object(monke.time, "sleep"), patch.object(monke, "_echo_mark")):
            from ink import load_styles
            s = monke._Streamer([], load_styles()["cursive"], 300, 1800, "h", 3, None,
                                lambda *a: None, None)
            s._unit("$$x$$")
        self.assertEqual(calls, ["lamp", 6, "lamp", 0])


if __name__ == "__main__":
    unittest.main()
