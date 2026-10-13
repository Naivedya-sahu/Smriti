"""host/selection.py ↔ host/monke.py turn wiring."""
# pyright: reportMissingImports=false

import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import monke
import selection
import vectors


class ReplySelectionWiring(unittest.TestCase):
    ROUTE = {"reader": "reader", "writer": "writer", "context": "stateless",
             "escalate": "", "turn": "split"}
    BOX = (100, 200, 500, 600)
    PROMPT = [[(700, 900), (800, 900)]]

    def setUp(self):
        selection.reset_cache()

    def frame(self, view=None, layers=None):
        img = Image.new("RGB", (1404, 1872), "white")
        img.info.update(view=view or {"pageId": "p1", "page": 0, "pages": 1, "scale": 1,
                                      "ox": 0, "oy": 0, "right": 0, "top": 0},
                        layers=layers or {"Layer 1": [[(100, 200), (500, 600)]]})
        return img

    def reply(self, selected=True, frame=None, strokes=None, focus_box=None, qml=None,
              real_layers=False):
        seen = {"reader_page": "", "reader_ctx": "", "writer": "", "writer_reading": None,
                "focus_box": None, "focus": None, "log": ""}
        ses = SimpleNamespace(graphs={}, pages={}, log_block=lambda *a: None,
                              log_turn=lambda *a: None)
        reading = {"kind": "text", "text": "what is this", "latex": "", "circuit": "",
                   "intent": "question", "summary": "", "target": "F", "target_kind": "",
                   "target_desc": ""}
        layers_fn = monke.harness.layers

        def layers(page, box, **kwargs):
            seen["focus_box"] = box
            if real_layers:
                return layers_fn(page, box, **kwargs)
            return Image.new("RGB", (20, 20), "white"), Image.new("RGB", (20, 20), "white")

        def read(*args):
            seen["reader_page"], seen["reader_ctx"], seen["focus"] = args[6], args[4], args[0]
            return reading

        def write(*args, **kwargs):
            seen["writer"] = args[0]
            seen["writer_reading"] = args[4]
            return "Answer."

        found = (selection.Selection(2, self.BOX, self.BOX, source="boundingRect")
                 if selected else None)
        reader = (patch.object(monke, "qml_eval", side_effect=qml) if qml else
                  patch.object(monke.selection, "read_selection", return_value=found))
        out = io.StringIO()
        with (patch.dict(monke._TURN, {}, clear=True),
              patch.object(monke, "marker"),
              patch.object(monke, "tablet_settings", return_value={}),
              patch.object(monke, "apply_settings", return_value=("auto", "", self.ROUTE)),
              patch.object(monke, "scribe_ocr", return_value=[]),
              patch.object(monke, "free_bands", return_value=[(1000, 1872)]),
              patch.object(monke.protocol, "page_map", return_value="<page/>"),
              patch.object(monke.harness, "layers", side_effect=layers),
              patch.object(monke.harness, "read", side_effect=read),
              patch.object(monke.harness, "repair_latex", side_effect=lambda text, _: text),
              patch.object(monke, "_write", side_effect=write),
              patch.object(monke, "_reply_top", return_value=900),
              patch.object(monke, "_same_page", return_value=True),
              patch.object(monke, "_place", return_value=[[(100, 1000), (200, 1000)]]),
              patch.object(monke, "_ink"),
              patch.object(monke, "notebook_context", return_value=""),
              patch.object(monke, "page_log", return_value=""),
              patch.object(monke, "_set_stage"),
              reader,
              contextlib.redirect_stdout(out)):
            monke._reply(strokes or self.PROMPT, {"pressure": 1}, 1, 1800, "tablet", [], 0,
                         ses=ses, cfg_ai={"som": False}, shot=frame or self.frame(),
                         focus_box=focus_box)
        seen["log"] = out.getvalue()
        return seen

    def test_selection_grounds_both_prompts_focuses_and_beats_regions(self):
        seen = self.reply(True)
        for text in (seen["reader_page"], seen["writer"]):
            self.assertIn("<selection", text)
            self.assertIn(selection.RULE, text)
        self.assertIn('image="attached"', seen["reader_page"])
        self.assertEqual(seen["focus_box"], self.BOX)
        self.assertEqual(seen["writer_reading"]["target"], "selection")

    def test_selection_log_line(self):
        seen = self.reply(True)
        self.assertIn("[selection] 2 items box=(100, 200, 500, 600) via boundingRect", seen["log"])

    def test_reader_input_carries_exactly_one_selection_block(self):
        seen = self.reply(True)
        self.assertEqual((seen["reader_ctx"] + seen["reader_page"]).count("<selection"), 1)

    def test_scrolled_view_selection_box_uses_registered_offset(self):
        # unscrolled fields say +234,+500; the prompt's own strokes register +234,-500
        view = {"pageId": "p1", "page": 0, "pages": 1, "scale": 1, "right": -234, "top": -500}
        frame = self.frame(view, {"Layer 1": [[(466, 1400), (566, 1460)]]})
        rect = selection.PROBES[0].js

        def qml(host, js, timeout=6):
            return {selection.COUNT_JS: "1", rect: "[0,900,300,200]"}.get(js, "undefined")
        seen = self.reply(frame=frame, strokes=[[(700, 900), (800, 960)]], qml=qml)
        (x0, y0), (x1, y1) = vectors.to_screen([(0, 900), (300, 1100)],
                                               dict(view, ox=234, oy=-500))
        self.assertEqual(seen["focus_box"], (x0, y0, x1, y1))
        self.assertIn(f"box=({x0}, {y0}, {x1}, {y1}) via boundingRect", seen["log"])

    def test_selected_ink_stays_a_region_and_prompt_box_is_navys_strokes(self):
        frame = self.frame(layers={"Layer 1": [[(100, 200), (500, 600)], *self.PROMPT]})
        with (patch.object(monke.regions, "build", wraps=monke.regions.build) as build,
              patch.object(monke.regions, "resolve", wraps=monke.regions.resolve) as resolve):
            seen = self.reply(True, frame=frame)
        self.assertEqual(build.call_args.kwargs["exclude_box"], (700, 900, 800, 900))
        self.assertEqual(resolve.call_args.kwargs["last_prompt_bbox"], (700, 900, 800, 900))
        regions_block = seen["reader_page"].split("<regions>")[1].split("</regions>")[0]
        self.assertIn('box="100,200,501,601"', regions_block)    # the selected graph (+1 px bbox)
        self.assertNotIn("700,900", regions_block)

    def test_explicit_focus_keeps_its_image_with_a_live_selection(self):
        seen = self.reply(True, focus_box=(300, 300, 400, 400))
        self.assertEqual(seen["focus_box"], (300, 300, 400, 400))
        self.assertEqual(seen["focus"].size, (20, 20))
        self.assertNotIn('image="attached"', seen["reader_page"])

    def test_selection_crop_goes_through_the_cleaning_path(self):
        with patch.object(monke.harness, "clean", wraps=monke.harness.clean) as clean:
            seen = self.reply(True, real_layers=True)
        margin = 60
        size = (self.BOX[2] - self.BOX[0] + 2 * margin, self.BOX[3] - self.BOX[1] + 2 * margin)
        self.assertIn(size, [c.args[0].size for c in clean.call_args_list])
        self.assertEqual(max(seen["focus"].size), 768)       # upscaled like any focus image

    def test_no_probe_keeps_the_existing_stroke_focus_and_no_selection_prompt(self):
        seen = self.reply(False)
        self.assertNotIn("<selection", seen["reader_page"])
        self.assertNotIn("<selection", seen["writer"])
        self.assertEqual(seen["focus_box"], (700, 900, 800, 900))
        self.assertEqual(seen["focus"].size, (20, 20))
        self.assertNotIn("[selection]", seen["log"])


class DaemonStart(unittest.TestCase):
    def test_run_forgets_the_session_probe_cache(self):
        selection._CACHE.update(probe="boundingRect", none=True)
        with (patch.object(monke, "_detach_stdin"),
              patch.object(monke, "_load_cfg", side_effect=KeyboardInterrupt),
              self.assertRaises(KeyboardInterrupt)):
            monke.run()
        self.assertIsNone(selection._CACHE["probe"])
        self.assertFalse(selection._CACHE["none"])

    def test_second_run_starts_with_clean_turn_and_page_state(self):
        monke._TURN.clear()
        monke._TURN.update(view={"pageId": "old"}, regions=["old"], offset=(9, 8), why="old")
        monke._PAGE["id"] = "old"
        with (patch.object(monke, "_detach_stdin"),
              patch.object(monke, "_load_cfg", side_effect=KeyboardInterrupt),
              self.assertRaises(KeyboardInterrupt)):
            monke.run()
        self.assertEqual(monke._TURN, {"view": None})
        self.assertEqual(monke._PAGE, {"id": None})


if __name__ == "__main__":
    unittest.main()
