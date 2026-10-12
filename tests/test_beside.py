"""Run 14: tikz graphs ~700 px wide on a 1404 px page, stacked -> graphs
2-3 'no room'. A drawing that fits beside the previous one goes there."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import monke


def box(x0, y0, x1, y1):
    return monke.Drawn([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


class BesideTest(unittest.TestCase):
    def test_second_graph_goes_right_of_the_first(self):
        row = (822, 1100)                                   # first graph: right edge, top
        g2 = [box(128, 1520, 822, 1900)]                    # laid out below it
        out = monke._beside(row, g2)
        xs = [x for s in out for x, _ in s]; ys = [y for s in out for _, y in s]
        self.assertEqual((min(xs), min(ys)), (862, 1100))
        self.assertLessEqual(max(xs), 1404 - 40)          # shrunk to fit (run-14 size, k ~0.73)
        self.assertTrue(all(isinstance(s, monke.Drawn) for s in out))

    def test_too_wide_stays_below(self):
        self.assertIsNone(monke._beside((820, 1100), [box(128, 1520, 1300, 1900)]))

    def test_prose_never_beside(self):
        self.assertIsNone(monke._beside((820, 1100), [[(128, 1520), (300, 1560)]]))
        self.assertIsNone(monke._beside(None, [box(128, 1520, 500, 1900)]))


if __name__ == "__main__":
    unittest.main()
