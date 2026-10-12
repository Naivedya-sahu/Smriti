"""Offline checks that reader images preserve native screenshot color."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import harness


class ReaderColorTest(unittest.TestCase):
    def test_focus_and_overview_keep_colored_native_ink_and_remove_grid_dots(self):
        page = Image.new("RGB", (200, 120), "white")
        draw = ImageDraw.Draw(page)
        draw.rectangle((5, 5, 7, 7), fill="black")
        draw.rectangle((50, 40, 100, 75), fill=(0, 255, 255))
        focus, overview = harness.layers(page, (50, 40, 100, 75))
        self.assertEqual(overview.getpixel((6, 6)), (255, 255, 255))
        self.assertEqual(overview.getpixel((75, 60)), (0, 255, 255))
        self.assertEqual(focus.getpixel((focus.width // 2, focus.height // 2)),
                         (0, 255, 255))


if __name__ == "__main__":
    unittest.main()
