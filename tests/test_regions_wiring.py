"""host/regions.py ↔ host/monke.py semantic-region wiring."""
# pyright: reportMissingImports=false

import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import regions
import monke


class Coordinates(unittest.TestCase):
    def test_registered_offset_scale_two_round_trips_exactly(self):
        view = {"pageId": "p1", "ox": 10, "oy": 20, "scale": 2}
        page_box = (100, 200, 300, 400)
        self.assertEqual(regions.to_page_box(regions.to_screen_box(page_box, view), view), page_box)

    def test_registered_offset_scale_one_is_unchanged(self):
        view = {"pageId": "p1", "ox": 10, "oy": 20, "scale": 1}
        page_box = (100, 200, 300, 400)
        self.assertEqual(regions.to_screen_box(page_box, view), (110, 220, 310, 420))
        self.assertEqual(regions.to_page_box((110, 220, 310, 420), view), page_box)

    def test_field_view_scale_two_round_trips_exactly(self):
        view = {"pageId": "p1", "right": -702, "top": -300, "scale": 2}
        page_box = (100, 200, 300, 400)
        self.assertEqual(regions.to_page_box(regions.to_screen_box(page_box, view), view), page_box)


class MonkeWiring(unittest.TestCase):
    def test_spp_accepts_semantic_r_target(self):
        semantic = regions.build([[(100, 100), (300, 100)]], [])
        out = StringIO()
        with redirect_stdout(out):
            body = monke._spp_directives("@target R1\nAnswer.", "", semantic)
        self.assertEqual(body, "Answer.")
        self.assertNotIn("unknown id R1", out.getvalue())

    def test_turn_start_keeps_prior_per_page_and_resets_on_page_change(self):
        first = regions.build([[(100, 100), (300, 100)]], [])
        monke._TURN.clear()
        monke._TURN.update(regions=first, regions_page="p1")
        monke._turn_start(type("Shot", (), {"info": {"view": {"pageId": "p1"}, "layers": {}}})(), [], "h")
        self.assertEqual(monke._TURN["regions"], first)
        monke._turn_start(type("Shot", (), {"info": {"view": {"pageId": "p2"}, "layers": {}}})(), [], "h")
        self.assertEqual(monke._TURN["regions"], [])

    def test_refine_source_uses_registered_view(self):
        raw = {"pageId": "p1", "right": -702, "top": -600, "scale": 1}
        registered = dict(raw, ox=0, oy=-600)
        shot = type("Shot", (), {"info": {"view": raw,
                                               "layers": {"Layer 1": [[(100, 1000), (300, 1100)]]}}})()
        seen = {}
        region_strokes = monke.protocol.region_strokes

        def source(view, layers, box):
            seen["view"] = view
            seen["strokes"] = region_strokes(view, layers, box)
            return []

        with patch.object(monke.protocol, "region_strokes", side_effect=source):
            self.assertIsNone(monke._refine({"target": "N1"},
                                             '<page><region id="N1" box="100,400,301,501"/></page>',
                                             shot, SimpleNamespace(), "h", 1, 0, 1800, view=registered))
        self.assertIs(seen["view"], registered)
        self.assertEqual(seen["strokes"], [[(100, 400), (300, 500)]])


class ReplyRegionsWiring(unittest.TestCase):
    """Semantic regions through the actual split-turn _reply path."""

    ROUTE = {"reader": "reader", "writer": "writer", "context": "stateless",
             "escalate": "", "turn": "split"}

    def frame(self, page_id="p1", layers=None, view=None):
        view = view or {"pageId": page_id, "page": 0, "pages": 1, "scale": 1,
                        "ox": 0, "oy": 0, "right": 0, "top": 0}
        img = Image.new("RGB", (1404, 1872), "white")
        img.info.update(view=view, layers=layers or {})
        return img

    def reply(self, frame, reading=None, monke_regions=None, capture_views=False, strokes=None):
        reading = {"kind": "text", "text": "explain topic", "latex": "", "circuit": "",
                   "intent": "question", "summary": "", "target": "F", "target_kind": "",
                   "target_desc": "", **(reading or {})}
        seen = {"reader_pages": [], "writer_prompts": [], "writer_readings": []}
        ses = SimpleNamespace(graphs={}, pages={}, log_block=lambda *a: None,
                              log_turn=lambda *a: None)

        def read(*args):
            seen["reader_pages"].append(args[6])
            return reading

        def write(*args, **kwargs):
            seen["writer_prompts"].append(args[0])
            seen["writer_readings"].append(args[4])
            return "Answer."

        page_map = monke.protocol.page_map
        monke_regions_fn = monke._monke_regions
        def map_page(*args, **kwargs):
            seen["page_map_view"] = args[0]
            seen["page_map"] = page_map(*args, **kwargs)
            return seen["page_map"]
        def monke_map(*args):
            seen["monke_regions_view"] = args[2] if len(args) > 2 else None
            return monke_regions_fn(*args) if capture_views else monke_regions or []

        with (patch.object(monke, "marker"),
              patch.object(monke, "tablet_settings", return_value={}),
              patch.object(monke, "apply_settings", return_value=("auto", "", self.ROUTE)),
              patch.object(monke, "scribe_ocr", return_value=[]),
              patch.object(monke, "free_bands", return_value=[(1000, 1872)]),
              patch.object(monke, "_monke_regions", side_effect=monke_map),
              patch.object(monke.protocol, "page_map", side_effect=map_page if capture_views else None,
                           return_value=None if capture_views else "<page/>"),
              patch.object(monke.harness, "layers", return_value=(None, None)),
              patch.object(monke.harness, "read", side_effect=read),
              patch.object(monke.harness, "repair_latex", side_effect=lambda text, _: text),
              patch.object(monke, "_write", side_effect=write),
              patch.object(monke, "_reply_top", return_value=900),
              patch.object(monke, "_same_page", return_value=True),
              patch.object(monke, "_place", return_value=[[(100, 1000), (200, 1000)]]),
              patch.object(monke, "_ink"),
              patch.object(monke, "notebook_context", return_value=""),
              patch.object(monke, "page_log", return_value=""),
              patch.object(monke, "_set_stage")):
            monke._reply(strokes or [[(500, 700), (650, 700)]], {"pressure": 1}, 1, 1800, "tablet", [], 0,
                         ses=ses, cfg_ai={"som": False}, shot=frame)
        return seen

    def test_reply_builds_once_per_turn_keeps_ids_and_resets_on_new_page(self):
        layers = {"Layer 1": [[(100, 100), (120, 100)], [(800, 100), (820, 100)]]}
        with (patch.dict(monke._TURN, {}, clear=True),
              patch.object(regions, "build", wraps=regions.build) as build):
            self.reply(self.frame("p1", layers))
            first = list(monke._TURN["regions"])
            self.reply(self.frame("p1", layers))
            second = list(monke._TURN["regions"])
            self.reply(self.frame("p2", layers))
            third = list(monke._TURN["regions"])
        self.assertEqual(build.call_count, 3)
        self.assertEqual(build.call_args_list[0].kwargs["prior"], [])
        self.assertEqual(build.call_args_list[1].kwargs["prior"], first)
        self.assertEqual(build.call_args_list[2].kwargs["prior"], [])
        self.assertEqual([r.id for r in second], [r.id for r in first])
        self.assertEqual([r.id for r in third], ["R1", "R2"])

    def test_reply_converts_registered_scrolled_monke_boxes_before_build(self):
        view = {"pageId": "p1", "page": 0, "pages": 1, "scale": 2,
                "ox": 10, "oy": 20, "right": -999, "top": -500}
        monke_box = {"box": (220, 440, 620, 840), "type": "circuit"}
        with (patch.dict(monke._TURN, {}, clear=True),
              patch.object(regions, "build", wraps=regions.build) as build):
            self.reply(self.frame(view=view), monke_regions=[monke_box])
        self.assertEqual(build.call_args.args[1][0]["box"], (100, 200, 300, 400))

    def test_reply_uses_one_registered_view_for_page_map_and_monke_regions(self):
        raw = {"pageId": "p1", "page": 0, "pages": 1, "scale": 1, "right": -702, "top": -600}
        layers = {"Layer 1": [[(100, 1000), (300, 1100)], [(500, 900), (600, 900)]],
                  "Monke": [[(700, 1100), (900, 1200)]]}
        with patch.dict(monke._TURN, {}, clear=True):
            seen = self.reply(self.frame(view=raw, layers=layers),
                              capture_views=True, strokes=[[(500, 300), (600, 300)]])
        registered = seen["page_map_view"]
        self.assertEqual((registered["ox"], registered["oy"]), (0, -600))
        self.assertIs(seen["monke_regions_view"], registered)
        boxes = monke.protocol.boxes(seen["page_map"])
        self.assertEqual(boxes["N1"], (100, 400, 301, 501))
        self.assertEqual(boxes["M1"], (700, 500, 901, 601))

    def test_reply_keeps_explicit_reader_target_over_confident_resolve(self):
        semantic = [regions.Region("R1", "graph", "monke", (100, 100, 300, 300), 1, label="G1"),
                    regions.Region("R2", "circuit", "navy", (400, 100, 650, 300), 4)]
        self.assertGreaterEqual(regions.resolve("the circuit in this area", semantic,
                                                last_prompt_bbox=(500, 700, 650, 700))[1], .7)
        with (patch.dict(monke._TURN, {}, clear=True),
              patch.object(regions, "build", return_value=semantic)):
            seen = self.reply(self.frame(), {"text": "the circuit in this area", "target": "G1"})
        self.assertEqual(seen["writer_readings"][0]["target"], "G1")

    def test_reply_logs_ambiguous_circuits_once_without_target(self):
        semantic = [regions.Region("R1", "circuit", "navy", (100, 100, 300, 300), 4),
                    regions.Region("R2", "circuit", "navy", (700, 100, 900, 300), 4)]
        out = StringIO()
        with (patch.dict(monke._TURN, {}, clear=True),
              patch.object(regions, "build", return_value=semantic),
              redirect_stdout(out)):
            seen = self.reply(self.frame(), {"text": "the circuit in this area"})
        self.assertEqual(seen["writer_readings"][0]["target"], "F")
        self.assertEqual(out.getvalue().count("[regions] ambiguous"), 1)

    def test_reply_sets_confident_resolved_target_and_logs_it(self):
        semantic = [regions.Region("R1", "circuit", "navy", (100, 100, 300, 300), 4,
                                   desc="battery loop")]
        out = StringIO()
        with (patch.dict(monke._TURN, {}, clear=True),
              patch.object(regions, "build", return_value=semantic),
              redirect_stdout(out)):
            seen = self.reply(self.frame(), {"text": "the circuit in this area"})
        reading = seen["writer_readings"][0]
        self.assertEqual((reading["target"], reading["target_kind"], reading["target_desc"]),
                         ("R1", "circuit", "battery loop"))
        self.assertIn("[regions] target R1 circuit", out.getvalue())

    def test_reply_resolves_target_hints_it_and_keeps_regions_under_budget(self):
        semantic = [regions.Region(f"R{i}", "circuit" if i == 1 else "text", "navy",
                                   (i * 100, 100, i * 100 + 60, 160), 1,
                                   desc="old description " * 5)
                    for i in range(1, 13)]
        out = StringIO()
        with (patch.dict(monke._TURN, {}, clear=True),
              patch.object(regions, "build", return_value=semantic),
              patch.object(regions, "hint", wraps=regions.hint) as hint,
              redirect_stdout(out)):
            seen = self.reply(self.frame(), {"text": "the circuit in this area", "target": "R1",
                                              "target_kind": "circuit", "target_desc": "resistor bridge"})
        hint.assert_called_once_with(semantic, "R1", "circuit", "resistor bridge")
        block = seen["writer_prompts"][0].split("<regions>", 1)[1].split("</regions>", 1)[0]
        self.assertIn('kind="circuit"', block)
        self.assertIn('desc="resistor bridge"', block)
        self.assertLessEqual(len("<regions>" + block + "</regions>") + 1, 700)


if __name__ == "__main__":
    unittest.main()
