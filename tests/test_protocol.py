"""Smriti Page Protocol v1 (skill/smriti/references/protocol.md): the <page>
map the daemon sends, the reader's target fields, the writer's @directives."""
# pyright: reportMissingImports=false

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import harness
import protocol

VIEW = {"name": "Test", "page": 56, "pages": 60, "pageId": "p", "scale": 1, "right": 0, "top": 0}


def line(x0, y0, x1, y1):
    return [(float(x0), float(y0)), (float(x1), float(y1))]


LAYERS = {
    "Layer 1": [line(96, 210, 1180, 215), line(96, 250, 900, 520),      # one group (near)
                line(700, 780, 1300, 1120),                              # a second, far away
                line(120, 1180, 1150, 1290),                             # the newest writing, flushed
                line(100, 2500, 900, 2600)],                             # below the screen
    "Monke": [line(96, 540, 1010, 720)],
    "Monke-greet": [line(900, 60, 1300, 120)],
}
NEW = [[(120, 1180), (1150, 1290)]]
MONKE = [{"type": "text", "box": (96, 540, 1011, 721), "turn": 3, "text": "Ohm law. V = IR."},
         {"id": "G1", "type": "graph", "box": (100, 760, 640, 1130), "turn": 4,
          "text": "y = sin(x) & <b> " + "x" * 80}]


def regions(xml: str) -> dict:
    return {r.get("id"): r for r in ET.fromstring(xml).iter("region")}


class PageMapTest(unittest.TestCase):
    def setUp(self):
        self.xml = protocol.page_map(VIEW, LAYERS, NEW, MONKE, [(1300, 1872)], None)
        self.r = regions(self.xml)

    def test_header_and_free_bands(self):
        self.assertEqual(self.xml.splitlines()[0],
                         '<page notebook="Test" page="57/60" screen="1404x1872" scrolled="no">')
        self.assertIn('<free box="0,1300,1404,1872"/>', self.xml)
        self.assertEqual(self.xml.splitlines()[-1], "</page>")

    def test_navy_groups_come_from_his_layers(self):
        self.assertEqual(sorted(self.r), ["F", "G1", "M1", "N1", "N2"])
        n1 = self.r["N1"]
        self.assertEqual((n1.get("owner"), n1.get("layer"), n1.get("strokes"), n1.get("age")),
                         ("navy", "Layer 1", "2", "old"))
        self.assertEqual(n1.get("box"), "96,210,1181,521")               # half-open
        self.assertEqual(self.r["N2"].get("box"), "700,780,1301,1121")
        self.assertIsNone(n1.text)                                       # the image is the truth

    def test_focus_is_the_newest_writing_once(self):
        f = self.r["F"]
        self.assertEqual((f.get("owner"), f.get("age"), f.get("focus"), f.get("strokes")),
                         ("navy", "new", "yes", "1"))
        self.assertEqual(f.get("box"), "120,1180,1151,1291")
        self.assertNotIn("120,1180", " ".join(r.get("box") for k, r in self.r.items() if k != "F"))

    def test_monke_regions_carry_type_turn_and_a_short_escaped_body(self):
        m, g = self.r["M1"], self.r["G1"]
        self.assertEqual((m.get("owner"), m.get("type"), m.get("turn"), m.text),
                         ("monke", "text", "3", "Ohm law. V = IR."))
        self.assertEqual(g.get("type"), "graph")
        self.assertTrue(g.text.startswith("y = sin(x) & <b> x"))
        self.assertLessEqual(len(g.text), 60)
        self.assertIn("&amp; &lt;b&gt;", self.xml)

    def test_regions_run_top_to_bottom(self):
        ids = [r.get("id") for r in ET.fromstring(self.xml).iter("region")]
        self.assertEqual(ids, ["N1", "M1", "G1", "N2", "F"])

    def test_off_screen_regions_are_dropped(self):
        xml = protocol.page_map(VIEW, LAYERS, NEW, MONKE + [{"box": (0, 2000, 500, 2400)}],
                                [], None)
        self.assertEqual(sorted(regions(xml)), ["F", "G1", "M1", "N1", "N2"])
        self.assertNotIn("2500", xml)

    def test_scrolled_and_registered_offset(self):
        v = dict(VIEW, top=-400)
        self.assertIn('scrolled="yes"', protocol.page_map(v, {}, NEW, [], [], None))
        v = dict(VIEW, ox=10, oy=-100)                                   # screen = page + offset
        r = regions(protocol.page_map(v, {"Layer 1": [line(100, 400, 300, 500)]}, [], [], [], None))
        self.assertEqual(r["N1"].get("box"), "110,300,311,401")

    def test_cap_of_twelve_regions(self):
        many = {"Layer 1": [line(60 + 330 * c, 60 + 150 * r, 200 + 330 * c, 100 + 150 * r)
                            for r in range(7) for c in range(4)]}        # 28 scattered groups
        r = regions(protocol.page_map(VIEW, many, [[(100, 1700), (400, 1750)]], MONKE, [], None))
        self.assertLessEqual(len(r), 12)
        self.assertLessEqual({"F", "M1", "G1"}, set(r))
        self.assertEqual(sum(int(x.get("strokes")) for k, x in r.items() if k.startswith("N")), 28)

    def test_no_view_no_layers_still_a_page(self):
        xml = protocol.page_map(None, None, NEW, [], [(0, 1100)], (100, 1150, 1200, 1300))
        self.assertEqual(xml.splitlines()[0], '<page screen="1404x1872">')
        self.assertEqual(regions(xml)["F"].get("box"), "100,1150,1200,1300")   # the selection box

    def test_region_lookup(self):
        self.assertIn('id="N2"', protocol.region(self.xml, "N2"))
        self.assertIn("Ohm law", protocol.region(self.xml, "M1"))
        self.assertIsNone(protocol.region(self.xml, "N9"))
        self.assertIsNone(protocol.region(self.xml, ".*"))

    def test_boxes_free_ids_and_region_strokes_round_trip(self):
        xml = protocol.page_map(VIEW, LAYERS, NEW, MONKE, [], None,
                                free_rects=[("E1", (900, 1300, 1380, 1700))])
        self.assertEqual(protocol.boxes(xml)["E1"], (900, 1300, 1380, 1700))
        n1 = protocol.boxes(xml)["N1"]
        strokes = protocol.region_strokes(VIEW, LAYERS, n1)
        self.assertEqual(len(strokes), 2)
        self.assertTrue(all(isinstance(x, int) and isinstance(y, int)
                            for stroke in strokes for x, y in stroke))


class DescribeReplyTest(unittest.TestCase):
    def test_type_and_body_from_the_logged_reply(self):
        d = protocol.describe_reply
        self.assertEqual(d("Ohm law.\n$$V = IR$$"), ("math", "Ohm law. $$V = IR$$"))
        self.assertEqual(d("See.\n$$\\begin{circuitikz}\\draw (0,0);\\end{circuitikz}$$"),
                         ("circuit", "See. [drawing]"))
        self.assertEqual(d("$$\\begin{tikzpicture}\\begin{axis}\\end{axis}\\end{tikzpicture}$$")[0],
                         "graph")
        self.assertEqual(d("<svg viewBox='0 0 4 3'><line/></svg>"), ("sketch", "[drawing]"))
        self.assertEqual(d("Plain words."), ("text", "Plain words."))


class DirectivesTest(unittest.TestCase):
    def test_leading_directives_become_dicts(self):
        d, rest = protocol.parse_directives(
            "@target N2\n@place beside N2\n@refine N2 as graph\n$$x$$\nDone.")
        self.assertEqual(d, [{"op": "target", "id": "N2"},
                             {"op": "place", "how": "beside", "id": "N2"},
                             {"op": "refine", "id": "N2", "as": "graph"}])
        self.assertEqual(rest, "$$x$$\nDone.")

    def test_place_forms(self):
        p = lambda t: protocol.parse_directives(t + "\nok")[0][0]
        self.assertEqual(p("@place free"), {"op": "place", "how": "free", "id": None})
        self.assertEqual(p("@place below F"), {"op": "place", "how": "below", "id": "F"})
        self.assertEqual(p("  @refine G1 as circuit  "), {"op": "refine", "id": "G1", "as": "circuit"})
        self.assertEqual(p("@place above N1"), {"op": "place", "how": "above", "id": "N1"})
        self.assertEqual(p("@place left-of N1"), {"op": "place", "how": "left-of", "id": "N1"})
        self.assertEqual(p("@place in E2"), {"op": "place", "how": "in", "id": "E2"})

    def test_blank_lines_between_and_unknown_directives_never_reach_the_page(self):
        d, rest = protocol.parse_directives("\n@target N1\n\n@dance wildly\nHello.")
        self.assertEqual(d, [{"op": "target", "id": "N1"}, {"op": "unknown", "raw": "@dance wildly"}])
        self.assertEqual(rest, "Hello.")

    def test_only_leading_lines_count(self):
        text = "Mail me.\n@target N1\nBye."
        self.assertEqual(protocol.parse_directives(text), ([], text))
        self.assertEqual(protocol.parse_directives("\nNo directives."), ([], "\nNo directives."))
        self.assertEqual(protocol.parse_directives("@target N1"), ([{"op": "target", "id": "N1"}], ""))


class ReadingTest(unittest.TestCase):
    def test_spp_keys_kept_with_defaults(self):
        r = harness.parse_reading('{"kind": "text", "text": "hi"}')
        self.assertEqual((r["target"], r["target_kind"], r["target_desc"]), ("F", "", ""))
        r = harness.parse_reading('{"kind": "text", "text": "#P neaten this", "intent": "refine",'
                                  ' "target": "N1", "target_kind": "graph",'
                                  ' "target_desc": "axes x 0..10, a sine"}')
        self.assertEqual((r["intent"], r["target"], r["target_kind"], r["target_desc"]),
                         ("refine", "N1", "graph", "axes x 0..10, a sine"))
        self.assertEqual(harness.parse_reading('{"text": "x", "target_kind": "banana"}')["target_kind"], "")

    def test_refine_is_confirmed(self):
        r = harness.parse_reading('{"kind": "text", "text": "x", "intent": "refine", "target": "N1"}')
        self.assertTrue(harness.needs_confirm(r, None))
        self.assertFalse(harness.needs_confirm(harness.parse_reading('{"kind": "text", "intent": "note"}'), None))

    def test_page_map_reaches_the_reader_whole(self):
        from PIL import Image
        seen = []
        page = "<page>" + "x" * 900 + "</page>"                          # longer than the ctx cut
        harness.read(Image.new("RGB", (40, 40), "white"), None,
                     lambda m: seen.extend(m) or "{}", context="earlier", page=page)
        self.assertTrue(seen[1]["content"][0]["text"].endswith(page))


class WiringTest(unittest.TestCase):
    """monke.py: directive lines never reach the page, on either inking path."""

    def test_streamer_skips_leading_directives(self):
        import contextlib
        from unittest.mock import MagicMock, patch
        import monke
        from ink import load_styles
        laid, layout, lamp = [], monke._layout, MagicMock()
        with (patch.object(monke, "lamp", lamp),
              patch.object(monke, "qml_eval", return_value="2"),
              patch.object(monke, "_same_page", return_value=True),
              patch.object(monke, "_on_monke", return_value=contextlib.nullcontext()),
              patch.object(monke, "_verify_ink"),
              patch.object(monke, "_shot", return_value=None),
              patch.object(monke.time, "sleep"),
              patch.object(monke, "_echo_mark"),
              patch.object(monke, "_layout",
                           lambda text, *a, **k: laid.append(text) or layout(text, *a, **k))):
            s = monke._Streamer([[(100, 200), (300, 200)]], load_styles()["cursive"],
                                300, 1800, "h", 3, None, lambda *a: None, None)
            s.feed("@target N1\n@refine N1 as text\nLine one.\nLine tw")
            first = lamp.call_count
            s.finish("@target N1\n@refine N1 as text\nLine one.\nLine two.\n@ not a directive here")
        self.assertEqual(first, 1)
        self.assertEqual(laid[:2], ["Line one.", "Line two."])
        self.assertFalse([t for t in laid if t.startswith("@t") or t.startswith("@r")])

    def test_whole_reply_path_strips_and_logs(self):
        import contextlib
        import io
        import monke
        page = protocol.page_map(VIEW, LAYERS, NEW, MONKE, [], None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            body = monke._spp_directives("@refine N1 as graph\n@place beside N9\n$$x$$\nDone.", page)
            same = monke._spp_directives("Just words.", page)
        self.assertEqual((body, same), ("$$x$$\nDone.", "Just words."))
        log = out.getvalue()
        self.assertEqual(log.count("[spp] directives:"), 1)
        self.assertIn("refine N1 as graph", log)
        self.assertIn("unknown id N9", log)

    def test_monke_regions_from_the_layer_and_the_session_log(self):
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch
        from PIL import Image
        import monke
        shot = Image.new("RGB", (1404, 1872), "white")
        shot.info.update(view=VIEW, layers={"Monke": [line(96, 540, 1010, 720)],
                                            "Layer 1": [line(96, 210, 1180, 215)]})
        with tempfile.TemporaryDirectory() as d, patch.dict(monke._TURN, {"view": VIEW}):
            ses = SimpleNamespace(dir=Path(d), graphs={})
            (Path(d) / "transcript.md").write_text(
                "---\nnotebook: t\n---\n\n## Navy (p57, 10:00)\nwhat is ohm law\n"
                "\n## Monke (p57, 10:00)\nOhm law.\n$$V = IR$$\n\n## Monke (p3, 10:01)\nElsewhere.\n",
                encoding="utf-8")
            one = monke._monke_regions(ses, shot)
            shot.info["layers"]["Monke"].append(line(96, 1400, 800, 1500))   # a group with no turn
            two = monke._monke_regions(ses, shot)
        self.assertEqual(one, [{"box": (96, 540, 1011, 721), "turn": 1, "type": "math",
                                "text": "Ohm law. $$V = IR$$"}])
        self.assertEqual(two, [{"box": (96, 540, 1011, 721)}, {"box": (96, 1400, 801, 1501)}])


if __name__ == "__main__":
    unittest.main()
