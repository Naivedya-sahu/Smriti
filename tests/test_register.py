"""Run 12/15: #P never erased on a scrolled view — to_screen used
y - top, the device (scrolled, 2026-09-30 22:30) measured y + top and an
x offset no view field explains. The offset is now found by matching the
page's strokes to the screenshot."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import vectors


def page_and_screen(ox, oy):
    rng = np.random.default_rng(1)
    layers = {"Layer 1": [], "Monke": []}
    for i in range(60):                       # handwriting-ish strokes down a long page
        x, y = rng.integers(-900, 400), rng.integers(200, 10700)
        layers["Layer 1" if i % 2 else "Monke"].append(
            [(float(x + k * 7), float(y + 20 * np.sin(k))) for k in range(25)])
    img = Image.new("L", (1404, 1872), 255)
    d = ImageDraw.Draw(img)
    for ss in layers.values():
        for s in ss:
            d.line([(x + ox, y + oy) for x, y in s], fill=0, width=3)
    return layers, np.asarray(img) < 160


class RegisterTest(unittest.TestCase):
    def test_recovers_scrolled_offset(self):
        layers, ink = page_and_screen(936, -9000)
        ox, oy = vectors.register(layers, ink)
        self.assertLessEqual(abs(ox - 936), 3)
        self.assertLessEqual(abs(oy + 9000), 3)

    def test_look_alike_lines_do_not_fool_it(self):
        # sim hash_p 2026-10-04: the page file held only the prompt line, the
        # screen also Monke's fresh reply lines — same row profile, the best
        # row match was a reply line, the hit check failed, #P was kept
        line = lambda y, f=1.0: [[(float(-600 + 90 * i + k * 3), float(y + 25 * np.sin(f * k + i)))
                                  for k in range(25)] for i in range(12)]
        layers = {"Layer 1": line(5000)}
        img = Image.new("L", (1404, 1872), 255)
        d = ImageDraw.Draw(img)
        for y, f in ((180, 1.9), (300, 1.0), (420, 0.45)):          # other writing, the prompt, other writing
            for s in line(5000, f):
                d.line([(x + 702, yy - 5000 + y) for x, yy in s], fill=0, width=3)
        self.assertEqual(vectors.register(layers, np.asarray(img) < 160), (702, -4700))

    def test_blank_screen_is_none(self):
        layers, _ = page_and_screen(936, -9000)
        self.assertIsNone(vectors.register(layers, np.zeros((1872, 1404), bool)))

    def test_to_screen_uses_registered_offset(self):
        self.assertEqual(vectors.to_screen([(10, 20)], {"ox": 936, "oy": -9000, "right": 0, "top": 0}),
                         [(946, -8980)])

    def test_to_page_round_trip_within_one_pixel_at_fractional_scale(self):
        # screen pixels are integers: to_screen truncates, so page -> screen -> page is exact only to
        # 1/scale page units; the contract is "within 1 screen px", pinned here
        for v in ({"ox": 0.1, "oy": 0.2, "scale": 1.5}, {"right": -468.3, "top": -10354.7, "scale": 1.25}):
            pts = [(1, 1), (123.4, 567.8), (-300.5, 2000.25)]
            back = vectors.to_page(vectors.to_screen(pts, v), v)
            for (x, y), (bx, by) in zip(pts, back):
                self.assertLessEqual(abs(x - bx) * v["scale"], 1.0)
                self.assertLessEqual(abs(y - by) * v["scale"], 1.0)
            self.assertEqual(vectors.to_screen(back, v), vectors.to_screen(pts, v))   # screen side is a fixed point

    def test_to_page_inverts_registered_and_field_views_at_scale_one_and_two(self):
        cases = (
            ({"ox": 936, "oy": -9000, "scale": 1}, [(10, 20), (300, 500)]),
            ({"ox": -702, "oy": -300, "scale": 2}, [(10, 20), (300, 500)]),
            ({"right": -702, "top": 300, "scale": 1}, [(10, 20), (300, 500)]),
            ({"right": -702, "top": -300, "scale": 2}, [(10, 20), (300, 500)]),
        )
        for view, stroke in cases:
            self.assertEqual(vectors.to_page(vectors.to_screen(stroke, view), view), stroke)


if __name__ == "__main__":
    unittest.main()
