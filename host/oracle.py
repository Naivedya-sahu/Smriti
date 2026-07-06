"""
oracle.py — Smriti AI provider (v0.1.3).

OpenAI-compatible chat + vision over plain urllib — works with LM Studio
(localhost), OpenRouter, or anything speaking /v1/chat/completions.
Provider picked by config.toml [ai]; api_key read from env when named.

    python host/oracle.py ask "hello, who are you?"
    python host/oracle.py see page.png "Transcribe the handwriting on this page."
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _getkey(name: str) -> str:
    """Env var, falling back to the Windows user registry (where `setx`
    writes) — covers keys set after this process started, e.g. over ssh."""
    if v := os.environ.get(name):
        return v
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, name)[0]
    except (OSError, ImportError):    # missing value, or not Windows
        return ""


def load_ai_config() -> dict:
    with open(REPO / "config.toml", "rb") as f:
        cfg = tomllib.load(f)["ai"]
    key_env = cfg.get("api_key_env")
    cfg["api_key"] = _getkey(key_env) if key_env else cfg.get("api_key", "lm-studio")
    return cfg


def chat(messages: list[dict], cfg: dict | None = None) -> str:
    cfg = cfg or load_ai_config()
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps({
            "model": cfg["model"],
            "messages": messages,
            "temperature": cfg.get("temperature", 0.7),
            "max_tokens": cfg.get("max_tokens", 1024),
        }).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 300)) as r:
            return json.load(r)["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{cfg['base_url']} -> HTTP {e.code}: "
                           f"{e.read().decode(errors='replace')[:300]}") from None


def ask(prompt: str, system: str | None = None) -> str:
    msgs = ([{"role": "system", "content": system}] if system else [])
    msgs.append({"role": "user", "content": prompt})
    return chat(msgs)


def image_msg(image: str | Path | bytes, prompt: str) -> dict:
    """Build a user message carrying an image + text."""
    data = image if isinstance(image, bytes) else Path(image).read_bytes()
    b64 = base64.b64encode(data).decode()
    return {"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url",
         "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]}


def see(image: str | Path | bytes, prompt: str, system: str | None = None) -> str:
    msgs = ([{"role": "system", "content": system}] if system else [])
    msgs.append(image_msg(image, prompt))
    return chat(msgs)


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "ask":
        print(ask(" ".join(sys.argv[2:])))
    elif cmd == "see":
        print(see(sys.argv[2], " ".join(sys.argv[3:]) or "Transcribe this page."))
    else:
        sys.exit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    main()
