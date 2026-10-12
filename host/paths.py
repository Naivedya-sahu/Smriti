"""
paths.py — where Smriti finds its files, dev checkout or installed exe.

Two roots, never mixed:
  APP_ROOT  read-only resources shipped with the program (default config.toml,
            device/, fonts, bundled TinyTeX). Dev: the repo. Frozen: the
            PyInstaller bundle (sys._MEIPASS).
  DATA_DIR  per-Windows-user state: user config overlay, credentials, ssh key,
            logs, Claude Code profile. %LOCALAPPDATA%\\Smriti, or SMRITI_HOME.
            Never another app's folder: no Hermes, no ~/.ssh, no ~/.claude.

Config = APP_ROOT/config.toml deep-merged with DATA_DIR/config.toml. The overlay
applies in the installed app (or when SMRITI_HOME is set), so a dev run from
the repo keeps reading exactly the repo config.
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
INSTALL_DIR = Path(sys.executable).resolve().parent if FROZEN else APP_ROOT


def data_dir() -> Path:
    raw = os.environ.get("SMRITI_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Smriti"


def overlay_active() -> bool:
    return FROZEN or bool(os.environ.get("SMRITI_HOME"))


DEFAULT_CFG = APP_ROOT / "config.toml"


def user_cfg() -> Path:
    return data_dir() / "config.toml"


def ssh_dir() -> Path:
    return data_dir() / "ssh"


def logs_dir() -> Path:
    return data_dir() / "logs"


def claude_home() -> Path:
    return data_dir() / "claude"


def credentials_file() -> Path:
    return data_dir() / "credentials.bin"


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _read(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def load_config() -> dict:
    """Effective config: shipped defaults + this user's overrides."""
    cfg = _read(DEFAULT_CFG)
    if overlay_active():
        cfg = deep_merge(cfg, _read(user_cfg()))
    return cfg


def load_user() -> dict:
    return _read(user_cfg())


# --- minimal TOML writer: the overlay only ever holds simple values ----------

def _val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_val(x)}" for k, x in v.items()) + " }"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v).__name__} to config")


def dumps(cfg: dict) -> str:
    lines = [f"{k} = {_val(v)}" for k, v in cfg.items() if not isinstance(v, dict)]
    for sec, body in cfg.items():
        if isinstance(body, dict):
            lines += ["", f"[{sec}]"] + [f"{k} = {_val(v)}" for k, v in body.items()]
    return "\n".join(lines).lstrip() + "\n"


def set_user(section: str, key: str, value) -> Path:
    """Write one override into the user overlay (atomic replace)."""
    cfg = load_user()
    cfg.setdefault(section, {})[key] = value
    path = user_cfg()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dumps(cfg), encoding="utf-8")
    tomllib.loads(tmp.read_text(encoding="utf-8"))   # refuse to install a broken file
    os.replace(tmp, path)
    return path
