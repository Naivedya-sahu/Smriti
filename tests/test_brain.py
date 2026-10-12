"""host/brain.py — the single entry for every model call (Navy 2026-10-10,
own-auth 2026-10-11). chatgpt transports: direct (Smriti's own ChatGPT login),
opt-in cli (`hermes chat`) / http (gateway). Claude Code CLI only when a claude
alias is picked. Fakes only: a Python script posing as `hermes`, local
http.servers posing as the backends. Every fixture pins provider+transport so
a DEFAULTS change can never reach the real network."""
# pyright: reportMissingImports=false

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import auth
import brain
import monke
import oracle

FAKE_HERMES = r'''
import json, os, sys, time
out, mode = os.environ["FAKE_OUT"], os.environ.get("FAKE_MODE", "ok")
argv = sys.argv[1:]
rec = {"argv": argv, "stdin": sys.stdin.read()}
if "--image" in argv:
    p = argv[argv.index("--image") + 1]
    rec["image_path"], rec["image"] = p, open(p, "rb").read().hex()
json.dump(rec, open(out, "w"))
emit = lambda o: print(json.dumps(o), flush=True)
emit({"type": "system", "subtype": "init", "model": "gpt-5.6-luna"})
if mode == "ok":
    emit({"type": "text", "text": "Hello"})
    emit({"type": "result", "exit_code": 0, "text": "Hello",
          "tokens": {"input": 1150, "output": 3}})
elif mode == "multi":
    for t in ("one ", "two ", "three"):
        emit({"type": "text", "text": t})
    emit({"type": "result", "exit_code": 0, "text": "one two three",
          "tokens": {"input": 10, "output": 6}})
elif mode == "error":
    emit({"type": "result", "exit_code": 1, "text": "rate limit hit " + "x" * 400,
          "tokens": {"input": 0, "output": 0}})
elif mode == "noresult":
    emit({"type": "text", "text": "half"})
elif mode == "hang":
    beat = out + ".beat"
    while True:
        open(beat, "a").write(".")
        time.sleep(0.1)
'''


class FakeHermesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        script = self.tmp / "hermes.py"
        script.write_text(FAKE_HERMES)
        self.out = self.tmp / "out.json"
        self.cfg = dict(brain.DEFAULTS, provider="chatgpt", model="gpt-5.6-luna", transport="cli",
                        hermes_bin=f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"',
                        timeout=20)
        self.env = patch.dict(os.environ, FAKE_OUT=str(self.out), FAKE_MODE="ok")
        self.env.start()
        self.addCleanup(self.env.stop)

    def mode(self, m):
        os.environ["FAKE_MODE"] = m

    def seen(self):
        return json.loads(self.out.read_text())

    msgs = [{"role": "system", "content": "You are Monke."},
            {"role": "user", "content": "What is 2+2?"}]


class CliTest(FakeHermesCase):
    def test_cli_closes_all_process_pipes(self):
        real_popen = subprocess.Popen
        seen = []

        def capture(*args, **kwargs):
            p = real_popen(*args, **kwargs)
            seen.append(p)
            return p

        with patch.object(brain.subprocess, "Popen", side_effect=capture):
            brain.chat(self.msgs, cfg=self.cfg)
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].stdin.closed)
        self.assertTrue(seen[0].stdout.closed)
        self.assertTrue(seen[0].stderr.closed)

    def test_argv_is_lean_one_turn_stream_json(self):
        brain.chat(self.msgs, cfg=self.cfg)
        a = self.seen()["argv"]
        for want in (["-p", "smriti"], ["-t", "none"], ["--max-turns", "1"],
                     ["--format", "stream-json"], ["--provider", "openai-codex"],
                     ["--reasoning", "none"], ["-m", "gpt-5.6-luna"],
                     ["--query-file", "-"]):
            i = a.index(want[0])
            self.assertEqual(a[i:i + 2], want, f"{want} in {a}")
        self.assertLess(a.index("-p"), a.index("chat"))         # profile is a global flag
        self.assertIn("-Q", a)
        self.assertNotIn("--image", a)

    def test_stdin_has_system_then_user(self):
        brain.chat(self.msgs, cfg=self.cfg)
        self.assertEqual(self.seen()["stdin"],
                         "SYSTEM:\nYou are Monke.\n\nUSER:\nWhat is 2+2?")

    def test_returns_result_text_and_parses_usage(self):
        out = brain.chat(self.msgs, role="write", cfg=self.cfg)
        self.assertEqual(out, "Hello")
        u = brain.last_usage()
        self.assertEqual((u["model"], u["in"], u["out"], u["cost_usd"], u["provider"]),
                         ("gpt-5.6-luna", 1150, 3, 0.0, "chatgpt"))
        self.assertGreaterEqual(u["ms"], 0)

    def test_streaming_calls_on_text_per_chunk_in_order(self):
        self.mode("multi")
        got = []
        out = brain.chat(self.msgs, on_text=got.append, cfg=self.cfg)
        self.assertEqual(got, ["one ", "two ", "three"])
        self.assertEqual(out, "one two three")

    def test_on_text_true_stops_early(self):
        self.mode("multi")
        out = brain.chat(self.msgs, on_text=lambda t: t.startswith("two"), cfg=self.cfg)
        self.assertEqual(out, "one two ")

    def test_model_setting_overrides_default(self):
        brain.chat(self.msgs, model="chatgpt:gpt-5.5", cfg=self.cfg)
        a = self.seen()["argv"]
        self.assertEqual(a[a.index("-m") + 1], "gpt-5.5")

    def test_error_result_raises_with_first_200_chars(self):
        self.mode("error")
        with self.assertRaises(brain.BrainError) as cm:
            brain.chat(self.msgs, cfg=self.cfg)
        msg = str(cm.exception)
        self.assertIn("rate limit hit", msg)
        self.assertLessEqual(len(msg), 200)

    def test_missing_result_raises(self):
        self.mode("noresult")
        with self.assertRaises(brain.BrainError):
            brain.chat(self.msgs, cfg=self.cfg)

    def test_timeout_kills_the_process_and_raises(self):
        self.mode("hang")
        t0 = time.time()
        with self.assertRaises(brain.BrainError) as cm:
            brain.chat(self.msgs, cfg=dict(self.cfg, timeout=1))
        self.assertLess(time.time() - t0, 10)
        self.assertIn("timed out", str(cm.exception))
        beat = Path(str(self.out) + ".beat")
        n = beat.stat().st_size
        time.sleep(0.6)
        self.assertEqual(beat.stat().st_size, n, "hermes still running after timeout")

    def test_first_image_goes_as_a_file_and_is_cleaned_up(self):
        png = b"\x89PNG-focus"
        brain.chat(self.msgs, images=[png, b"\x89PNG-overview"], cfg=self.cfg)
        s = self.seen()
        self.assertEqual(bytes.fromhex(s["image"]), png)
        self.assertEqual(s["argv"].count("--image"), 1)       # Hermes takes one
        self.assertFalse(Path(s["image_path"]).exists())

    def test_image_url_parts_in_messages_are_extracted(self):
        m = [self.msgs[0], oracle.image_msg(b"\x89PNG-part", "look")]
        brain.chat(m, cfg=self.cfg)
        s = self.seen()
        self.assertEqual(bytes.fromhex(s["image"]), b"\x89PNG-part")
        self.assertTrue(s["stdin"].endswith("USER:\nlook"))
        self.assertNotIn("base64", s["stdin"])


SSE = [": keepalive", "",
       'data: {"choices":[{"delta":{"content":"Hel"}}]}', "",
       'data: {"choices":[{"delta":{"content":"lo"}}]}', "",
       'data: {"choices":[],"usage":{"prompt_tokens":40,"completion_tokens":2}}', "",
       "data: [DONE]", ""]


class Gateway(BaseHTTPRequestHandler):
    seen: dict = {}
    status = 200

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        Gateway.seen = {"path": self.path, "auth": self.headers.get("Authorization"),
                        "body": json.loads(self.rfile.read(n))}
        self.send_response(Gateway.status)
        self.send_header("Content-Type", "text/event-stream" if Gateway.status == 200 else "text/plain")
        self.end_headers()
        if Gateway.status != 200:
            self.wfile.write(b"quota exhausted")
            return
        self.wfile.write(("\n".join(SSE) + "\n").encode())

    def log_message(self, *a):
        pass


class HttpTest(unittest.TestCase):
    def setUp(self):
        Gateway.status = 200
        self.srv = HTTPServer(("127.0.0.1", 0), Gateway)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        key = Path(tempfile.mkdtemp()) / "brain.key"
        key.write_text("sk-test\n")
        self.cfg = dict(brain.DEFAULTS, provider="chatgpt", model="gpt-5.6-luna",
                        transport="http", timeout=10, key_file=str(key),
                        url=f"http://127.0.0.1:{self.srv.server_port}/p/smriti/v1")

    def test_streams_sse_skips_comments_and_reads_usage(self):
        got = []
        out = brain.chat(FakeHermesCase.msgs, on_text=got.append, role="read", cfg=self.cfg)
        self.assertEqual((out, got), ("Hello", ["Hel", "lo"]))
        u = brain.last_usage()
        self.assertEqual((u["in"], u["out"], u["cost_usd"], u["provider"]), (40, 2, 0.0, "chatgpt"))

    def test_request_shape(self):
        brain.chat(FakeHermesCase.msgs, model="chatgpt:gpt-5.5", images=[b"\x89PNG"], cfg=self.cfg)
        s = Gateway.seen
        self.assertEqual(s["path"], "/p/smriti/v1/chat/completions")
        self.assertEqual(s["auth"], "Bearer sk-test")
        b = s["body"]
        self.assertEqual((b["model"], b["provider"], b["stream"]), ("gpt-5.5", "openai-codex", True))
        self.assertEqual(b["model_options"], {"reasoning_effort": "none"})
        parts = b["messages"][-1]["content"]
        self.assertTrue(any(p.get("type") == "image_url"
                            and p["image_url"]["url"].startswith("data:image/png;base64,")
                            for p in parts))

    def test_http_error_raises(self):
        Gateway.status = 429
        with self.assertRaises(brain.BrainError) as cm:
            brain.chat(FakeHermesCase.msgs, cfg=self.cfg)
        self.assertIn("429", str(cm.exception))

    def test_missing_key_file_raises(self):
        with self.assertRaises(brain.BrainError):
            brain.chat(FakeHermesCase.msgs, cfg=dict(self.cfg, key_file="/nonexistent/brain.key"))


def _jwt(exp: float, acct: str = "acct-1") -> str:
    import base64 as b
    enc = lambda o: b.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': exp, 'https://api.openai.com/auth': {'chatgpt_account_id': acct}})}.sig"


class Codex(BaseHTTPRequestHandler):
    """Fake chatgpt.com/backend-api/codex/responses (Responses SSE)."""
    seen: list = []
    status = [200]

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Codex.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        code = Codex.status.pop(0) if len(Codex.status) > 1 else Codex.status[0]
        if code != 200:
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b'{"detail":"nope"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for ev in ({"type": "response.created"},
                   {"type": "response.output_text.delta", "delta": "f_c "},
                   {"type": "response.output_text.delta", "delta": "= 159 Hz"},
                   {"type": "response.completed",
                    "response": {"model": "gpt-5.6-luna",
                                 "usage": {"input_tokens": 35, "output_tokens": 9}}}):
            self.wfile.write(f"event: x\ndata: {json.dumps(ev)}\n\n".encode())

    def log_message(self, *a):
        pass


class DirectTest(unittest.TestCase):
    """chatgpt direct: Codex backend with Smriti's OWN ChatGPT login (auth.py
    store under SMRITI_HOME); refreshed by auth near expiry or once on 401."""

    def setUp(self):
        Codex.seen, Codex.status = [], [200]
        self.srv = HTTPServer(("127.0.0.1", 0), Codex)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        env = patch.dict(os.environ, SMRITI_HOME=tempfile.mkdtemp())
        env.start()
        self.addCleanup(env.stop)
        self.write_token(_jwt(time.time() + 3600))
        self.refreshed = []
        self.cfg = dict(brain.DEFAULTS, provider="chatgpt", model="gpt-5.6-luna", transport="direct",
                        timeout=10, chatgpt_url=f"http://127.0.0.1:{self.srv.server_port}/codex")

    def write_token(self, tok):
        auth.put("chatgpt", {"access_token": tok, "refresh_token": "R-SECRET"})

    def call(self, **kw):
        def refresh():
            self.refreshed.append(1)
            self.write_token(_jwt(time.time() + 3600))
            return auth.get("chatgpt")["access_token"]
        with patch.object(auth, "refresh_chatgpt", refresh):
            return brain.chat([{"role": "system", "content": "Be short."},
                               {"role": "user", "content": "RC cutoff?"}], cfg=self.cfg, **kw)

    def test_streams_responses_sse_and_reads_usage(self):
        got = []
        self.assertEqual(self.call(on_text=got.append), "f_c = 159 Hz")
        self.assertEqual(got, ["f_c ", "= 159 Hz"])
        u = brain.last_usage()
        self.assertEqual((u["in"], u["out"], u["provider"], u["cost_usd"]), (35, 9, "chatgpt", 0.0))

    def test_early_stop_estimates_usage(self):
        self.assertEqual(self.call(on_text=lambda chunk: True), "f_c ")
        u = brain.last_usage()
        self.assertEqual((u["in"], u["out"], u["estimated"]), (5, 1, True))

    def test_request_shape(self):
        self.call(images=[b"\x89PNG"])
        r = Codex.seen[0]
        self.assertEqual(r["path"], "/codex")
        b = r["body"]
        self.assertEqual((b["model"], b["instructions"], b["stream"], b["store"]),
                         ("gpt-5.6-luna", "Be short.", True, False))
        self.assertEqual(b["reasoning"], {"effort": "none"})
        parts = b["input"][0]["content"]
        self.assertEqual(parts[0], {"type": "input_text", "text": "RC cutoff?"})
        self.assertTrue(parts[1]["image_url"].startswith("data:image/png;base64,"))
        h = {k.lower(): v for k, v in r["headers"].items()}
        self.assertTrue(h["authorization"].startswith("Bearer "))
        self.assertEqual(h["chatgpt-account-id"], "acct-1")
        self.assertNotIn("R-SECRET", json.dumps(r))      # refresh token never leaves

    def test_expired_token_is_refreshed_first(self):
        self.write_token(_jwt(time.time() - 10))
        self.assertEqual(self.call(), "f_c = 159 Hz")
        self.assertEqual(self.refreshed, [1])

    def test_401_refreshes_once_and_retries(self):
        Codex.status = [401, 200]
        self.assertEqual(self.call(), "f_c = 159 Hz")
        self.assertEqual((self.refreshed, len(Codex.seen)), ([1], 2))

    def test_second_401_raises(self):
        Codex.status = [401, 401, 401]
        with self.assertRaises(brain.BrainError):
            self.call()
        self.assertEqual(self.refreshed, [1])

    def test_http_error_raises_short(self):
        Codex.status = [429]
        with self.assertRaisesRegex(brain.BrainError, "429"):
            self.call()

    def test_missing_auth_raises(self):
        auth.forget("chatgpt")
        with self.assertRaisesRegex(brain.BrainError, "login"):
            self.call()

    def test_direct_is_the_default_transport(self):
        self.assertEqual(brain.DEFAULTS["transport"], "direct")


class RoutingTest(unittest.TestCase):
    D = dict(brain.DEFAULTS, provider="chatgpt", model="gpt-5.6-luna")

    def test_default_is_chatgpt_luna(self):
        for spec in (None, "", "local", "whatever"):
            self.assertEqual(brain.resolve(spec, self.D), ("chatgpt", "gpt-5.6-luna"))

    def test_chatgpt_setting_names_the_model(self):
        self.assertEqual(brain.resolve("chatgpt:gpt-5.5", self.D), ("chatgpt", "gpt-5.5"))

    def test_claude_alias_goes_to_claude(self):
        self.assertEqual(brain.resolve("haiku", self.D), ("claude", "haiku"))

    def test_chat_dispatches_by_provider(self):
        cfg = dict(self.D, transport="cli")
        with (patch.object(brain, "_cli", lambda *a, **k: ("via-hermes", {})),
              patch.object(oracle, "_claude_compat", lambda m, cfg: f"via-claude-{cfg['model']}")):
            self.assertEqual(brain.chat([], cfg=cfg), "via-hermes")
            self.assertEqual(brain.chat([], model="haiku", cfg=cfg), "via-claude-haiku")

    def test_apply_settings_routes_roles(self):
        monke.CLAUDE_CFG.update(models=["haiku", "sonnet", "opus"])
        d = monke.apply_settings(self.D, {})[2]
        self.assertEqual((d["reader"], d["writer"]), ("chatgpt:gpt-5.6-luna",) * 2)
        r = monke.apply_settings(self.D, {"reader": "haiku", "writer": "chatgpt:gpt-5.5"})[2]
        self.assertEqual((r["reader"], r["writer"]), ("haiku", "chatgpt:gpt-5.5"))
        e = monke.apply_settings(self.D, {"escalate": "sonnet", "escalate_next": True})[2]
        self.assertEqual(e["escalate"], "sonnet")


class CloudChatTest(unittest.TestCase):
    """monke.cloud_chat: chatgpt -> brain (uncapped, $0 ledger); claude -> tiers."""

    def setUp(self):
        monke.BUDGET.update(spent=0.0, resets_at=0, status="allowed")
        self.rec = []
        self.p = [patch.object(monke, "usage_add", lambda ses, host, rec, role: self.rec.append((role, rec))),
                  patch.object(monke, "publish_usage", lambda *a, **k: None),
                  patch.dict(monke.CLAUDE_CFG, window_usd=10.0, sonnet_pct=32, haiku_pct=22)]
        for p in self.p:
            p.start()
            self.addCleanup(p.stop)

    def test_chatgpt_calls_brain_and_ledgers_usage(self):
        def fake(messages, *, model=None, images=(), on_text=None, role=""):
            brain.LAST.clear()
            brain.LAST.update(model=model.split(":")[-1], **{"in": 11, "out": 2}, cost_usd=0.0, provider="chatgpt", ms=5)
            return "hi"
        with patch.object(brain, "chat", fake):
            out = monke.cloud_chat("chatgpt:gpt-5.5", None, "rm2", "write")([])
        self.assertEqual(out, "hi")
        role, rec = self.rec[0]
        self.assertEqual((role, rec["model"], rec["in"], rec["out"], rec["cost_usd"]),
                         ("write", "gpt-5.5", 11, 2, 0.0))

    def test_chatgpt_ignores_claude_budget(self):
        monke.BUDGET["spent"] = 9.0
        with patch.object(brain, "chat", lambda *a, **k: "ok"):
            self.assertEqual(monke.cloud_chat("chatgpt:gpt-5.6-luna", None, "rm2", "read")([]), "ok")

    def test_streamed_chunks_reach_the_consumer_cumulatively(self):
        seen = []

        def fake(messages, *, model=None, images=(), on_text=None, role=""):
            on_text("a ")
            on_text("b")
            return "a b"
        with patch.object(brain, "chat", fake):
            monke.cloud_chat("chatgpt:x", None, "rm2", "write", on_text=seen.append)([])
        self.assertEqual(seen, ["a ", "a b"])

    def test_claude_is_still_capped(self):
        monke.BUDGET["spent"] = 9.0
        with patch.object(brain, "chat", side_effect=AssertionError("brain")):
            with self.assertRaises(RuntimeError):
                monke.cloud_chat("haiku", None, "rm2", "write")([])

    def test_brain_failure_propagates(self):
        with patch.object(brain, "chat", side_effect=brain.BrainError("rate limit")):
            with self.assertRaises(brain.BrainError):
                monke.cloud_chat("chatgpt:gpt-5.5", None, "rm2", "write")([])


class ModelListTest(unittest.TestCase):
    def test_static_picker_list_keeps_the_json_shape(self):
        wrote = []
        with (patch.object(monke.tablet, "run", lambda h, s, i=b"", timeout=30: wrote.append(i) or (0, b"")),
              patch.dict(monke.CLAUDE_CFG, models=["haiku", "sonnet"]),
              patch.object(monke, "_MODELS_BODY", "")):
            monke.publish_models("rm2", dict(brain.DEFAULTS, provider="chatgpt", model="gpt-5.6-luna"))
        body = json.loads(wrote[0])
        ids = [c["id"] for c in body["cloud"]]
        self.assertEqual(ids[0], body["default"])                # default listed first
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({f"chatgpt:{m}" for m in brain.CHATGPT_MODELS} <= set(ids))
        self.assertTrue(any(i.startswith("openai:") for i in ids))
        self.assertTrue(any(i.startswith("anthropic:") for i in ids))
        self.assertEqual(ids[-2:], ["haiku", "sonnet"])          # Claude Code aliases last
        for i in ids[:-2]:
            self.assertIn(i.partition(":")[0], monke.API_PROVIDERS)
        self.assertTrue(all(isinstance(c["ready"], bool) for c in body["cloud"]))
        self.assertEqual(body["models"], [])
        self.assertEqual(body["default"], "chatgpt:gpt-5.6-luna")


class EyeReasonTest(unittest.TestCase):
    """A failed brain call must show on the eye, with a short reason."""

    def test_error_state_carries_a_short_reason(self):
        wrote = []
        monke._EYE["error"] = False
        with patch.object(monke.tablet, "run", lambda h, s, i=b"", timeout=30: wrote.append((s, i)) or (0, b"")):
            monke.eye_error("rm2", True, "BrainError: rate limit hit\n'; rm -rf / #" + "x" * 300)
        script, body = wrote[0]
        self.assertTrue(body.decode().startswith("watching error "))
        self.assertIn("rate limit hit", body.decode())
        self.assertNotIn("\n", body.decode().strip())
        self.assertLessEqual(len(body.decode()), 100)
        self.assertNotIn("rm -rf", script)                  # reason rides stdin, never the shell line
        monke._EYE["error"] = False

    def test_turn_failure_reason_is_remembered_for_the_eye(self):
        monke._TURN.clear()
        monke._fail("read", "[read] AI unreachable: rate limit")
        monke._fail("write", "[x] second")
        self.assertIn("rate limit", monke._TURN["why"])
        monke._TURN.clear()


class NoLocalModelTest(unittest.TestCase):
    BANNED = re.compile(r"localhost:1234|\blms\b|qwen|gemini|fallback_local|load_ai_config",
                        re.IGNORECASE)

    def test_no_local_model_or_gemini_in_host(self):
        hits = []
        for f in sorted((ROOT / "host").glob("*.py")):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if self.BANNED.search(line):
                    hits.append(f"{f.name}:{n}: {line.strip()[:80]}")
        self.assertEqual(hits, [])

    def test_config_has_brain_and_no_ai_section(self):
        txt = (ROOT / "config.toml").read_text(encoding="utf-8")
        self.assertIn("[brain]", txt)
        self.assertNotRegex(txt, r"(?m)^\[ai\]|^\[\[ai_fallback\]\]")
        # [endpoints] local_url is the user-picked OpenAI-compatible server
        # (Navy 2026-10-11) — never a default, never a fallback
        self.assertNotRegex(txt, r"(?mi)^(?!local_url)[^#\n]*(1234|qwen|gemini|lms)")
        self.assertRegex(txt, r'(?m)^provider = "openai"')


if __name__ == "__main__":
    unittest.main()
