"""Run 16: reply line 1 inked over Navy's writing — he kept writing under
his prompt while the reply streamed. Before each unit the band it will
cover is checked on a fresh screenshot; Monke's own ink doesn't count."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import monke

UNIT = [[(120, 900), (900, 900)], [(120, 940), (900, 940)]]


def shot(*lines):
    img = Image.new("RGB", (1404, 1872), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for ln in lines:
        d.line(ln, fill=(0, 0, 0), width=4)
    return img


class BandBusyTest(unittest.TestCase):
    def test_navy_writing_in_band_is_busy(self):
        self.assertTrue(monke._band_busy(shot((200, 920, 700, 925)), UNIT, []))

    def test_empty_band_is_free(self):
        self.assertFalse(monke._band_busy(shot((200, 600, 700, 600)), UNIT, []))

    def test_monke_own_ink_in_band_is_free(self):
        mine = [[(200, 920), (700, 925)]]
        self.assertFalse(monke._band_busy(shot((200, 920, 700, 925)), UNIT, mine))


if __name__ == "__main__":
    unittest.main()
