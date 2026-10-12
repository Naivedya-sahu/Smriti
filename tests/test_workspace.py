"""Offline contract checks for deterministic workspace observation."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

from workspace import observe


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.blank = Image.new("RGB", (1404, 1872), "white")

    def test_nearby_pen_strokes_group_but_distant_edit_stays_separate(self):
        strokes = [[(200, 300), (240, 305)],
                   [(260, 315), (300, 315)],
                   [(900, 1100), (950, 1110)]]
        state = observe(self.blank, strokes, frame_id="frame-1")
        self.assertEqual(state.frame_id, "frame-1")
        self.assertEqual(state.size, (1404, 1872))
        self.assertEqual([(r.source, r.box, r.stroke_count) for r in state.regions],
                         [("pen", (200, 300, 301, 316), 2),
                          ("pen", (900, 1100, 951, 1111), 1)])

    def test_delta_only_observation_reports_non_ui_changes(self):
        after = self.blank.copy()
        ImageDraw.Draw(after).rectangle((300, 400, 340, 420), fill="black")
        state = observe(after, [], before=self.blank)
        self.assertEqual([(r.source, r.box) for r in state.regions],
                         [("screen_delta", (300, 400, 341, 421))])
        self.assertGreater(state.regions[0].density, 0)

    def test_existing_pen_region_is_not_duplicated_by_pixel_delta(self):
        after = self.blank.copy()
        ImageDraw.Draw(after).line((300, 400, 340, 400), fill="black", width=8)
        state = observe(after, [[(300, 400), (340, 400)]], before=self.blank)
        self.assertEqual([r.source for r in state.regions], ["pen"])

    def test_delta_reaching_past_pen_region_is_kept(self):
        # page_diff is one union box: pen stroke + a distant edit. The delta
        # is not redundant, so dropping it would hide the distant edit.
        after = self.blank.copy()
        draw = ImageDraw.Draw(after)
        draw.line((300, 400, 340, 400), fill="black", width=8)
        draw.rectangle((900, 1200, 940, 1220), fill="black")
        state = observe(after, [[(300, 400), (340, 400)]], before=self.blank)
        self.assertEqual(sorted(r.source for r in state.regions), ["pen", "screen_delta"])

    def test_ui_changes_and_blank_page_produce_no_regions(self):
        after = self.blank.copy()
        ImageDraw.Draw(after).rectangle((0, 200, 100, 900), fill="black")
        self.assertEqual(observe(after, [], before=self.blank).regions, ())
        self.assertEqual(observe(self.blank, []).regions, ())

    def test_bridge_stroke_merges_existing_groups(self):
        strokes = [[(200, 300), (210, 300)],
                   [(310, 300), (320, 300)],
                   [(245, 300), (275, 300)]]
        regions = observe(self.blank, strokes).regions
        self.assertEqual([(r.box, r.stroke_count) for r in regions],
                         [((200, 300, 321, 301), 3)])

    def test_mismatched_frame_geometry_rejected(self):
        with self.assertRaises(ValueError):
            observe(self.blank, [], before=Image.new("RGB", (200, 100)))


if __name__ == "__main__":
    unittest.main()
