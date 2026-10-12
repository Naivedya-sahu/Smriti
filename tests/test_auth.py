"""host/auth.py — Smriti's own per-user credentials (Navy 2026-10-11): never
Hermes/Codex/Claude files. Fakes only: temp SMRITI_HOME, fake keys, local
http.servers posing as OpenAI / Anthropic / a local server / the token endpoint."""
# pyright: reportMissingImports=false

import builtins
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import auth  # noqa: E402
import brain  # noqa: E402
import paths  # noqa: E402

FAKE_KEY = "sk-fake-test-key-9876"


def _jwt(exp: float, acct: str = "acct-1") -> str:
    import base64 as b
    enc = lambda o: b.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': exp, 'https://api.openai.com/auth': {'chatgpt_account_id': acct}})}.sig"


class Fake(BaseHTTPRequestHandler):
    """One server for every route: records the request, answers per path."""
    seen: list = []
    token_reply: dict = {}

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        h = {k.lower(): v for k, v in self.headers.items()}
        body = (dict(urllib.parse.parse_qsl(raw.decode()))
                if h.get("content-type", "").startswith("application/x-www-form")
                else json.loads(raw or b"{}"))
        Fake.seen.append({"path": self.path, "headers": h, "body": body})
        if self.path == "/oauth/token":
            out = json.dumps(Fake.token_reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if self.path.endswith("/messages"):
            evs = [{"type": "message_start", "message": {"model": "claude-x", "usage": {"input_tokens": 7}}},
                   {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "O"}},
                   {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "K"}},
                   {"type": "message_delta", "usage": {"output_tokens": 2}}]
        elif self.path.endswith("/chat/completions"):
            evs = [{"choices": [{"delta": {"content": "OK"}}]},
                   {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 1}}]
        else:                                           # Responses (openai / chatgpt)
            evs = [{"type": "response.output_text.delta", "delta": "OK"},
                   {"type": "response.completed", "response": {"model": "m", "usage": {
                       "input_tokens": 4, "output_tokens": 1}}}]
        for e in evs:
            self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())

    def log_message(self, *a):
        pass


class AuthCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        env = patch.dict(os.environ, {"SMRITI_HOME": str(self.home),
                                      "SMRITI_CLAUDE_HOME": str(self.home / "claude"),
                                      "OPENAI_API_KEY": "sk-env-should-not-be-used"})
        env.start()
        self.addCleanup(env.stop)
        Fake.seen, Fake.token_reply = [], {}
        self.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def run_cli(self, *argv):
        lines = []
        rc = auth.cli(list(argv), out=lines.append)
        return rc, "\n".join(lines)


class StoreTest(AuthCase):
    def test_roundtrip_is_encrypted_and_under_smriti_home(self):
        auth.put("openai", {"api_key": FAKE_KEY})
        auth.put("chatgpt", {"access_token": "AT", "refresh_token": "RT-secret-123"})
        f = paths.credentials_file()
        self.assertTrue(f.is_relative_to(self.home))
        raw = f.read_bytes()
        if os.name == "nt":
            self.assertTrue(raw.startswith(auth.MAGIC_DPAPI))
            self.assertNotIn(FAKE_KEY.encode(), raw)
            self.assertNotIn(b"RT-secret-123", raw)
        self.assertEqual(auth.get("openai"), {"api_key": FAKE_KEY})
        self.assertEqual(auth.get("chatgpt")["refresh_token"], "RT-secret-123")
        self.assertTrue(auth.forget("openai"))
        self.assertEqual(auth.get("openai"), {})
        self.assertFalse(list(self.home.glob("*.tmp")))

    def test_env_keys_only_when_allowed(self):
        self.assertIsNone(auth.api_key("openai", {"brain": {}}))
        self.assertEqual(auth.api_key("openai", {"brain": {"allow_env_keys": True}}),
                         "sk-env-should-not-be-used")


class CliTest(AuthCase):
    def test_key_set_and_status_mask_secrets(self):
        rc, out = self.run_cli("key", "set", "anthropic", FAKE_KEY)
        self.assertEqual(rc, 0)
        auth.put("chatgpt", {"access_token": _jwt(time.time() + 3600), "refresh_token": "RT-XYZW-SECRET"})
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertNotIn(FAKE_KEY, out)
        self.assertNotIn("RT-XYZW-SECRET", out)
        self.assertNotIn("sk-env-should", out)              # env not allowed by default
        self.assertIn("****9876", out)
        self.assertRegex(out, r"chatgpt\s+yes")
        self.assertRegex(out, r"openai\s+no")

    def test_key_prompt_uses_getpass(self):
        with patch.object(auth.getpass, "getpass", lambda prompt: FAKE_KEY):
            self.assertEqual(self.run_cli("key", "set", "openai")[0], 0)
        self.assertEqual(auth.get("openai")["api_key"], FAKE_KEY)

    def test_endpoint_set_validates_and_lands_in_overlay(self):
        rc, out = self.run_cli("endpoint", "set", "local", "ftp://box/v1")
        self.assertEqual(rc, 1)
        rc, out = self.run_cli("endpoint", "set", "local", "http://192.168.1.9:8080/v1/")
        self.assertEqual(rc, 0)
        self.assertIn("warning", out)                       # plain http off-loopback: warn only
        rc, out = self.run_cli("endpoint", "set", "openai", "https://proxy.example/v1")
        self.assertNotIn("warning", out)
        cfg = brain.load_config()
        self.assertEqual(cfg["local_url"], "http://192.168.1.9:8080/v1")
        self.assertEqual(cfg["openai_url"], "https://proxy.example/v1")
        self.assertEqual(cfg["anthropic_url"], "https://api.anthropic.com")   # shipped default

    def test_provider_set_writes_brain_overlay(self):
        self.assertEqual(self.run_cli("provider", "set", "anthropic")[0], 0)
        cfg = brain.load_config()
        self.assertEqual((cfg["provider"], cfg["model"]), ("anthropic", "claude-sonnet-4-5"))
        self.assertEqual(brain.default_spec(cfg), "anthropic:claude-sonnet-4-5")

    def test_claude_login_without_cli_prints_install_hint(self):
        with patch.object(auth, "claude_bin", lambda cfg=None: None):
            rc, out = self.run_cli("login", "claude")
        self.assertEqual(rc, 1)
        self.assertIn("npm i -g @anthropic-ai/claude-code", out)

    def test_claude_logout_needs_yes(self):
        cred = self.home / "claude" / ".credentials.json"
        cred.parent.mkdir(parents=True)
        cred.write_text("{}")
        self.assertEqual(self.run_cli("logout", "claude")[0], 2)
        self.assertTrue(cred.exists())
        self.assertEqual(self.run_cli("logout", "claude", "--yes")[0], 0)
        self.assertFalse(cred.exists())


class RefreshTest(AuthCase):
    def test_near_expiry_refreshes_via_token_endpoint(self):
        auth.put("chatgpt", {"access_token": _jwt(time.time() + 60), "refresh_token": "RT-old"})
        Fake.token_reply = {"access_token": _jwt(time.time() + 3600, "acct-new")}
        with patch.object(auth, "TOKEN_URL", self.base + "/oauth/token"):
            tok = auth.chatgpt_access_token()
        self.assertEqual(auth.jwt_claim(tok, "chatgpt_account_id"), "acct-new")
        b = Fake.seen[0]["body"]
        self.assertEqual((b["grant_type"], b["refresh_token"], b["client_id"]),
                         ("refresh_token", "RT-old", auth.CLIENT_ID))
        self.assertEqual(auth.get("chatgpt")["refresh_token"], "RT-old")   # none returned: kept
        Fake.token_reply = {"access_token": _jwt(time.time() + 3600), "refresh_token": "RT-new"}
        with patch.object(auth, "TOKEN_URL", self.base + "/oauth/token"):
            auth.chatgpt_access_token(force_refresh=True)
        self.assertEqual(auth.get("chatgpt")["refresh_token"], "RT-new")

    def test_fresh_token_is_not_refreshed(self):
        auth.put("chatgpt", {"access_token": _jwt(time.time() + 3600), "refresh_token": "RT"})
        auth.chatgpt_access_token()
        self.assertEqual(Fake.seen, [])


class RouteTest(AuthCase):
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "reply OK"}]

    def cfg(self, provider, **kw):
        return dict(brain.DEFAULTS, provider=provider, model="m1", transport="direct", timeout=10,
                    chatgpt_url=self.base + "/codex", openai_url=self.base + "/v1",
                    anthropic_url=self.base, local_url=self.base + "/local/v1", **kw)

    def test_openai_responses_with_key_and_plan_format(self):
        auth.put("openai", {"api_key": FAKE_KEY})
        self.assertEqual(brain.chat(self.msgs, role="write", cfg=self.cfg("openai")), "OK")
        r = Fake.seen[-1]
        self.assertEqual(r["path"], "/v1/responses")
        self.assertEqual(r["headers"]["authorization"], f"Bearer {FAKE_KEY}")
        self.assertNotIn("originator", r["headers"])
        self.assertIn("format", r["body"]["text"])
        self.assertEqual(brain.LAST["provider"], "openai")

    def test_openai_without_key_raises_and_ignores_env(self):
        with self.assertRaisesRegex(brain.BrainError, "auth key set openai"):
            brain.chat(self.msgs, cfg=self.cfg("openai"))
        self.assertEqual(Fake.seen, [])

    def test_anthropic_messages_route(self):
        auth.put("anthropic", {"api_key": FAKE_KEY})
        out = brain.chat(self.msgs, model="anthropic:claude-x", images=[b"\x89PNG"], cfg=self.cfg("openai"))
        self.assertEqual(out, "OK")
        r = Fake.seen[-1]
        self.assertEqual(r["path"], "/v1/messages")
        self.assertEqual((r["headers"]["x-api-key"], r["headers"]["anthropic-version"]),
                         (FAKE_KEY, "2023-06-01"))
        b = r["body"]
        self.assertEqual((b["model"], b["system"], b["stream"]), ("claude-x", "S", True))
        self.assertEqual(b["messages"][0]["content"][0]["type"], "image")
        self.assertEqual((brain.LAST["in"], brain.LAST["out"]), (7, 2))

    def test_local_chat_completions_no_key(self):
        self.assertEqual(brain.chat(self.msgs, model="local:qw", cfg=self.cfg("openai")), "OK")
        r = Fake.seen[-1]
        self.assertEqual(r["path"], "/local/v1/chat/completions")
        self.assertNotIn("authorization", r["headers"])
        self.assertNotIn("provider", r["body"])             # no gateway-only fields

    def test_chatgpt_route_uses_own_store(self):
        auth.put("chatgpt", {"access_token": _jwt(time.time() + 3600), "refresh_token": "RT"})
        self.assertEqual(brain.chat(self.msgs, model="chatgpt:gpt-5.5", cfg=self.cfg("openai")), "OK")
        r = Fake.seen[-1]
        self.assertEqual((r["path"], r["headers"]["originator"], r["headers"]["chatgpt-account-id"]),
                         ("/codex", "codex_cli_rs", "acct-1"))

    def test_resolve_prefixes(self):
        c = self.cfg("openai")
        self.assertEqual(brain.resolve("openai:", c), ("openai", "m1"))
        self.assertEqual(brain.resolve("anthropic:", c), ("anthropic", "claude-sonnet-4-5"))
        self.assertEqual(brain.resolve("claude:sonnet", c), ("claude", "sonnet"))
        self.assertEqual(brain.resolve("local", c), ("openai", "m1"))   # old setting -> default


class NoHermesTest(AuthCase):
    def test_brain_and_auth_never_touch_hermes_paths(self):
        opened, ran = [], []
        real_open, real_io_open, real_os_open = builtins.open, io.open, os.open
        real_popen = subprocess.Popen

        def rec(fn):
            def w(file, *a, **k):
                opened.append(str(file))
                return fn(file, *a, **k)
            return w

        def popen(argv, *a, **k):
            ran.append(" ".join(map(str, argv)) if isinstance(argv, (list, tuple)) else str(argv))
            return real_popen(argv, *a, **k)
        auth.put("chatgpt", {"access_token": _jwt(time.time() + 60), "refresh_token": "RT"})
        Fake.token_reply = {"access_token": _jwt(time.time() + 3600)}
        with (patch.object(builtins, "open", rec(real_open)), patch.object(io, "open", rec(real_io_open)),
              patch.object(os, "open", rec(real_os_open)), patch.object(subprocess, "Popen", popen),
              patch.object(auth, "TOKEN_URL", self.base + "/oauth/token")):
            cfg = dict(brain.load_config(), provider="chatgpt", transport="direct",
                       chatgpt_url=self.base + "/codex")
            brain.chat(RouteTest.msgs, cfg=cfg)
            self.run_cli("status")
        self.assertTrue(opened)
        self.assertEqual([p for p in opened if "hermes" in p.lower()], [])
        self.assertEqual([c for c in ran if "hermes" in c.lower()], [])
        self.assertTrue(any("/oauth/token" == r["path"] for r in Fake.seen))   # refreshed itself


if __name__ == "__main__":
    unittest.main()
