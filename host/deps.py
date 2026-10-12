"""deps.py — where Smriti finds its external tools: ssh, pdflatex, claude.

One resolver per tool, each in a fixed order so `smriti deps` can say which
copy is used and why. The installed app never silently borrows another app's
copy: TeX comes from the bundled TinyTeX unless the user points at an existing
MiKTeX / TeX Live (`smriti deps set pdflatex <path>`), and ssh runs with
Smriti's own key + known_hosts, never ~/.ssh.

Dev (repo checkout, no SMRITI_HOME, no Smriti key) keeps the old behaviour
exactly: plain `ssh` + the user's ~/.ssh config (aliases like `rm2` work),
pdflatex from SMRITI_PDFLATEX / PATH / ~/.TinyTeX.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import paths

WIN_SSH = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH"


# ── config access (overlay-aware, never raises) ──────────────────────────────

def _cfg() -> dict:
    try:
        return paths.load_config()
    except (OSError, ValueError):
        return {}


def _opt(cfg: dict | None, section: str, key: str) -> str:
    cfg = _cfg() if cfg is None else cfg
    v = (cfg.get(section) or {}).get(key, "")
    return str(v).strip() if isinstance(v, str) else ""


# ── ssh ──────────────────────────────────────────────────────────────────────

def key_path() -> Path:
    return paths.ssh_dir() / "id_ed25519"


def known_hosts() -> Path:
    return paths.ssh_dir() / "known_hosts"


def smriti_ssh() -> bool:
    """Use Smriti's own key/known_hosts: installed app or SMRITI_HOME. A dev
    checkout keeps plain `ssh` + ~/.ssh/config (aliases like `rm2`) even when
    a Smriti key exists on this PC (2026-10-11: pairing the installed app
    flipped dev runs to -F none and broke the USB alias)."""
    return paths.FROZEN or bool(os.environ.get("SMRITI_HOME"))


def ssh_exe(cfg: dict | None = None) -> tuple[str, str]:
    """(ssh binary, source). Configured > Windows OpenSSH > PATH; dev: 'ssh'."""
    if (p := _opt(cfg, "ssh", "ssh_bin")):
        return p, "configured"
    if not smriti_ssh():
        return "ssh", "dev (PATH + ~/.ssh/config)"
    win = WIN_SSH / "ssh.exe"
    if win.is_file():
        return str(win), "Windows OpenSSH"
    if (p := shutil.which("ssh")):
        return p, "PATH"
    return "", "missing"


def keygen_exe(cfg: dict | None = None) -> str:
    ssh, _ = ssh_exe(cfg)
    if ssh and Path(ssh).is_file():
        sib = Path(ssh).with_name("ssh-keygen" + Path(ssh).suffix)
        if sib.is_file():
            return str(sib)
    win = WIN_SSH / "ssh-keygen.exe"
    return str(win) if win.is_file() else (shutil.which("ssh-keygen") or "ssh-keygen")


def _quote(v: str) -> str:
    return f'"{v}"' if " " in v else v


def ssh_base(cfg: dict | None = None) -> list[str]:
    """ssh binary + identity options (no host). Dev: just ['ssh']."""
    exe, _ = ssh_exe(cfg)
    exe = exe or "ssh"
    if not smriti_ssh():
        return [exe]
    try:
        paths.ssh_dir().mkdir(parents=True, exist_ok=True)   # known_hosts lands here
    except OSError:
        pass
    kh = known_hosts().as_posix()
    # -o IdentityFile, not -i: ssh drops a missing -i file and then falls back
    # to ~/.ssh/id_* (verified on OpenSSH_for_Windows_9.5); IdentityFile keeps
    # the explicit path even before `smriti pair` created it.
    return [exe, "-F", "none",                       # never read ~/.ssh/config
            "-o", f"IdentityFile={_quote(key_path().as_posix())}",
            "-o", "IdentitiesOnly=yes",
            "-o", f"UserKnownHostsFile={_quote(kh)}",
            "-o", "GlobalKnownHostsFile=none",
            "-o", "StrictHostKeyChecking=accept-new"]


def ssh_argv(host: str, *opts: str, cfg: dict | None = None) -> list[str]:
    """[ssh ..., *opts, host] — append the remote command yourself."""
    return [*ssh_base(cfg), *opts, host]


# ── pdflatex ─────────────────────────────────────────────────────────────────

def bundled_tex_root() -> Path:
    return paths.APP_ROOT / "tinytex"


def bundled_pdflatex() -> Path | None:
    for sub in ("windows", "win32", "win64"):
        p = bundled_tex_root() / "bin" / sub / "pdflatex.exe"
        if p.is_file():
            return p
    hits = sorted(bundled_tex_root().glob("bin/*/pdflatex"))
    return hits[0] if hits else None


def pdflatex(cfg: dict | None = None) -> tuple[str, str]:
    """(path, source): configured > SMRITI_PDFLATEX > bundled > (dev) PATH/~/.TinyTeX.

    The installed app never falls back to a PATH TeX the user didn't pick."""
    if (p := _opt(cfg, "tex", "pdflatex")):
        return p, "configured"
    if (p := os.environ.get("SMRITI_PDFLATEX")):
        return p, "env SMRITI_PDFLATEX"
    if (b := bundled_pdflatex()):
        return str(b), "bundled TinyTeX"
    if paths.FROZEN:
        return "", "missing"
    if (p := shutil.which("pdflatex")):
        return p, "PATH"
    hits = sorted(Path.home().glob(".TinyTeX/bin/*/pdflatex*"))
    if hits:
        return str(hits[0]), "~/.TinyTeX"
    return "", "missing"


def tex_env(exe: str) -> dict | None:
    """Environment for a bundled-TinyTeX run: caches under data_dir(), never
    the user profile. None (inherit) for a user-chosen TeX."""
    b = bundled_pdflatex()
    if not exe or b is None or Path(exe).resolve() != b.resolve():
        return None
    var = paths.data_dir() / "texmf-var"
    env = dict(os.environ)
    env.update(TEXMFVAR=str(var), TEXMFCONFIG=str(paths.data_dir() / "texmf-config"),
               TEXMFHOME=str(paths.data_dir() / "texmf-home"))
    return env


# ── claude CLI ───────────────────────────────────────────────────────────────

def claude(cfg: dict | None = None) -> tuple[str, str]:
    p = _opt(cfg, "claude", "claude_bin")
    if p and Path(p).is_file():
        return p, "configured"
    found = shutil.which(p or "claude")
    return (found, "PATH") if found else ("", "missing")


# ── report + setters (smriti deps / deps set) ────────────────────────────────

def _exists(p: str) -> bool:
    return bool(p) and (Path(p).is_file() or shutil.which(p) is not None)


def report(cfg: dict | None = None) -> list[tuple[str, str, bool, bool]]:
    """[(name, value, ok, required)]."""
    cfg = _cfg() if cfg is None else cfg
    tex, tsrc = pdflatex(cfg)
    ssh, ssrc = ssh_exe(cfg)
    cl, csrc = claude(cfg)
    key = key_path()
    py = f"{sys.version.split()[0]} ({'bundled' if paths.FROZEN else sys.executable})"
    return [
        ("python", py, True, True),
        ("pdflatex", f"{tex or '-'}  [{tsrc}]", _exists(tex), True),
        ("ssh", f"{ssh or '-'}  [{ssrc}]", _exists(ssh), True),
        ("ssh key", f"{key}  [{'present' if key.exists() else 'not paired: smriti pair <tablet-ip>'}]",
         key.exists(), False),
        ("claude cli", f"{cl or '-'}  [{csrc}]", bool(cl), False),
        ("data dir", str(paths.data_dir()), True, False),
        ("config overlay", f"{paths.user_cfg()}  [{'present' if paths.user_cfg().exists() else 'absent'}]",
         True, False),
    ]


SETTABLE = {"pdflatex": ("tex", "pdflatex"), "ssh": ("ssh", "ssh_bin"),
            "claude": ("claude", "claude_bin")}


def set_tool(name: str, value: str) -> Path:
    """`smriti deps set <tool> <path>` — point at an existing installation."""
    if name not in SETTABLE:
        raise ValueError(f"unknown tool {name!r} (one of: {', '.join(SETTABLE)})")
    p = Path(value).expanduser()
    if value and not p.is_file():
        raise ValueError(f"not a file: {value}")
    section, key = SETTABLE[name]
    return paths.set_user(section, key, str(p.resolve()) if value else "")
