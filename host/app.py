"""app.py — Smriti's program entry (frozen as smriti.exe + SmritiTray.exe).

    smriti                      start the tray (SmritiTray.exe does this too)
    smriti tray                 run the tray in this process
    smriti run                  daemon in the foreground
    smriti doctor [host]        every link pen -> reply, with fixes
    smriti link-test            probe each tablet route (read-only)
    smriti link show | set-wifi <ipv4> | set-tailnet <ip-or-magicdns>
    smriti pair <tablet-ip>     one-time: Smriti SSH key onto the tablet
    smriti deps                 resolved tools (python, pdflatex, ssh, key, claude)
    smriti deps set pdflatex|ssh|claude <path>   point at an existing install
    smriti tex-test             compile one equation + one circuit
    smriti auth ...             sign-in / keys / endpoints (auth module)
    smriti paths                where everything lives
    smriti version
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

VERSION = "0.5.0"

if not getattr(sys, "frozen", False):              # dev: host/ modules importable
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402


def _frozen_fixups() -> None:
    """Frozen modules sit at _MEIPASS/<name>.pyc, so their
    Path(__file__).parents[1] 'repo root' is wrong. Point the resource
    roots at the bundle before anything reads them."""
    if not paths.FROZEN:
        return
    root = paths.APP_ROOT
    import skill
    skill.ROOT = root / "skill" / "smriti"
    for name in ("ink", "scribe", "brain", "oracle"):
        try:
            mod = __import__(name)
        except Exception:                          # noqa: BLE001 — optional modules
            continue
        if hasattr(mod, "REPO"):
            mod.REPO = root


def _console_utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:                          # noqa: BLE001
            pass


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="smriti", description="Smriti — reMarkable 2 study companion.",
                                 epilog="Sign-in/keys/endpoints: smriti auth --help")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("tray", help="system tray (default)")
    sub.add_parser("run", help="daemon in the foreground")
    d = sub.add_parser("doctor", help="check every link, print fixes")
    d.add_argument("host", nargs="?")
    sub.add_parser("link-test", help="probe each tablet route (read-only)")
    lk = sub.add_parser("link", help="tablet routes (wireless)")
    lsub = lk.add_subparsers(dest="action", required=True)
    lsub.add_parser("show")
    w = lsub.add_parser("set-wifi", help="tablet IPv4 on Wi-Fi/hotspot")
    w.add_argument("ip", nargs="?", help="omit to be asked")
    sc = lsub.add_parser("scan", help="find the tablet on this PC's Wi-Fi/hotspot")
    sc.add_argument("--save", action="store_true", help="save a verified hit as the wifi endpoint")
    t = lsub.add_parser("set-tailnet", help="tablet tailnet IP or MagicDNS name")
    t.add_argument("host")
    p = sub.add_parser("pair", help="one-time: install Smriti's SSH key on the tablet")
    p.add_argument("tablet", nargs="?", help="IP / MagicDNS name (default: saved Wi-Fi IP, else asked)")
    p.add_argument("--password-prompt", action="store_true", help="(default) type the password here")
    p.add_argument("--no-save", action="store_true", help="don't save the address as an endpoint")
    cf = sub.add_parser("config", help="your settings file (endpoints, IPs, paths)")
    cf.add_argument("action", nargs="?", choices=("edit", "path"), default="edit")
    dp = sub.add_parser("deps", help="resolved dependencies")
    dsub = dp.add_subparsers(dest="action")
    ds = dsub.add_parser("set", help="point at an existing installation ('' clears)")
    ds.add_argument("tool", choices=["pdflatex", "ssh", "claude"])
    ds.add_argument("path")
    sub.add_parser("tex-test", help="compile an equation + a circuit")
    sub.add_parser("auth", help="sign-in, API keys, endpoints (smriti auth --help)", add_help=False)
    sub.add_parser("paths", help="where everything lives")
    sub.add_parser("version")
    sub.add_parser("claude-status", help="Claude login + spend")
    sub.add_parser("selfcheck", help="placement self-test")
    return ap


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_auth(argv: list[str]) -> int:
    try:
        import auth
    except ImportError as e:
        print(f"auth module not available in this build ({e}). "
              "Sign-in/keys/endpoints need host/auth.py.", file=sys.stderr)
        return 2
    return int(auth.cli(argv) or 0)


def cmd_paths() -> int:
    for k, v in (("APP_ROOT", paths.APP_ROOT), ("INSTALL_DIR", paths.INSTALL_DIR),
                 ("data_dir", paths.data_dir()), ("config (shipped)", paths.DEFAULT_CFG),
                 ("config overlay", paths.user_cfg()), ("ssh", paths.ssh_dir()),
                 ("logs", paths.logs_dir()), ("frozen", paths.FROZEN),
                 ("overlay active", paths.overlay_active())):
        print(f"{k:17} {v}")
    return 0


def cmd_deps(a) -> int:
    import deps
    if a.action == "set":
        try:
            p = deps.set_tool(a.tool, a.path)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"saved {a.tool} -> {a.path or '(default)'} in {p}")
        return 0
    bad = False
    for name, value, ok, required in deps.report():
        mark = "ok " if ok else ("MISSING" if required else "-  ")
        print(f"{mark:8} {name:15} {value}")
        bad |= required and not ok
    return 1 if bad else 0


def cmd_link(a) -> int:
    import link
    if a.action == "show":
        cfg = link.app_config()
        for ep in link.endpoints(cfg):
            print(f"{ep.name:8} {ep.host:28} {ep.screen_url}")
        if not paths.overlay_active():
            print("(dev checkout: user overlay ignored; set SMRITI_HOME to use it)")
        return 0
    try:
        if a.action == "scan":
            return cmd_scan(a.save)
        if a.action == "set-wifi":
            raw = a.ip or input("Tablet Wi-Fi IPv4 (tablet: Settings > Help > About > IP): ").strip()
            ip = link.wifi_ip(raw)
            p = paths.set_user("capture", "wifi_ip", ip)
            print(f"wifi endpoint: root@{ip} (saved in {p})")
        else:
            ep = link.tailnet_endpoint(a.host)
            p = paths.set_user("capture", "tailnet", ep.host.removeprefix("root@"))
            print(f"tailnet endpoint: {ep.host} (saved in {p})")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not paths.overlay_active():
        print("note: dev checkout ignores the overlay unless SMRITI_HOME is set")
    return 0


def default_tablet() -> str:
    import link
    cap = link.app_config().get("capture", {})
    return str(cap.get("wifi_ip") or cap.get("tailnet") or "")


def cmd_pair(target: str | None, save: bool = True) -> int:
    import pair
    target = target or default_tablet()
    if not target:
        target = input("Tablet IP (tablet: Settings > Help > About): ").strip()
    if not target:
        print("no tablet address given")
        return 2
    return pair.pair(target, save=save)


def key_rejected(host: str) -> bool:
    """True when the tablet answers but refuses Smriti's SSH key."""
    import subprocess
    import deps
    try:
        p = subprocess.run([*deps.ssh_argv(host, "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"),
                            "true"], stdin=subprocess.DEVNULL, capture_output=True, timeout=12)
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode != 0 and b"Permission denied" in p.stderr


def offer_ssh_fix(ip: str, ask=input) -> int:
    """The tablet refused the key: fix it right here in the console."""
    print(f"\nThe tablet at {ip} refused Smriti's SSH key.")
    if not sys.stdin or not sys.stdin.isatty():
        print(f"fix: smriti pair {ip}   (type the tablet password once)  or  smriti config")
        return 1
    print("  [1] type the tablet's root password now (installs the key, password not stored)")
    print("  [2] open the settings file (change IPs / endpoints / ssh path)")
    print("  [Enter] skip")
    choice = ask("choice: ").strip()
    if choice == "1":
        return cmd_pair(ip)
    if choice == "2":
        import tray
        print(f"opening {tray.edit_config()} — save, then restart Smriti (tray: Stop, Start)")
        return 0
    return 1


def cmd_scan(save: bool) -> int:
    import scan
    nets = scan.local_networks()
    if not nets:
        print("no private Wi-Fi/hotspot network on this PC")
        return 1
    print("scanning " + ", ".join(map(str, nets)) + " (port 22, read-only) ...")
    hits = scan.scan(nets)
    for h in hits:
        print(f"  {'TABLET' if h.verified else 'maybe '} {h.ip:15} {h.banner[:40]:40} {h.detail}")
    good = [h for h in hits if h.verified]
    if not hits:
        print("nothing found: same Wi-Fi? tablet awake? (or: smriti link set-wifi <ip>)")
        return 1
    if save and len(good) == 1:
        p = paths.set_user("capture", "wifi_ip", good[0].ip)
        print(f"saved wifi endpoint: root@{good[0].ip} ({p})")
    elif save:
        print("not saved: need exactly one verified tablet (pair first: smriti pair <ip>)")
    if not good:
        denied = [h for h in hits if "key not accepted" in h.detail]
        if len(denied) == 1:
            rc = offer_ssh_fix(denied[0].ip)
            if rc == 0 and save:
                paths.set_user("capture", "wifi_ip", denied[0].ip)
                print(f"saved wifi endpoint: root@{denied[0].ip}")
            return rc
    return 0 if good else 1


def cmd_tex_test() -> int:
    import time
    import deps
    import tex
    exe, src = deps.pdflatex()
    print(f"pdflatex: {exe or '-'} [{src}]")
    cases = [("equation", r"$\displaystyle \int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2}$"),
             ("circuitikz", r"\begin{circuitikz}\draw (0,0) to[R=$R_1$] (3,0) to[C=$C_1$] (3,-2)"
                            r" node[ground]{};\end{circuitikz}"),
             ("pgfplots", r"\begin{tikzpicture}\begin{axis}[width=5cm]\addplot{x^2};"
                          r"\end{axis}\end{tikzpicture}")]
    fail = False
    for name, src_tex in cases:
        t0 = time.perf_counter()
        try:
            img = tex.tex_to_image(src_tex)
            ms = (time.perf_counter() - t0) * 1000
            print(f"OK   {name:11} {ms:6.0f} ms  {img.shape[1]}x{img.shape[0]} px, {int(img.sum())} ink px")
        except Exception as e:                     # noqa: BLE001
            fail = True
            print(f"FAIL {name:11} {e}")
    return 1 if fail else 0


def start_tray_detached() -> int:
    """smriti.exe with no args: hand off to the windowed SmritiTray.exe."""
    import subprocess
    tray_exe = paths.INSTALL_DIR / "SmritiTray.exe"
    if paths.FROZEN and tray_exe.is_file():
        subprocess.Popen([str(tray_exe)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                         close_fds=True)
        print("Smriti tray started (notification area). `smriti --help` lists the switches.")
        return 0
    import tray
    return tray.main()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _console_utf8()
    os.environ.setdefault("PYTHONUTF8", "1")
    if Path(sys.executable).stem.lower() == "smrititray" and not argv:
        argv = ["tray"]
    if argv and argv[0] == "auth":                 # auth parses its own argv
        return cmd_auth(argv[1:])
    a = parser().parse_args(argv)
    if a.cmd is None:
        return start_tray_detached()
    if a.cmd == "version":
        print(f"smriti {VERSION} (python {sys.version.split()[0]}"
              f"{', frozen' if paths.FROZEN else ', dev'})")
        return 0
    if a.cmd == "paths":
        return cmd_paths()
    if a.cmd == "deps":
        return cmd_deps(a)
    if a.cmd == "link":
        return cmd_link(a)
    if a.cmd == "pair":
        return cmd_pair(a.tablet, save=not a.no_save)
    if a.cmd == "config":
        if a.action == "path":
            print(paths.user_cfg())
            return 0
        import tray
        print(f"opening {tray.edit_config()} — save, then restart Smriti (tray: Stop, Start)")
        return 0
    _frozen_fixups()
    if a.cmd == "tex-test":
        return cmd_tex_test()
    if a.cmd == "tray":
        import tray
        return tray.main()
    import monke
    if a.cmd == "run":
        monke.run()
        return 0
    if a.cmd == "doctor":
        rows = monke.doctor(a.host)
        return 0 if all(ok for _, ok, _ in rows) else 1
    if a.cmd == "link-test":
        ok = monke.link_test()
        if not ok:
            import link
            for ep in link.endpoints(link.app_config()):
                if key_rejected(ep.host):
                    return offer_ssh_fix(ep.host.removeprefix("root@"))
        return 0 if ok else 1
    if a.cmd == "claude-status":
        monke.claude_status()
        return 0
    if a.cmd == "selfcheck":
        monke._selfcheck()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
