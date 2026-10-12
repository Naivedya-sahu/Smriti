"""
oracle.py — the Claude Code CLI path (headless `claude -p` on Navy's
subscription). Every other model call goes through host/brain.py.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _claude_compat(messages: list[dict], cfg: dict) -> str:
    """openai-style messages → one stateless claude -p call. Only the system
    prompt and the LAST user message survive (text + images) — session-aware
    callers should use claude_chat directly with resume ids instead."""
    system = next((m["content"] for m in messages if m["role"] == "system"), None)
    user = next(m for m in reversed(messages) if m["role"] == "user")
    prompt, images = user["content"], []
    if isinstance(prompt, list):        # multimodal parts
        texts = []
        for part in prompt:
            if part.get("type") == "text":
                texts.append(part["text"])
            elif part.get("type") == "image_url":
                b64 = part["image_url"]["url"].split(",", 1)[1]
                images.append(base64.b64decode(b64))
        prompt = "\n".join(texts)
    return claude_chat(prompt, images, system=system, cfg=cfg)[0]


LAST_USAGE: dict = {}      # usage of the most recent claude_chat call
RATE: dict = {}            # last CLI rate_limit_event: status, resetsAt (no percent)

# $ per million tokens (in, out) — for streams stopped early, which the CLI
# never prices. ponytail: alias-level table; update when prices change.
PRICES = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (5.0, 25.0)}


def estimate_cost(model: str, tok_in: int, tok_out: int) -> float:
    pin, pout = next((v for k, v in PRICES.items() if k in model), PRICES["sonnet"])
    return (tok_in * pin + tok_out * pout) / 1e6


def claude_home(cfg: dict) -> Path:
    """Smriti's ISOLATED Claude Code profile (CLAUDE_CONFIG_DIR). Navy's
    normal profile loads hooks, plugins, MCP servers and CLAUDE.md into every
    call — tens of KB of hidden tokens per turn. This one holds only the
    subscription login, made by the user: `Smriti auth login claude`.
    Order: env SMRITI_CLAUDE_HOME > [claude] config_dir (installed app: only
    the user's own overlay, never the shipped dev path) > paths.claude_home()."""
    import paths
    raw = os.environ.get("SMRITI_CLAUDE_HOME")
    if not raw:
        raw = ((paths.load_user().get("claude") or {}).get("config_dir") if paths.FROZEN
               else cfg.get("config_dir"))
    return Path(os.path.expanduser(raw)) if raw else paths.claude_home()


def claude_chat(prompt: str, images: list[bytes] = (),
                system: str | None = None, resume: str | None = None,
                cfg: dict | None = None, persist: bool = False) -> tuple[str, str]:
    """Headless Claude Code (`claude -p`) on the subscription, lean:
    isolated profile, `system` REPLACES Claude Code's default prompt, tools
    off (Read only when there are images to look at), --setting-sources ""
    and strict empty MCP. persist=False -> stateless (no session saved);
    persist=True / resume=<id> -> full session (context grows per turn).
    Returns (reply_text, session_id); usage lands in LAST_USAGE. Raises on
    any failure: the turn fails visibly."""
    cfg = cfg or {}
    on_text = cfg.get("_on_text")          # stream: callback(text so far)
    home = claude_home(cfg)
    work = home / "work"                        # neutral cwd: no project CLAUDE.md
    work.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, data in enumerate(images):           # inside cwd: Read may open them
        p = work / f"page_{os.getpid()}_{i}.png"
        p.write_bytes(data)
        paths.append(p)
    if paths:
        prompt = ("Look at " + " and ".join(str(p) for p in paths) +
                  " (Read tool) first.\n\n" + prompt)
    cmd = [cfg.get("claude_bin", "claude"), "-p",
           "--model", cfg.get("model", "haiku"),
           "--output-format", "stream-json" if on_text else "json",
           "--tools", "Read" if paths else "",
           "--setting-sources", "",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--max-turns", "3" if paths else "1"]
    if on_text:
        cmd += ["--verbose", "--include-partial-messages"]
    if system:
        cmd += ["--system-prompt", system]
    if resume:
        cmd += ["--resume", resume]
    elif not persist:
        cmd += ["--no-session-persistence"]
    # thinking OFF unless configured: Claude Code thinks by default, and it
    # bills as output — a 2-sentence reply cost 8,284 output tokens / 78 s;
    # with MAX_THINKING_TOKENS=0 the same reply is ~170 tokens / 4 s
    # (measured 2026-09-26). --effort low still thought (6,261 tokens).
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(home),
               MAX_THINKING_TOKENS=str(int(cfg.get("thinking_tokens", 0))))
    try:
        if on_text:
            out = _claude_stream(cmd, prompt, work, env, cfg.get("timeout", 120), on_text)
        else:
            r = subprocess.run(cmd, input=prompt.encode(), capture_output=True,
                               cwd=work, env=env, timeout=cfg.get("timeout", 120))
            if r.returncode != 0 and not r.stdout:
                raise RuntimeError(f"claude rc={r.returncode}: "
                                   f"{r.stderr.decode(errors='replace')[:300]}")
            out = json.loads(r.stdout.decode())
        u = out.get("usage", {}) or {}
        LAST_USAGE.clear()
        LAST_USAGE.update({
            "model": cfg.get("model", "haiku"),
            "in": int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0))
                  + int(u.get("cache_creation_input_tokens", 0)),
            "out": int(u.get("output_tokens", 0)),
            "cost_usd": float(out.get("total_cost_usd", 0) or 0)
                        or estimate_cost(cfg.get("model", "haiku"),
                                         int(u.get("input_tokens", 0)),
                                         int(u.get("output_tokens", 0)))})
        if out.get("is_error"):
            raise RuntimeError(f"claude error: {str(out.get('result'))[:300]}")
        return out["result"].strip(), out.get("session_id", "")
    finally:
        for p in paths:
            try:
                p.unlink()
            except OSError:
                pass



def _claude_stream(cmd, prompt, work, env, timeout, on_text) -> dict:
    """stream-json: call on_text(text so far) at every text delta; return the
    final 'result' event (same shape as --output-format json)."""
    import threading
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, cwd=work, env=env)
    timer = threading.Timer(timeout, p.kill)        # a hung stream must not hang Smriti
    timer.start()
    try:
        p.stdin.write(prompt.encode())
        p.stdin.close()
        text, result, tok_in = "", None, 0
        for line in p.stdout:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            ev = e.get("event") or {}
            d = ev.get("delta") or {}
            if e.get("type") == "rate_limit_event":
                RATE.update(e.get("rate_limit_info") or {})
            elif ev.get("type") == "message_start":
                u = (ev.get("message") or {}).get("usage") or {}
                tok_in = sum(int(u.get(k, 0) or 0) for k in (
                    "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            if e.get("type") == "stream_event" and d.get("type") == "text_delta":
                text += d.get("text", "")
                if on_text(text):
                    # caller has what it needs (e.g. the reader's JSON is
                    # complete): stop paying for tokens nobody reads
                    p.kill()
                    print(f"[claude] stopped at a complete answer ({len(text)} chars)", flush=True)
                    return {"result": text, "session_id": "", "is_error": False,
                            "usage": {"input_tokens": tok_in,
                                      "output_tokens": max(1, len(text) // 4)},
                            "stopped_early": True}       # usage: estimated
            elif e.get("type") == "result":
                result = e
        p.wait(timeout=5)
    finally:
        timer.cancel()
    if result is None:
        raise RuntimeError(f"claude stream ended without a result (rc={p.returncode}): "
                           f"{p.stderr.read().decode(errors='replace')[:300]}")
    return result

def image_msg(image: str | Path | bytes, prompt: str) -> dict:
    """Build a user message carrying an image + text."""
    data = image if isinstance(image, bytes) else Path(image).read_bytes()
    b64 = base64.b64encode(data).decode()
    return {"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url",
         "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]}
