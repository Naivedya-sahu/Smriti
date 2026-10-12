"""Contracts for Set-of-Mark overview labels."""
import sys
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import som
import harness


class SomTest(unittest.TestCase):
    def test_harness_marks_use_a_512px_overview(self):
        page = Image.new("RGB", (1404, 1872), "white")
        _, overview = harness.layers(page, (100, 100, 400, 300),
                                     marks=({"N1": (100, 100, 400, 300)}, []))
        self.assertEqual(overview.height, 512)
        self.assertTrue(any(pixel != (255, 255, 255)
                            for pixel in overview.get_flattened_data()))

    def test_marks_every_region_and_free_rect(self):
        image = Image.new("RGB", (384, 512), "white")
        regions = {"N1": (100, 100, 400, 300), "M1": (600, 400, 900, 700), "F": (200, 900, 700, 1100)}
        out, tags = som.mark(image, 512 / 1872, regions, [("E1", (900, 1200, 1300, 1600))], return_tags=True)
        self.assertEqual(out.size, image.size)
        self.assertEqual(set(tags), {"N1", "M1", "F", "E1"})
        boxes = list(tags.values())
        for x0, y0, x1, y1 in boxes:
            self.assertGreaterEqual(x0, 0); self.assertGreaterEqual(y0, 0)
            self.assertLessEqual(x1, out.width); self.assertLessEqual(y1, out.height)
        self.assertTrue(all(not som.overlap(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:]))

    def test_focus_is_red_and_navy_is_blue(self):
        image = Image.new("RGB", (384, 512), "white")
        out = som.mark(image, 512 / 1872, {"N1": (100, 100, 500, 500), "F": (600, 600, 900, 900)}, [])
        pixels = list(out.get_flattened_data())
        self.assertTrue(any(b > r + 80 for r, _, b in pixels))
        self.assertTrue(any(r > b + 80 for r, _, b in pixels))


if __name__ == "__main__":
    unittest.main()
