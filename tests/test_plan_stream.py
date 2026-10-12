"""plan_stream + plan transport: the streamed smriti_plan JSON yields each
block the moment it closes (chunk boundaries anywhere, escaped quotes,
truncation); brain asks for the plan only on the ChatGPT direct writer call
and falls back to the text grammar for the process when the backend rejects
the schema; monke's streamer inks plan blocks through the executors."""
# pyright: reportMissingImports=false

import base64
import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import brain
import plan_schema as ps
import plan_stream

FIXTURE = ROOT / "tests" / "fixtures" / "ac_rlc_plan.json"
TRICKY = {"v": 1, "outline": {"text_lines": 2, "figures": []}, "place": "below N2", "blocks": [
    {"type": "text", "role": "body", "text": 'He said "hi" {not a block} [x] \\ end'},
    {"type": "latex", "role": "answer", "tex": "\\frac{a}{b} = \"q\" \\\\ }]"},
    {"type": "text", "role": "body", "text": "\u03c9 \u2220 ok"}]}


def _dicts(blocks):
    return [b.to_dict() for b in blocks]


def _scan(text, sizes):
    s, out, i, k = plan_stream.Scanner(), [], 0, 0
    while i < len(text):
        n = sizes[k % len(sizes)]
        out += s.feed(text[i:i + n])
        i, k = i + n, k + 1
    s.close()
    return s, out


class ScannerTest(unittest.TestCase):
    def test_chunked_fixture_equals_whole_validate(self):
        text = FIXTURE.read_text(encoding="utf-8")
        want = _dicts(ps.validate(text)[0])
        for sizes in ([1], [7], [13, 2, 40], [len(text)]):
            s, got = _scan(text, sizes)
            self.assertEqual(_dicts(got), want, sizes)
            self.assertEqual(s.problems, [])
            self.assertEqual(s.header["outline"]["text_lines"], 3)
            self.assertIsNone(s.place)

    def test_every_split_point_with_escapes_and_brackets_in_strings(self):
        text = json.dumps(TRICKY)
        want = _dicts(ps.validate(TRICKY)[0])
        for cut in range(1, len(text)):
            s = plan_stream.Scanner()
            got = s.feed(text[:cut]) + s.feed(text[cut:])
            self.assertEqual(_dicts(got), want, cut)
            self.assertEqual(s.place, "below N2", cut)
        self.assertEqual(want[0]["src"], 'He said "hi" {not a block} [x] \\ end')

    def test_block_inks_the_moment_it_closes(self):
        text = json.dumps(TRICKY)
        end0 = text.index("}", text.index('end"')) + 1               # block 0's closing brace
        s = plan_stream.Scanner()
        first = s.feed(text[:end0])
        self.assertEqual([b.kind for b in first], ["text"])
        self.assertEqual(s.feed(text[end0:end0 + 1]), [])           # the comma: nothing new
        self.assertEqual(len(s.feed(text[end0 + 1:])), 2)

    def test_truncated_plan_keeps_closed_blocks(self):
        text = FIXTURE.read_text(encoding="utf-8")
        cut = text.index('"type": "graph"')                          # mid-plan
        s = plan_stream.Scanner()
        got = s.feed(text[:cut + 30])
        self.assertEqual(len(got), 5)
        self.assertEqual(s.close(), ["plan truncated: 5 closed blocks kept"])

    def test_fence_bare_array_trailing_junk_and_bad_element(self):
        s, got = _scan('```json\n[{"type":"text","role":"body","text":"a"}]\n```', [3])
        self.assertEqual([b.src for b in got], ["a"])
        s = plan_stream.Scanner()
        got = s.feed('{"blocks":[{"type":"text","text":"a"},{"type":"text","text":"b"}]} tail {"x":1}')
        self.assertEqual([b.src for b in got], ["a", "b"])
        self.assertEqual(s.close(), [])

    def test_labels_avoid_session_graphs_across_chunks(self):
        g = {"type": "graph", "id": "G1", "plot": {"type": "function", "curves": [{"expr": "t", "label": None}]}}
        s = plan_stream.Scanner(taken={"G1"})
        text = json.dumps({"blocks": [g, g]})
        got = s.feed(text[:40]) + s.feed(text[40:])
        self.assertEqual([b.label for b in got], ["G2", "G3"])

    def test_text_grammar_is_not_a_plan(self):
        self.assertFalse(ps.is_plan("Ohm's law.\n$$V=IR$$"))
        s = plan_stream.Scanner()
        self.assertEqual(s.feed("plain prose, no braces"), [])
        self.assertEqual(s.close(), ["no JSON plan in the reply"])


def _jwt(exp):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': exp, 'https://api.openai.com/auth': {'chatgpt_account_id': 'a'}})}.sig"


class Codex(BaseHTTPRequestHandler):
    """Fake /codex/responses: 400 on text.format while `reject`, else SSE."""
    seen: list = []
    reject = False
    deltas = ['{"v":1,"outline":{"text_lines":1,"figures":[]},"place":null,"blocks":[',
              '{"type":"text","role":"body","text":"hi"}', "]}"]

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Codex.seen.append(body)
        if Codex.reject and "text" in body:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"detail":"Unsupported parameter: text.format"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = Codex.deltas if "text" in body else ["plain ", "text"]
        for d in chunks:
            ev = {"type": "response.output_text.delta", "delta": d}
            self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
        done = {"type": "response.completed", "response": {"usage": {"input_tokens": 3, "output_tokens": 4}}}
        self.wfile.write(f"data: {json.dumps(done)}\n\n".encode())

    def log_message(self, *a):
        pass


class BrainPlanTest(unittest.TestCase):
    def setUp(self):
        Codex.seen, Codex.reject = [], False
        brain.PLAN_OFF.clear()
        self.addCleanup(brain.PLAN_OFF.clear)
        self.srv = HTTPServer(("127.0.0.1", 0), Codex)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        env = patch.dict(os.environ, SMRITI_HOME=tempfile.mkdtemp())
        env.start()
        self.addCleanup(env.stop)
        brain.auth.put("chatgpt", {"access_token": _jwt(time.time() + 3600), "refresh_token": "R"})
        self.cfg = dict(brain.DEFAULTS, provider="chatgpt", model="gpt-5.6-luna", transport="direct",
                        timeout=10, chatgpt_url=f"http://127.0.0.1:{self.srv.server_port}/codex")

    def call(self, role="write", **cfg):
        got = []
        text = brain.chat([{"role": "system", "content": "SYS"}, {"role": "user", "content": "q"}],
                          role=role, on_text=got.append, cfg=dict(self.cfg, **cfg))
        return text, got

    def test_writer_asks_for_the_plan_and_deltas_stream(self):
        text, got = self.call()
        body = Codex.seen[0]
        self.assertEqual(body["text"], {"format": ps.FORMAT})
        self.assertTrue(body["instructions"].endswith(ps.SWITCH))
        self.assertEqual(got, Codex.deltas)
        self.assertEqual([b.src for b in ps.validate(text)[0]], ["hi"])
        self.assertEqual(brain.LAST["plan"], "json")

    def test_other_roles_transports_and_config_stay_text(self):
        self.assertEqual(self.call(role="read")[0], "plain text")
        self.assertEqual(self.call(plan="text")[0], "plain text")
        self.assertNotIn("text", Codex.seen[0])
        self.assertEqual(brain.LAST["plan"], "text")
        self.assertEqual(brain.plan_mode(dict(self.cfg, transport="cli")), "text")
        self.assertEqual(brain.plan_mode({}), "json")              # code default

    def test_schema_rejected_falls_back_to_text_for_the_process(self):
        Codex.reject = True
        with patch("builtins.print") as said:
            text, _ = self.call()
            self.assertEqual(text, "plain text")
            self.assertEqual(self.call()[0], "plain text")
        self.assertEqual(["text" in b for b in Codex.seen], [True, False, False])
        self.assertEqual(said.call_count, 1)                          # logged once
        self.assertIn("400", brain.PLAN_OFF[0])
        self.assertEqual(brain.LAST["plan"], "text")

    def test_400_that_is_not_the_schema_still_raises(self):
        class Always400(Codex):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"detail":"image too large"}')
        self.srv.RequestHandlerClass = Always400
        with self.assertRaisesRegex(brain.BrainError, "400"):
            self.call()
        self.assertEqual(brain.PLAN_OFF, [])                          # plan mode kept


class StreamerPlanTest(unittest.TestCase):
    """monke._Streamer inks plan blocks as they close: graph -> plots.render
    (fake) registered in ses.graphs; the rest -> _layout with role colours."""

    def test_plan_blocks_ink_in_order_with_graph_registration(self):
        import monke
        lamp, laid = MagicMock(), []

        def layout(text, style, y, log=None, color=None):
            laid.append((text.split("\n")[0][:20], color))
            return [monke.Tinted([(100, y), (200, y + 30)], color or 0)]
        fake = types.ModuleType("plots")
        fake.validate = lambda spec: None
        fake.render = lambda spec, label=None: types.SimpleNamespace(
            label=label, curves=[c["expr"] for c in spec["curves"]], x=0, y=0, max_w=0,
            color=lambda i: [0, 7][i])
        batches = lambda g, strokes_fn=None: [([[(100, g.y), (300, g.y + 80)]], 0),
                                              ([[(120, g.y + 10), (280, g.y + 60)]], 7)]
        ses = types.SimpleNamespace(graphs={"G1": object()})
        text = json.dumps({"v": 1, "outline": {"text_lines": 1, "figures": ["graph"]}, "place": None,
                           "blocks": [{"type": "text", "role": "body", "text": "Line one."},
                                      {"type": "graph", "id": "G1", "plot": {
                                          "type": "function", "curves": [
                                              {"expr": "sin(t)", "label": "a"},
                                              {"expr": "cos(t)", "label": "b"}]}},
                                      {"type": "latex", "role": "answer", "tex": "Z = 10"}]})
        with (patch.object(monke, "lamp", lamp),
              patch.object(monke, "qml_eval", return_value="2"),
              patch.object(monke, "pen_color"),
              patch.object(monke, "_same_page", return_value=True),
              patch.object(monke, "_on_monke", return_value=contextlib.nullcontext()),
              patch.object(monke, "_verify_ink"),
              patch.object(monke, "_shot", return_value=None),
              patch.object(monke.time, "sleep"),
              patch.object(monke, "_echo_mark"),
              patch.object(monke, "_layout", layout),
              patch.object(monke.render_plan, "graph_batches", batches),
              patch.dict(sys.modules, plots=fake),
              patch("builtins.print")):
            from ink import load_styles
            s = monke._Streamer([[(100, 200), (300, 200)]], load_styles()["cursive"],
                                300, 1800, "h", 3, None, lambda *a: None, None, ses=ses)
            cut = text.index("}", text.index("Line one.")) + 1
            s.feed(text[:cut])
            after_first = lamp.call_count
            s.feed(text)
            floor = s.finish(text)
        self.assertEqual(after_first, 1)                              # block 0 inked mid-stream
        self.assertEqual(laid, [("Line one.", 0), ("$$Z = 10$$", 7)])
        self.assertEqual(sorted(ses.graphs), ["G1", "G2"])            # G1 taken: plan graph -> G2
        g = ses.graphs["G2"]
        self.assertEqual((g.label, g.curves, ses.graph), ("G2", ["sin(t)", "cos(t)"], g))
        self.assertGreater(g.y, 300)
        self.assertGreater(floor, 300)
        self.assertGreaterEqual(lamp.call_count, 4)                   # text, graph x2 pens, answer

    def test_text_grammar_still_streams_line_by_line(self):
        import monke
        lamp = MagicMock()
        with (patch.object(monke, "lamp", lamp),
              patch.object(monke, "qml_eval", return_value="2"),
              patch.object(monke, "_same_page", return_value=True),
              patch.object(monke, "_on_monke", return_value=contextlib.nullcontext()),
              patch.object(monke, "_verify_ink"),
              patch.object(monke, "_shot", return_value=None),
              patch.object(monke.time, "sleep"),
              patch.object(monke, "_echo_mark"),
              patch("builtins.print")):
            from ink import load_styles
            s = monke._Streamer([[(100, 200), (300, 200)]], load_styles()["cursive"],
                                300, 1800, "h", 3, None, lambda *a: None, None,
                                ses=types.SimpleNamespace(graphs={}))
            s.feed("Line one.\nLine tw")
            first = lamp.call_count
            floor = s.finish("Line one.\nLine two.")
        self.assertEqual((s.plan, first, lamp.call_count), (False, 1, 2))
        self.assertGreater(floor, 300)


if __name__ == "__main__":
    unittest.main()
