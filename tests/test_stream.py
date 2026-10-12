"""Streaming Claude replies: text reaches the caller as it is generated,
so ink can start before the whole reply exists (Navy 2026-09-30: live)."""
# pyright: reportMissingImports=false

import io
import tempfile
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import oracle

EVENTS = [{"type": "system", "subtype": "init"}] + [
    {"type": "stream_event", "event": {"type": "content_block_delta",
                                       "delta": {"type": "text_delta", "text": t}}}
    for t in ["Line", " one.", "\nThen", " line two."]] + [
    {"type": "result", "subtype": "success", "is_error": False,
     "result": "Line one.\nThen line two.", "session_id": "s-1",
     "usage": {"input_tokens": 10, "output_tokens": 7}, "total_cost_usd": 0}]


class FakeProc:
    def __init__(self, cmd, **k):
        self.cmd = cmd
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO("".join(json.dumps(e) + "\n" for e in EVENTS).encode())
        self.stderr = io.BytesIO(b"")
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class ClaudeStreamTest(unittest.TestCase):
    def test_partial_text_is_delivered_then_full_result_returned(self):
        seen = []
        cfg = {"claude_bin": "claude", "model": "sonnet", "config_dir": tempfile.mkdtemp(),
               "_on_text": seen.append}
        with patch.object(oracle.subprocess, "Popen", FakeProc) as P:
            text, sid = oracle.claude_chat("hi", cfg=cfg)
        self.assertEqual(seen, ["Line", "Line one.", "Line one.\nThen", "Line one.\nThen line two."])
        self.assertEqual((text, sid), ("Line one.\nThen line two.", "s-1"))
        self.assertEqual(oracle.LAST_USAGE["out"], 7)


class StreamUnitsTest(unittest.TestCase):
    def test_units_end_at_newlines_outside_blocks(self):
        import harness
        text = ("Line one.\nThen $$x_1$$ two.\n$$\\begin{circuitikz}\n\\draw (0,0);"
                "\n\\end{circuitikz}$$\n<svg>\n<line/>\n</svg>\nTail")
        units, used = harness.stream_units(text)
        self.assertEqual(units, ["Line one.", "Then $$x_1$$ two.",
                                 "$$\\begin{circuitikz}\n\\draw (0,0);\n\\end{circuitikz}$$",
                                 "<svg>\n<line/>\n</svg>"])
        self.assertEqual(text[used:], "Tail")          # incomplete: wait for more

    def test_open_block_waits(self):
        import harness
        units, used = harness.stream_units("Formula:\n$$f_c = \\frac{1}{2")
        self.assertEqual((units, used), (["Formula:"], len("Formula:\n")))


class StreamerTest(unittest.TestCase):
    """Units ink while the reply streams; one layer session; guard first."""
    def run_stream(self, same_page=True):
        import contextlib
        from unittest.mock import MagicMock
        import monke
        lamp = MagicMock()
        with (patch.object(monke, "lamp", lamp),
              patch.object(monke, "qml_eval", return_value="2"),
              patch.object(monke, "_same_page", return_value=same_page),
              patch.object(monke, "_on_monke", return_value=contextlib.nullcontext()) as on,
              patch.object(monke, "_verify_ink"),
              patch.object(monke, "_shot", return_value=None),
              patch.object(monke.time, "sleep"),
              patch.object(monke, "_echo_mark")):
            from ink import load_styles
            s = monke._Streamer([[(100, 200), (300, 200)]], load_styles()["cursive"],
                                300, 1800, "h", 3, None, lambda *a: None, None)
            s.feed("Line one.\nLine tw")
            after_first = lamp.call_count
            floor = s.finish("Line one.\nLine two.")
        return s, after_first, lamp.call_count, on.call_count, floor

    def test_first_line_inks_before_the_reply_is_complete(self):
        s, first, total, layer_sessions, floor = self.run_stream()
        self.assertEqual((first, total, layer_sessions), (1, 2, 1))
        self.assertTrue(s.started)
        self.assertGreater(floor, 300)

    def test_page_turned_away_inks_nothing(self):
        s, first, total, layer_sessions, floor = self.run_stream(same_page=False)
        self.assertEqual((total, layer_sessions), (0, 0))
        self.assertTrue(s.blocked)


class ReaderStopTest(unittest.TestCase):
    """The reader stops as soon as its JSON is complete (run 2: JSON then a
    worked solution + SVG, 1078 output tokens / 24 s for ~150 needed)."""

    def test_json_complete(self):
        import harness
        self.assertFalse(harness.json_complete('{"kind": "math", "text": "V'))
        self.assertTrue(harness.json_complete('{"kind": "math"}\n---\nsolution'))
        self.assertTrue(harness.json_complete('```json\n{"text": "x}y"}'))
        self.assertFalse(harness.json_complete("no json yet"))

    def test_stream_stops_when_callback_says_done(self):
        events = [{"type": "stream_event", "event": {"type": "content_block_delta",
                   "delta": {"type": "text_delta", "text": t}}}
                  for t in ['{"kind":', ' "math"}', "\nlong worked solution"]]
        killed = []

        class Proc(FakeProc):
            def __init__(self, cmd, **k):
                super().__init__(cmd, **k)
                self.stdout = io.BytesIO("".join(json.dumps(e) + "\n" for e in events).encode())

            def kill(self):
                killed.append(True)
        seen = []

        def on_text(t):
            seen.append(t)
            return t.endswith("}")
        cfg = {"claude_bin": "claude", "model": "sonnet",
               "config_dir": tempfile.mkdtemp(), "_on_text": on_text}
        with patch.object(oracle.subprocess, "Popen", Proc):
            text, sid = oracle.claude_chat("read", cfg=cfg)
        self.assertEqual(text, '{"kind": "math"}')
        self.assertEqual(len(seen), 2)            # never read the solution
        self.assertTrue(killed)
        self.assertGreater(oracle.LAST_USAGE["out"], 0)


if __name__ == "__main__":
    unittest.main()

