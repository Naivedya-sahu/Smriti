"""
brain.py — the single entry for every Smriti model call (Navy 2026-10-10,
own-auth 2026-10-11).

Providers (all logins/keys are Smriti's own, per Windows user — host/auth.py;
nothing is read from Hermes, ~/.codex or ~/.claude):
  openai     OpenAI API key -> {openai_url}/responses             (shipped default)
  chatgpt    "Sign in with ChatGPT" -> {chatgpt_url} (Codex backend), token
             refreshed by auth.py. transport "cli"/"http" = explicit opt-in to
             a Hermes CLI / gateway, never the default.
  anthropic  Anthropic API key -> {anthropic_url}/v1/messages
  claude     Claude Code CLI on the subscription (host/oracle.py), Smriti-only
             profile dir
  local      any OpenAI-compatible server -> {local_url}/chat/completions;
             only when picked — never a silent fallback.
Endpoints come from config [endpoints] (user overlay via `auth endpoint set`).
A failed call RAISES (BrainError) — the turn fails visibly.

    chat(messages, model="openai:gpt-5.5", images=[png], on_text=fn, role="write")
    last_usage() -> {model, in, out, cost_usd, provider, ms}

model = the tablet's setting: "<provider>:<id>" | haiku/sonnet/opus (Claude
CLI) | empty or unknown -> [brain] default. on_text(chunk) per streamed chunk;
a truthy return stops the call early (the reader's JSON is complete).

Plan mode ([brain] plan = "json", the default): the WRITER call (role
"write") on a Responses route (chatgpt direct, openai) asks for the
smriti_plan JSON (host/plan_schema.py) through Structured Outputs
text.format; deltas still stream. An HTTP 400 on a schema call retries once
without it; if that succeeds the backend rejected the schema, so the text
grammar is used for the rest of the process (logged once). Other routes
always use the text grammar.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth  # noqa: E402
import paths  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

DEFAULTS = {
    "provider": "openai",           # openai | chatgpt | anthropic | claude | local
    "model": "gpt-5.5",
    "transport": "direct",          # chatgpt only: direct | cli (Hermes CLI) | http (gateway)
    "hermes_bin": "hermes",         # cli transport only (opt-in)
    "profile": "smriti",
    "timeout": 60,
    "url": "http://127.0.0.1:8642/p/smriti/v1",   # http transport only (opt-in)
    "key_file": "",                 # http transport key; default %LOCALAPPDATA%/smriti/brain.key
    "turn": "split",
    "som": True,
    "plan": "json",                 # json (Structured Outputs plan) | text (the text grammar)
    "reasoning": "none",            # Responses reasoning.effort; "" = omit
    "max_tokens": 4096,             # anthropic route
    "allow_env_keys": False,        # OPENAI_API_KEY / ANTHROPIC_API_KEY only when true
    "chatgpt_model": "gpt-5.6-luna",
    "openai_model": "gpt-5.5",
    "anthropic_model": "claude-sonnet-4-5",
    "local_model": "",
    "claude_model": "haiku",
    **auth.ENDPOINT_DEFAULTS,       # chatgpt_url, openai_url, anthropic_url, local_url
}
PREFIXED = ("chatgpt", "openai", "anthropic", "local", "claude")
RESPONSES = ("chatgpt", "openai")    # routes that speak the Responses API (plan json)
CLAUDE_ALIASES = ("haiku", "sonnet", "opus")
# allowed on Navy's ChatGPT plan (measured 2026-10-10); the tablet's picker
CHATGPT_MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.5")
PLAN_ROLES = ("write",)              # only the stage-2 writer answers with a plan

LAST: dict = {}
PLAN_OFF: list = []                  # why json plans are off for this process (set once)


class BrainError(RuntimeError):
    pass


class _SchemaRejected(BrainError):
    """HTTP 400 on a request that carried text.format."""


def load_config() -> dict:
    """[brain] + [endpoints] of the effective config (shipped + user overlay)."""
    full = paths.load_config()
    cfg = {**DEFAULTS, **(full.get("brain") or {})}
    if cfg.get("direct_url") and not (full.get("endpoints") or {}).get("chatgpt_url"):
        cfg["chatgpt_url"] = cfg["direct_url"]                      # legacy key
    cfg.update({k: v for k, v in (full.get("endpoints") or {}).items()
                if k in auth.ENDPOINT_DEFAULTS and v})
    return cfg


def last_usage() -> dict:
    return dict(LAST)


def default_model(cfg: dict, provider: str) -> str:
    """The model a provider uses when the setting names none."""
    cfg = {**DEFAULTS, **cfg}
    if cfg["provider"] == provider and cfg.get("model"):
        return str(cfg["model"])
    return str(cfg.get(f"{provider}_model") or "")


def default_spec(cfg: dict) -> str:
    """The [brain] default as a tablet-setting string."""
    cfg = {**DEFAULTS, **cfg}
    return cfg["model"] if cfg["provider"] == "claude" else f"{cfg['provider']}:{cfg['model']}"


def resolve(spec: str | None, cfg: dict) -> tuple[str, str]:
    """(provider, model) for a tablet setting."""
    cfg = {**DEFAULTS, **cfg}
    spec = (spec or "").strip()
    head, sep, tail = spec.partition(":")
    if sep and head in PREFIXED:
        return head, tail or default_model(cfg, head)
    if spec in CLAUDE_ALIASES:
        return "claude", spec
    return cfg["provider"], cfg["model"]


def plan_mode(cfg: dict, role: str = "write", provider: str | None = None) -> str:
    """'json' when this call asks for the smriti_plan JSON, else 'text'."""
    cfg = {**DEFAULTS, **cfg}
    provider = provider or cfg["provider"]
    if PLAN_OFF or role not in PLAN_ROLES or provider not in RESPONSES:
        return "text"
    if provider == "chatgpt" and cfg["transport"] != "direct":
        return "text"
    return "json" if str(cfg.get("plan", "json")).strip().lower() == "json" else "text"


def chat(messages: list[dict], *, model: str | None = None, images=(), on_text=None,
         role: str = "", cfg: dict | None = None) -> str:
    cfg = {**DEFAULTS, **(cfg or load_config())}
    provider, name = resolve(model, cfg)
    t0 = time.time()
    if provider == "claude":                 # budget tiers live in monke.cloud_chat
        return _claude(messages, images, name, on_text)
    system, user, imgs = _split(messages, images)
    plan = plan_mode(cfg, role, provider)
    if provider in RESPONSES and (provider == "openai" or cfg["transport"] == "direct"):
        if plan == "json":
            import plan_schema
            try:
                text, usage = _responses(cfg, provider, name, system + "\n\n" + plan_schema.SWITCH,
                                         user, imgs, on_text, fmt=plan_schema.FORMAT)
            except _SchemaRejected as e:
                text, usage = _responses(cfg, provider, name, system, user, imgs, on_text)
                plan = "text"                # the plain retry worked: the schema was the problem
                if not PLAN_OFF:
                    PLAN_OFF.append(str(e))
                    print(f"[brain] plan json rejected ({str(e)[:120]}) - text grammar "
                          f"for the rest of this process", flush=True)
        else:
            text, usage = _responses(cfg, provider, name, system, user, imgs, on_text)
    elif provider == "anthropic":
        text, usage = _anthropic(cfg, name, system, user, imgs, on_text)
    elif provider == "local":
        text, usage = _local(cfg, name, system, user, imgs, on_text)
    elif provider == "chatgpt" and cfg["transport"] == "http":
        text, usage = _http(cfg, name, system, user, imgs, on_text)
    elif provider == "chatgpt" and cfg["transport"] == "cli":
        text, usage = _cli(cfg, name, system, user, imgs, on_text)
    else:
        raise BrainError(f"unknown provider/transport {provider}/{cfg['transport']}")
    if not usage.get("in") and not usage.get("out"):
        usage.update({"in": (len(system) + len(user) + 3) // 4,
                      "out": (len(text) + 3) // 4, "estimated": True})
    LAST.clear()
    LAST.update(model=usage.get("model") or name, cost_usd=0.0, provider=provider, role=role,
                ms=int((time.time() - t0) * 1000),
                estimated=bool(usage.get("estimated", False)), plan=plan,
                **{"in": int(usage.get("in", 0)), "out": int(usage.get("out", 0))})
    return text


def _claude(messages, images, name, on_text) -> str:
    import oracle
    cfg = dict(paths.load_config().get("claude") or {}, model=name)
    if on_text:
        cfg["_on_text"] = lambda cum: on_text(cum)
    if images:
        messages = messages[:-1] + [_with_images(messages[-1], images)]
    oracle.LAST_USAGE.clear()
    text = oracle._claude_compat(messages, cfg)
    LAST.clear()
    LAST.update(oracle.LAST_USAGE, provider="claude")
    return text


def _with_images(msg: dict, images) -> dict:
    parts = msg["content"] if isinstance(msg["content"], list) else [{"type": "text", "text": msg["content"]}]
    return dict(msg, content=parts + [_img_part(b) for b in images])


def _img_part(data: bytes) -> dict:
    return {"type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(data).decode()}}


def _split(messages, images) -> tuple[str, str, list[bytes]]:
    """system text, last user text, images (user-message parts first, then
    the `images` argument). Stateless: only the last user turn survives."""
    system = "\n\n".join(m["content"] for m in messages
                         if m["role"] == "system" and isinstance(m["content"], str))
    user = next((m for m in reversed(messages) if m["role"] == "user"), {"content": ""})
    content, imgs = user["content"], []
    if isinstance(content, list):
        texts = []
        for p in content:
            if p.get("type") == "text":
                texts.append(p["text"])
            elif p.get("type") == "image_url":
                imgs.append(base64.b64decode(p["image_url"]["url"].split(",", 1)[1]))
        content = "\n".join(texts)
    return system, content, imgs + list(images)


def _cli(cfg, model, system, user, images, on_text) -> tuple[str, dict]:
    argv = shlex.split(cfg["hermes_bin"])
    argv[0] = shutil.which(argv[0]) or argv[0]
    argv += ["-p", cfg["profile"], "chat", "-Q", "--query-file", "-", "-m", model,
             "--provider", "openai-codex", "--reasoning", "none", "-t", "none",
             "--max-turns", "1", "--format", "stream-json"]
    img = None
    if images:                               # Hermes takes one image
        fd, img = tempfile.mkstemp(suffix=".png", prefix="smriti_brain_")
        with os.fdopen(fd, "wb") as f:
            f.write(images[0])
        argv += ["--image", img]
    prompt = f"SYSTEM:\n{system}\n\nUSER:\n{user}"
    try:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    except OSError as e:
        _rm(img)
        raise BrainError(f"cannot run {argv[0]}: {e}"[:200]) from None
    timed_out = []
    timer = threading.Timer(cfg["timeout"], lambda: (timed_out.append(1), p.kill()))
    timer.start()
    err: list[bytes] = []
    err_pipe = p.stderr
    assert err_pipe is not None
    err_thread = threading.Thread(target=lambda: err.append(err_pipe.read()), daemon=True)
    err_thread.start()
    text, used, result, stopped = "", {}, None, False
    try:
        try:
            p.stdin.write(prompt.encode("utf-8"))
            p.stdin.close()
        except OSError:
            pass                             # child died early: its output says why
        for line in p.stdout:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind = e.get("type")
            if kind == "system" and e.get("subtype") == "init":
                used["model"] = e.get("model")
            elif kind == "text":
                chunk = e.get("text", "")
                text += chunk
                if on_text and on_text(chunk):
                    p.kill()
                    stopped = True
                    break
            elif kind == "result":
                result = e
    finally:
        timer.cancel()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        err_thread.join(timeout=5)
        p.stdout.close()
        err_pipe.close()
        _rm(img)
    if timed_out:
        raise BrainError(f"hermes timed out after {cfg['timeout']}s")
    if stopped:
        return text, used
    if result is None:
        tail = (err[0].decode(errors="replace") if err else "").strip()
        raise BrainError(f"hermes gave no result (rc={p.returncode}): {tail}"[:200])
    if result.get("exit_code") != 0:
        raise BrainError(str(result.get("text", ""))[:200])
    tok = result.get("tokens") or {}
    used.update({"in": tok.get("input", 0), "out": tok.get("output", 0)})
    return str(result.get("text") or text).strip(), used


def _rm(path) -> None:
    try:
        if path:
            os.unlink(path)
    except OSError:
        pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _sse(r):
    """data: payloads of an SSE response, parsed (non-JSON lines skipped)."""
    for raw in r:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue                         # blank lines, "event:", ": keepalive"
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except ValueError:
            continue


def _key(provider: str, cfg: dict, required: bool = True) -> str | None:
    try:
        key = auth.api_key(provider, {"brain": cfg})
    except auth.AuthError as e:
        raise BrainError(str(e)[:200]) from None
    if not key and required:
        raise BrainError(f"no {provider} API key — run: Smriti auth key set {provider}")
    return key


def _http_error(e: urllib.error.HTTPError) -> str:
    return f"HTTP {e.code}: {e.read().decode(errors='replace')}"[:200]


def _responses(cfg, provider, model, system, user, images, on_text, fmt=None) -> tuple[str, dict]:
    """Responses API, streamed. provider "chatgpt": the Codex backend
    ({chatgpt_url}) with Smriti's own ChatGPT login (auth.py refreshes it:
    near expiry, or once on a 401). provider "openai": {openai_url}/responses
    with the user's API key. fmt = a text.format (the smriti_plan json_schema)."""
    chatgpt = provider == "chatgpt"
    url = cfg["chatgpt_url"] if chatgpt else cfg["openai_url"].rstrip("/") + "/responses"
    content = [{"type": "input_text", "text": user}] + [
        {"type": "input_image", "image_url": "data:image/png;base64," + _b64(b)} for b in images]
    body = {"model": model, "instructions": system, "stream": True, "store": False}
    if cfg.get("reasoning"):
        body["reasoning"] = {"effort": cfg["reasoning"]}
    body["input"] = [{"role": "user", "content": content}]
    if fmt:
        body["text"] = {"format": fmt}
    data = json.dumps(body).encode()

    def send(token: str):
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                   "Authorization": f"Bearer {token}", "User-Agent": "smriti-brain/0.1"}
        if chatgpt:
            headers["originator"] = "codex_cli_rs"
            if acct := auth.jwt_claim(token, "chatgpt_account_id"):
                headers["ChatGPT-Account-ID"] = acct
        req = urllib.request.Request(url, data, headers)
        return urllib.request.urlopen(req, timeout=cfg["timeout"])

    def once(token):
        text, used = "", {}
        with send(token) as r:
            for e in _sse(r):
                ty = e.get("type", "")
                if ty == "response.output_text.delta":
                    chunk = e.get("delta", "")
                    text += chunk
                    if on_text and on_text(chunk):
                        return text, used
                elif ty == "response.completed":
                    u = (e.get("response") or {}).get("usage") or {}
                    used = {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0),
                            "model": (e.get("response") or {}).get("model")}
                elif ty in ("response.failed", "error"):
                    err = (e.get("response") or {}).get("error") or e.get("error") or e
                    raise BrainError(f"{provider}: {json.dumps(err)}"[:200])
        return text, used

    def token(force=False):
        if not chatgpt:
            return _key("openai", cfg)
        try:
            return auth.chatgpt_access_token(force_refresh=force)
        except auth.AuthError as e:
            raise BrainError(str(e)[:200]) from None

    tok, retried = token(), False
    while True:
        try:
            return once(tok)
        except urllib.error.HTTPError as e:
            if e.code == 401 and chatgpt and not retried:
                retried = True
                tok = token(force=True)
                continue
            why = _http_error(e)
            if fmt and e.code == 400:
                raise _SchemaRejected(why) from None
            raise BrainError(why) from None
        except (urllib.error.URLError, OSError) as e:
            raise BrainError(f"{provider} backend unreachable: {e}"[:200]) from None


def _anthropic(cfg, model, system, user, images, on_text) -> tuple[str, dict]:
    """Anthropic Messages API ({anthropic_url}/v1/messages), streamed."""
    key = _key("anthropic", cfg)
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": _b64(b)}} for b in images]
    content.append({"type": "text", "text": user})
    body = {"model": model, "max_tokens": int(cfg.get("max_tokens") or 4096), "stream": True,
            "messages": [{"role": "user", "content": content}]}
    if system:
        body["system"] = system
    req = urllib.request.Request(
        cfg["anthropic_url"].rstrip("/") + "/v1/messages", json.dumps(body).encode(),
        {"Content-Type": "application/json", "Accept": "text/event-stream", "x-api-key": key,
         "anthropic-version": "2023-06-01", "User-Agent": "smriti-brain/0.1"})
    text, used = "", {}
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as r:
            for e in _sse(r):
                ty = e.get("type")
                if ty == "message_start":
                    m = e.get("message") or {}
                    used.update(model=m.get("model"), **{"in": (m.get("usage") or {}).get("input_tokens", 0)})
                elif ty == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                    chunk = e["delta"].get("text", "")
                    text += chunk
                    if on_text and on_text(chunk):
                        return text, used
                elif ty == "message_delta":
                    used["out"] = (e.get("usage") or {}).get("output_tokens", 0)
                elif ty == "error":
                    raise BrainError(f"anthropic: {json.dumps(e.get('error'))}"[:200])
    except urllib.error.HTTPError as e:
        raise BrainError(_http_error(e)) from None
    except (urllib.error.URLError, OSError) as e:
        raise BrainError(f"anthropic unreachable: {e}"[:200]) from None
    return text.strip(), used


def _chat_completions(cfg, url, key, model, system, user, images, on_text, extra=None):
    content = [{"type": "text", "text": user}] + [_img_part(b) for b in images]
    body = {"model": model, "stream": True, "stream_options": {"include_usage": True},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": content if images else user}],
            **(extra or {})}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(body).encode(), headers)
    deadline = time.time() + cfg["timeout"]
    text, used = "", {}
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as r:
            for e in _sse(r):
                if time.time() > deadline:
                    raise BrainError(f"brain timed out after {cfg['timeout']}s")
                if e.get("usage"):
                    used = {"in": e["usage"].get("prompt_tokens", 0),
                            "out": e["usage"].get("completion_tokens", 0)}
                for c in e.get("choices") or []:
                    if chunk := (c.get("delta") or {}).get("content"):
                        text += chunk
                        if on_text and on_text(chunk):
                            return text, used
    except urllib.error.HTTPError as e:
        raise BrainError(_http_error(e)) from None
    except (urllib.error.URLError, OSError) as e:
        raise BrainError(f"brain unreachable: {e}"[:200]) from None
    return text.strip(), used


def _local(cfg, model, system, user, images, on_text) -> tuple[str, dict]:
    """Any OpenAI-compatible server at {local_url}; key optional."""
    return _chat_completions(cfg, cfg["local_url"], _key("local", cfg, required=False),
                             model or "default", system, user, images, on_text)


def _http(cfg, model, system, user, images, on_text) -> tuple[str, dict]:
    """Opt-in transport="http": a Hermes-style gateway with its own key file."""
    key_file = Path(cfg["key_file"] or Path(os.environ.get("LOCALAPPDATA", "~")) / "smriti" / "brain.key")
    try:
        key = key_file.expanduser().read_text().strip()
    except OSError as e:
        raise BrainError(f"brain key unreadable: {e}"[:200]) from None
    return _chat_completions(cfg, cfg["url"], key, model, system, user, images, on_text,
                             extra={"provider": "openai-codex",
                                    "model_options": {"reasoning_effort": "none"}})
