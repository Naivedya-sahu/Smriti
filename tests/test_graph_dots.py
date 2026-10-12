"""Run 12: G1 tick label '0.5' inked as '0 5' — graph.strokes traced without
the decimal-point path tex.py uses (dots=True + visible_dots)."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import graph


class GraphDotTest(unittest.TestCase):
    def test_decimal_point_survives(self):
        img = np.zeros((200, 400), bool)
        img[100:103, 20:380] = True          # an axis
        img[150:154, 200:204] = True         # a lone decimal point
        g = graph.Graph.new("x", 0, 1, y=0)
        st = graph.strokes(g, compile_fn=lambda tex: img)
        k = min(1.0, g.max_w / 380)
        dot_y = 152 * k
        self.assertTrue(any(all(abs(py - g.y - dot_y) < 8 for _, py in s) and len(s) > 1
                            and max(px for px, _ in s) - min(px for px, _ in s) < 12
                            for s in st), st)


if __name__ == "__main__":
    unittest.main()
