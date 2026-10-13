"""Selection-aware grounding (host/selection.py): when Navy lasso-selects ink
and asks, the selection is the model's explicit target. All device access is
faked: qml_eval is a dict of JS expression -> result (or exception)."""
# pyright: reportMissingImports=false

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import selection as sel
import vectors

HOST = "rm"
UNSCROLLED = {"pageId": "p", "scale": 1, "right": -702, "top": 0}
# live scrolled view (device 22:30): fields alone map wrong; registered
# offset x + 936 (right -468), y + top
SCROLLED = {"pageId": "p", "scale": 1, "right": -468, "top": -10354,
            "ox": 936, "oy": -10354}


class FakeQML:
    def __init__(self, table):
        self.table, self.calls = dict(table), []

    def __call__(self, host, js, timeout=6):
        self.calls.append(js)
        r = self.table.get(js)
        if isinstance(r, Exception):
            raise r
        return r


def probe(i):
    return sel.PROBES[i].js


def fake(count: object = "3", **by_index):
    t = {sel.COUNT_JS: count}
    for k, v in by_index.items():
        t[probe(int(k[1:]))] = v
    return FakeQML(t)


class ReadSelectionTest(unittest.TestCase):
    def setUp(self):
        sel.reset_cache()

    def test_probe_list_is_ordered_readonly_and_nonempty(self):
        self.assertGreaterEqual(len(sel.PROBES), 3)
        for p in sel.PROBES:
            for bad in ("delete", "addSelection", "clearSelected", "activeTool"):
                self.assertNotIn(bad, p.js, p.name)
            self.assertNotRegex(p.js, r"\.\w+\s*=[^=]", p.name)   # no property writes

    def test_device_bounding_rect_probes_are_first_in_page_space(self):
        self.assertEqual([p.name for p in sel.PROBES[:2]],
                         ["boundingRect", "itemsBoundingRect"])
        self.assertIn("sceneController.boundingRect", sel.PROBES[0].js)
        self.assertIn("sceneController.itemsBoundingRect", sel.PROBES[1].js)

    def test_first_candidate_success(self):
        q = fake(p0="[100,200,50,40]")
        s = sel.read_selection(q, HOST, view=UNSCROLLED)
        self.assertIsNotNone(s)
        self.assertEqual(s.count, 3)
        self.assertEqual(s.source, sel.PROBES[0].name)
        self.assertEqual(s.page_box, (100.0, 200.0, 150.0, 240.0))

    def test_falls_back_to_second_candidate(self):
        q = fake(p0="undefined", p1='{"rect":[10,20,30,40]}')
        s = sel.read_selection(q, HOST, view=UNSCROLLED)
        self.assertEqual(s.source, sel.PROBES[1].name)
        self.assertEqual(s.page_box, (10.0, 20.0, 40.0, 60.0))

    def test_falls_back_to_third_past_exception_and_err(self):
        q = fake(p0=RuntimeError("ssh died"), p1="ERR: TypeError",
                 p2='{"rect":[1,2,3,4],"ids":["a","b"]}')
        s = sel.read_selection(q, HOST, view=UNSCROLLED)
        self.assertEqual(s.source, sel.PROBES[2].name)
        self.assertEqual(s.ids, ["a", "b"])

    def test_all_candidates_fail_returns_none(self):
        q = fake(**{f"p{i}": "undefined" for i in range(len(sel.PROBES))})
        self.assertIsNone(sel.read_selection(q, HOST, view=UNSCROLLED))

    def test_garbage_and_degenerate_rects_rejected(self):
        q = fake(p0="not json", p1="[0,0,0,0]", p2="[1,2,NaN,4]")
        self.assertIsNone(sel.read_selection(q, HOST, view=UNSCROLLED))

    def test_count_zero_returns_none_without_probing(self):
        q = fake(count="0", p0="[1,2,3,4]")
        self.assertIsNone(sel.read_selection(q, HOST, view=UNSCROLLED))
        self.assertEqual(q.calls, [sel.COUNT_JS])

    def test_count_unreadable_returns_none(self):
        for c in (None, "ERR: x", "undefined", RuntimeError("down")):
            self.assertIsNone(sel.read_selection(fake(count=c, p0="[1,2,3,4]"), HOST,
                                                 view=UNSCROLLED), c)

    def test_working_candidate_is_cached_and_tried_first(self):
        sel.read_selection(fake(p0="undefined", p1="[1,2,3,4]"), HOST, view=UNSCROLLED)
        q = fake(p0="[9,9,9,9]", p1="[1,2,3,4]")
        s = sel.read_selection(q, HOST, view=UNSCROLLED)
        self.assertEqual(s.source, sel.PROBES[1].name)
        self.assertEqual(q.calls, [sel.COUNT_JS, probe(1)])

    def test_no_probe_is_remembered_so_later_turns_cost_one_count(self):
        q = fake()                                     # count 3, every probe silent
        self.assertIsNone(sel.read_selection(q, HOST, view=UNSCROLLED))
        self.assertEqual(len(q.calls), 1 + len(sel.PROBES))
        self.assertIsNone(sel.read_selection(q, HOST, view=UNSCROLLED))
        self.assertEqual(len(q.calls), 2 + len(sel.PROBES))
        sel.reset_cache()                              # daemon start probes again
        q.table[probe(0)] = "[1,2,3,4]"
        self.assertEqual(sel.read_selection(q, HOST, view=UNSCROLLED).source, sel.PROBES[0].name)

    def test_cached_candidate_failing_falls_back_to_list(self):
        sel.read_selection(fake(p0="undefined", p1="[1,2,3,4]"), HOST, view=UNSCROLLED)
        q = fake(p0="[5,6,7,8]", p1="undefined")
        s = sel.read_selection(q, HOST, view=UNSCROLLED)
        self.assertEqual(s.source, sel.PROBES[0].name)

    def test_no_view_keeps_page_box_but_no_screen_box(self):
        s = sel.read_selection(fake(p0="[1,2,3,4]"), HOST, view=None)
        self.assertEqual(s.page_box, (1.0, 2.0, 4.0, 6.0))
        self.assertIsNone(s.screen_box)


class ConversionTest(unittest.TestCase):
    def setUp(self):
        sel.reset_cache()

    def test_scrolled_view_screen_box_uses_registered_offset(self):
        q = fake(p0="[100,10500,200,200]")
        s = sel.read_selection(q, HOST, view=SCROLLED)
        self.assertEqual(s.screen_box, (1036, 146, 1236, 346))


    def test_mapping_agrees_with_vectors_to_screen(self):
        pts = vectors.to_screen([(100, 10500)], SCROLLED)
        self.assertEqual(sel.page_to_screen((100, 10500, 100, 10500), SCROLLED)[:2], pts[0])


class StrokesInTest(unittest.TestCase):
    S = sel.Selection(count=3, page_box=(0, 0, 100, 100), screen_box=None)

    def test_fully_inside_stroke_is_in(self):
        st = [(10, 10), (50, 50)]
        self.assertEqual(sel.strokes_in(self.S, [st]), [st])

    def test_overlap_exactly_60_percent_is_in(self):
        st = [(40, 10), (140, 50)]
        self.assertEqual(sel.strokes_in(self.S, [st]), [st])

    def test_overlap_50_percent_is_out(self):
        self.assertEqual(sel.strokes_in(self.S, [[(50, 10), (150, 50)]]), [])

    def test_disjoint_stroke_is_out(self):
        self.assertEqual(sel.strokes_in(self.S, [[(200, 200), (300, 300)]]), [])

    def test_flat_line_and_dot_use_length_not_zero_area(self):
        flat_in, flat_out, dot = [(10, 50), (90, 50)], [(60, 50), (160, 50)], [(5, 5)]
        self.assertEqual(sel.strokes_in(self.S, [flat_in, flat_out, dot]), [flat_in, dot])

    def test_layers_dict_is_flattened(self):
        st = [(10, 10), (20, 20)]
        self.assertEqual(sel.strokes_in(self.S, {"Layer 1": [st], "Monke": [[(500, 5), (600, 6)]]}),
                         [st])


class GroundingBlockTest(unittest.TestCase):
    S = sel.Selection(count=3, page_box=(100, 10500, 300, 10700),
                      screen_box=(1036, 146, 1236, 346), source="selectionRect")
    LINE = [(100, 10500), (300, 10500)]
    ARC = [(200 - 100 * math.cos(t / 10), 10600 - 100 * math.sin(t / 10)) for t in range(32)]

    def test_block_shape_and_rule_line(self):
        out = sel.grounding_block(self.S, [self.LINE, self.ARC])
        self.assertTrue(out.startswith('<selection count="3" box="1036,146,1236,346"'), out)
        self.assertIn("</selection>", out)
        self.assertEqual(out.splitlines()[-1], sel.RULE)

    def test_kinds_classify_line_and_curve(self):
        out = sel.grounding_block(self.S, [self.LINE, self.ARC, [(150, 10550)]])
        head = out.splitlines()[0]
        for k in ("line", "curve", "dot"):
            self.assertIn(k, head)

    def test_stays_under_budget_with_many_strokes(self):
        many = [[(100 + i, 10500), (101 + i, 10690)] for i in range(200)]
        out = sel.grounding_block(self.S, many, budget_chars=500)
        self.assertLessEqual(len(out), 500)
        self.assertIn("more", out)
        self.assertTrue(out.endswith(sel.RULE))

    def test_crop_bytes_marked_as_attached(self):
        self.assertIn('image="attached"', sel.grounding_block(self.S, [], crop_png_bytes=b"\x89PNG"))
        self.assertNotIn("image=", sel.grounding_block(self.S, []))

    def test_unknown_screen_box_falls_back_to_page_box(self):
        s = sel.Selection(count=1, page_box=(1.5, 2, 3, 4), screen_box=None)
        out = sel.grounding_block(s, [])
        self.assertIn('page="1,2,3,4"', out)
        self.assertNotIn(" box=", out)


class PrecedenceTest(unittest.TestCase):
    S = sel.Selection(count=2, page_box=(0, 0, 10, 10), screen_box=(0, 0, 10, 10))
    DEIXIS = {"id": "N3", "box": (500, 500, 600, 600)}

    def test_active_selection_beats_deictic(self):
        p = sel.precedence(self.S, self.DEIXIS)
        self.assertEqual(p["winner"], "selection")
        self.assertIs(p["target"], self.S)
        self.assertIn("selection", p["reason"])

    def test_deictic_wins_without_selection(self):
        p = sel.precedence(None, self.DEIXIS)
        self.assertEqual((p["winner"], p["target"]), ("deictic", self.DEIXIS))

    def test_empty_selection_does_not_win(self):
        empty = sel.Selection(count=0, page_box=(0, 0, 1, 1), screen_box=None)
        self.assertEqual(sel.precedence(empty, self.DEIXIS)["winner"], "deictic")

    def test_neither_gives_none(self):
        p = sel.precedence(None, None)
        self.assertEqual((p["winner"], p["target"]), ("none", None))
        self.assertTrue(p["reason"])


if __name__ == "__main__":
    unittest.main()
