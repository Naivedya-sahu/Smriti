"""Run 13: Navy scrolled by hand between turns (vertical scrolls are not
detected), the carried floor was from the old view — reply 2 started at
y 1390 under a prompt ending at 830, and its graphs had no room."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import monke


class ReplyTopTest(unittest.TestCase):
    def test_screen_beats_stale_floor(self):
        shot = Image.new("L", (1404, 1872), 255)
        ImageDraw.Draw(shot).line((120, 800, 600, 800), fill=0, width=6)   # prompt bottom
        prompt = [[(120, 760), (600, 800)]]
        y0 = monke._reply_top(1380, prompt, None, shot)
        self.assertLess(y0, 900)
        self.assertGreater(y0, 800)

    def test_no_screenshot_keeps_the_carried_floor(self):
        self.assertEqual(monke._reply_top(1380, [[(1, 700), (2, 800)]], None, None), 1430)


if __name__ == "__main__":
    unittest.main()
