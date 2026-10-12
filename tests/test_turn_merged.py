# pyright: reportMissingImports=false
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import brain
import harness
import monke
import skill


class MergedPromptTest(unittest.TestCase):
    def test_merged_system_contains_both_contracts(self):
        text = skill.merged_system("math")
        self.assertIn("First the JSON object", text)
        self.assertIn("kind", text)
        self.assertIn("math", text.lower())


class StreamSplitTest(unittest.TestCase):
    def call(self, chunks, **kwargs):
        def chat(messages, on_text=None):
            text = ""
            for chunk in chunks:
                text += chunk
                if on_text and on_text(text):
                    break
            return text
        return harness.read_and_reply(None, None, chat, **kwargs)

    def test_partial_json_then_reply_streams_only_tail(self):
        seen = []
        raw = '{"kind":"text","text":"hi","intent":"question"}\n\nAnswer {x}.'
        reading, reply = self.call([raw[:18], raw[18:51], raw[51:]], on_reply=seen.append)
        self.assertEqual(reading["text"], "hi")
        self.assertEqual(reply, "Answer {x}.")
        self.assertEqual(seen[-1], "Answer {x}.")

    def test_plot_stops_after_json(self):
        raw = '{"kind":"graph","text":"y=x","intent":"plot"}\n\nWASTE'
        stopped = []
        reading, reply = self.call([raw[:55], raw[55:]],
                                   stop_after=lambda r: stopped.append(r["kind"]) or True)
        self.assertEqual((reading["kind"], reply, stopped), ("graph", "", ["graph"]))

    def test_no_json_is_reading_with_empty_reply(self):
        reading, reply = self.call(["plain unreadable text"])
        self.assertEqual((reading["text"], reply), ("plain unreadable text", ""))


class TurnModeTest(unittest.TestCase):
    CFG = dict(brain.DEFAULTS, turn="split")

    def test_default_and_tablet_and_env_precedence(self):
        self.assertEqual(monke.apply_settings(self.CFG, {})[2]["turn"], "split")
        self.assertEqual(monke.apply_settings(self.CFG, {"turn": "merged"})[2]["turn"], "merged")
        with patch.dict(os.environ, SMRITI_TURN="merged"):
            self.assertEqual(monke.apply_settings(self.CFG, {"turn": "split"})[2]["turn"], "merged")

    def test_fallback_matrix(self):
        base = {"turn": "merged", "reader": "chatgpt:x", "writer": "chatgpt:x", "escalate": ""}
        self.assertEqual(monke.merged_fallback(base, None), "")
        self.assertIn("different", monke.merged_fallback(dict(base, writer="chatgpt:y"), None))
        self.assertIn("Claude", monke.merged_fallback(dict(base, reader="sonnet", writer="sonnet"), None))
        self.assertIn("escalation", monke.merged_fallback(dict(base, escalate="chatgpt:z"), None))
        self.assertIn("selection", monke.merged_fallback(base, "ask"))


if __name__ == "__main__":
    unittest.main()
