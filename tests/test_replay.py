# pyright: reportMissingImports=false
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import harness
import replay


READING = json.dumps({"kind": "math", "text": "2+2", "latex": "2+2",
                      "circuit": "", "intent": "solve", "summary": "add"})


class WriterPromptTest(unittest.TestCase):
    def test_shared_builder_adds_page_and_target(self):
        reading = harness.parse_reading(json.dumps({"kind": "text", "text": "that",
                                                    "target": "N1", "target_desc": "old note"}))
        page = '<page>\n<region id="N1" owner="navy" box="1,2,3,4"/>\n</page>'
        prompt = harness.build_writer_prompt(reading, None, "room ", "rules", "auto",
                                             page=page, page_log="Earlier answer")
        self.assertIn("Earlier answer", prompt)
        self.assertIn(page, prompt)
        self.assertIn("Target region:", prompt)
        self.assertIn("old note", prompt)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.png = self.tmp / "page.png"
        img = Image.new("RGB", (240, 320), "white")
        ImageDraw.Draw(img).line((20, 40, 180, 40), fill="black", width=4)
        img.save(self.png)

    def test_fake_brain_builds_result_row_without_tablet(self):
        calls = []

        def fake(messages, **kwargs):
            calls.append(messages)
            return READING if len(calls) == 1 else "Four."

        row = replay.run_case(self.png, chat=fake, no_write=False,
                              variant="A", mode="split", repeat=1)
        required = {"page", "variant", "mode", "repeat", "reading", "reply",
                    "ms_read", "ms_write", "tokens_in", "tokens_out", "kind_ok",
                    "keyword_hits", "reply_words", "directives", "non_ascii_drops"}
        self.assertTrue(required <= row.keys())
        self.assertEqual((row["reply"], row["reading"]["kind"], len(calls)),
                         ("Four.", "math", 2))
        self.assertIn("Navy wrote", calls[1][-1]["content"])

    def test_box_recolours_selected_ink_red(self):
        focus, _ = replay.prepare_images(self.png, (10, 20, 200, 80))
        rgb = focus.convert("RGB")
        self.assertTrue(any(r > 150 and g < 100 and b < 100 for r, g, b in rgb.getdata()))

    def test_summary_reports_each_variant(self):
        rows = [{"variant": "A", "mode": "split", "ms_read": 10, "ms_write": 20,
                 "tokens_in": 5, "tokens_out": 2, "kind_ok": True,
                 "keyword_hits": 1.0, "word_cap_violation": False,
                 "non_ascii_drops": 0}]
        text = replay.summary_markdown(rows)
        self.assertIn("| A | split |", text)
        self.assertIn("kind", text.lower())


if __name__ == "__main__":
    unittest.main()
