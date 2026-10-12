"""Contracts for pure region placement."""
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import place


class PlaceTest(unittest.TestCase):
    def test_occupancy_dilates_ink_and_resolver_goes_beside(self):
        image = Image.new("RGB", (1404, 1872), "white")
        ImageDraw.Draw(image).line((400, 800, 600, 800), fill="black", width=4)
        occ = place.occupancy(image)
        self.assertFalse(occ.free((500, 790, 510, 810)))
        spot = place.resolve({"how": "beside", "id": "N1"}, {"N1": (200, 300, 400, 500)},
                             occ, (200, 100), floor=100)
        self.assertEqual((spot.x, spot.y, spot.k, spot.why), (440, 300, 1.0, "beside N1"))

    def test_beside_falls_below_and_never_scales_under_point_seven(self):
        occ = place.Occ(24, np.zeros((78, 59), bool))
        regions = {"N1": (1200, 300, 1380, 500)}
        spot = place.resolve({"how": "beside", "id": "N1"}, regions, occ, (300, 100), floor=0)
        self.assertEqual((spot.x, spot.y), (860, 300))  # left mirror is next in the resolver chain
        narrow = place.resolve({"how": "free"}, {}, occ, (2100, 100), floor=0)
        self.assertIsNone(narrow)

    def test_free_rects_skip_ui_and_shift_preserves_input(self):
        occ = place.Occ(24, np.zeros((78, 59), bool))
        free = place.free_rects(occ)
        self.assertTrue(free)
        self.assertTrue(all(box[0] >= 120 and box[1] >= 100 for _, box in free))
        strokes = [[(10, 20), (20, 20)]]
        moved = place.shift(strokes, 100, 200, 0.7)
        self.assertEqual(strokes, [[(10, 20), (20, 20)]])
        self.assertEqual(moved, [[(100, 200), (107, 200)]])

    def test_seeded_random_pages_never_overlap_or_leave_screen(self):
        rng = np.random.default_rng(20261010)
        for _ in range(200):
            busy = np.zeros((78, 59), bool)
            for _ in range(int(rng.integers(1, 15))):
                x, y = rng.integers((5, 5), (55, 74))
                w, h = rng.integers((1, 1), (8, 8))
                busy[y:min(78, y+h), x:min(59, x+w)] = True
            occ = place.Occ(24, busy)
            w, h = map(int, rng.integers((80, 60), (700, 500)))
            spot = place.resolve({"how": "free"}, {}, occ, (w, h), floor=int(rng.integers(0, 1000)))
            if spot is None:
                continue
            box = (spot.x, spot.y, spot.x + round(w*spot.k), spot.y + round(h*spot.k))
            self.assertGreaterEqual(spot.k, .7)
            self.assertGreaterEqual(box[0], 120); self.assertGreaterEqual(box[1], 100)
            self.assertLessEqual(box[2], 1404); self.assertLessEqual(box[3], 1872)
            self.assertTrue(occ.free(box))


if __name__ == "__main__":
    unittest.main()
