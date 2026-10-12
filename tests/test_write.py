"""Offline regression checks for lamp command generation."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

from write import to_lamp


class LampPathsTest(unittest.TestCase):
    def test_empty_paths_are_skipped_without_raising(self):
        self.assertEqual(to_lamp([[], [(1, 2), (3, 4)]], 2000),
                         "pen down 1 2 2000\npen move 3 4\npen up\n")
        self.assertEqual(to_lamp([], 2000), "")

    def test_sharp_turn_survives_decimation(self):
        path = [(0, 0), (3, 0), (6, 0), (6, 30), (6, 60)]
        cmds = to_lamp([path], 2400, step=3)
        self.assertIn("pen move 6 0\n", cmds)
        self.assertTrue(cmds.endswith("pen move 6 60\npen up\n"))

    def test_collinear_hairpin_keeps_turnaround(self):
        path = [(0, 0), (10, 0), (20, 0), (10, 0)]
        self.assertIn("pen move 20 0\n", to_lamp([path], 2400, step=3))

    def test_step_zero_keeps_only_bends(self):
        # device 2026-09-30: same coverage, 54% fewer lamp moves (1.6 ms each)
        import math
        arc = [(round(400 + 80 * math.cos(t / 40)), round(400 + 80 * math.sin(t / 40)))
               for t in range(250)]
        stride = to_lamp([arc], 2400, 3).count("move")
        bends = to_lamp([arc], 2400, 0)
        self.assertLess(bends.count("move"), stride)
        self.assertTrue(bends.startswith(f"pen down {arc[0][0]} {arc[0][1]} 2400"))
        self.assertIn(f"pen move {arc[-1][0]} {arc[-1][1]}\n", bends)


if __name__ == "__main__":
    unittest.main()
