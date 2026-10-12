"""Offline check for the layer probe's pixel count on RGB screenshots."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import layers


class ZoneInkTest(unittest.TestCase):
    def test_rgb_screenshot_counts_pixels_not_channels(self):
        img = Image.new("RGB", (1404, 1872), "white")
        ImageDraw.Draw(img).rectangle((410, 510, 419, 519), fill="black")
        self.assertEqual(layers._zone_ink(img, (400, 500, 1000, 600)), 100)

    def test_blank_page_yields_probe_zone_below_toolbar(self):
        self.assertEqual(layers._probe_zone([(0, 1872)]), (400, 170, 1000, 270))

    def test_no_room_yields_no_zone(self):
        self.assertIsNone(layers._probe_zone([(0, 200), (1700, 1800)]))


if __name__ == "__main__":
    unittest.main()
