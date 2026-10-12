"""
setup_wizard.py — interactive first-run setup (keys, token, endpoints).

    uv run python host/setup_wizard.py

Collects provider keys + the control token and persists them where the
daemon actually reads them: ~/.config/smriti/env on Linux (systemd
EnvironmentFile), HKCU setx on Windows.
Everything is skippable — manual fallback is editing that env file /
config.toml by hand, documented in README. Zero deps, plain ANSI.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ENV_FILE = Path.home() / ".config" / "smriti" / "env"
EYE = ["(o)", "(-)", "(o)", "(0)"]


def _blink(msg: str) -> None:
    for f in EYE:
        sys.stdout.write(f"\r  {f} {msg}")
        sys.stdout.flush()
        time.sleep(0.08)
    print()


def _ask(label: str, current: str = "", secret: bool = False) -> str:
    cur = f" [{'set' if secret and current else current or 'unset'}]" if True else ""
    v = input(f"  {label}{cur} (enter = keep): ").strip()
    return v or current


def _load_env() -> dict[str, str]:
    env = {}
    try:
        for ln in ENV_FILE.read_text().splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, _, v = ln.partition("=")
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def _save(env: dict[str, str]) -> None:
    if os.name == "nt":
        for k, v in env.items():
            if v:
                subprocess.run(["setx", k, v], capture_output=True)
        _blink("saved to user environment (setx) — new shells see it")
    else:
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        ENV_FILE.write_text("".join(f"{k}={v}\n" for k, v in env.items() if v))
        ENV_FILE.chmod(0o600)
        _blink(f"saved {ENV_FILE}")


def main() -> None:
    print("\n  ~~~ smriti setup — monke wakes ~~~\n")
    env = _load_env() if os.name != "nt" else {
        k: os.environ.get(k, "") for k in
        ("SMRITI_CONTROL_TOKEN",)}

    print("1/3 Brain — ChatGPT via Hermes (default); Claude CLI optional (subscription, no key here)")
    try:
        subprocess.run(["claude", "--version"], capture_output=True, check=True)
        _blink("claude CLI found — run `claude login` once if you haven't")
    except Exception:
        print("  !! claude CLI missing. Install:")
        print("     Linux:   curl -fsSL https://claude.ai/install.sh | bash")
        print("     Windows: irm https://claude.ai/install.ps1 | iex")
        print("     then: claude login   (or `claude setup-token` headless)")

    print("\n2/3 Control token (:7333 guards your AI quota)")
    tok = env.get("SMRITI_CONTROL_TOKEN", "")
    if not tok and input("  generate one? [Y/n]: ").strip().lower() != "n":
        tok = secrets.token_urlsafe(16)
        print(f"  token: {tok}")
        print("  ALSO put it on the tablet:")
        print(f"    ssh rm2 'echo SMRITI_TOKEN={tok} >> /home/root/.config/smriti-eye'")
    env["SMRITI_CONTROL_TOKEN"] = tok

    print("\n3/3 Endpoints — edit config.toml [ocr]/[screen]/[brain] for scribe "
          "boxes, tablet address, model. Defaults match Navy's tailnet.")

    _save(env)
    print("\n  monke ready. Start the daemon:")
    print("    uv run python host/monke.py          (foreground)")
    print("    sudo systemctl enable --now smriti-monke@$USER   (Pi/server)\n")


if __name__ == "__main__":
    main()
