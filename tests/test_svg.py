"""run 10: block diagrams inked as empty boxes — the prompt forbade <text>
because svg_to_strokes could not draw it. Labels are now handwritten."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

from svg import svg_to_strokes

BOX = '<rect x="10" y="10" width="120" height="40"/>'


class SvgTextTest(unittest.TestCase):
    def test_label_is_inked_inside_its_box(self):
        bare = svg_to_strokes(f'<svg viewBox="0 0 400 300">{BOX}</svg>', x=100, y=100)
        both = svg_to_strokes(f'<svg viewBox="0 0 400 300">{BOX}'
                              '<text x="70" y="35" text-anchor="middle">5V reg</text></svg>',
                              x=100, y=100)
        self.assertGreater(len(both), len(bare) + 3)
        box = bare[0]
        x0, x1 = min(p[0] for p in box), max(p[0] for p in box)
        y0, y1 = min(p[1] for p in box), max(p[1] for p in box)
        for s in both[1:]:
            for x, y in s:
                self.assertTrue(x0 <= x <= x1 and y0 <= y <= y1, (x, y, (x0, y0, x1, y1)))

    def test_text_only_svg_still_draws(self):
        self.assertTrue(svg_to_strokes('<svg viewBox="0 0 100 40"><text x="5" y="20">Qi</text></svg>'))


if __name__ == "__main__":
    unittest.main()


class HandDotTest(unittest.TestCase):
    def test_handwritten_decimal_point_survives(self):
        from ink import load_styles, render
        for name in ("print", "cursive"):
            st = load_styles()[name]
            self.assertEqual(len(render("2.4", st)), len(render("24", st)) + 1, name)


class IntCoordsTest(unittest.TestCase):
    """run 11 20:39: the dot ring returned floats -> _missing crashed
    indexing the frame, the daemon died mid-greeting."""
    @unittest.skipUnless(__import__("tex").have_pdflatex(), "no pdflatex on this machine")
    def test_rendered_points_are_ints(self):
        from ink import load_styles, render
        from tex import tex_to_strokes
        for st in (render("Navy back. 2.4 V", load_styles()["cursive"]),
                   tex_to_strokes(r"$6.2 \times 3$"),
                   svg_to_strokes('<svg viewBox="0 0 100 40"><text x="5" y="20">2.4 V</text></svg>')):
            for s in st:
                for p in s:
                    self.assertTrue(all(isinstance(c, int) for c in p), p)
