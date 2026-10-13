"""tray.py — Smriti in the Windows notification area (SmritiTray.exe).

The daemon runs as a child process (`smriti.exe run`) logging to
logs_dir()/daemon-YYYYMMDD.log. Stop and Quit ALWAYS disarm the tablet first
and verify it (armed == 0, goMarkableStream not active) before killing the
daemon — killing first once left the tablet armed + streaming overnight.
If the disarm can't be verified the daemon is left running and a
notification says so; "Force stop" is then offered.

Icon: assets/smriti.ico (packaging/assets in the repo). The eye is a
placeholder — drop in an ermine smriti.ico with the same name to rebrand.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import deps
import paths

DISARM = "echo 0 > /home/root/.smriti/armed; /home/root/.vellum/bin/smriti-eye stop"
VERIFY = ("echo armed=$(cat /home/root/.smriti/armed 2>/dev/null); "
          "echo gms=$(systemctl is-active goMarkableStream 2>/dev/null)")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


# ── icon ─────────────────────────────────────────────────────────────────────

def make_icon(size: int = 256):
    """Placeholder: dark circle, white eye. (Future: ermine mascot .ico.)"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    d.ellipse([2 * s, 2 * s, 62 * s, 62 * s], fill=(28, 30, 38, 255))
    d.ellipse([10 * s, 21 * s, 54 * s, 43 * s], fill=(245, 245, 245, 255))
    d.ellipse([24 * s, 22 * s, 40 * s, 42 * s], fill=(28, 30, 38, 255))
    d.ellipse([29 * s, 26 * s, 34 * s, 31 * s], fill=(245, 245, 245, 255))
    return img


def write_ico(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    make_icon(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


def icon_image():
    from PIL import Image
    for p in (paths.APP_ROOT / "assets" / "smriti.ico",
              paths.APP_ROOT / "packaging" / "assets" / "smriti.ico"):
        if p.is_file():
            try:
                return Image.open(p)
            except OSError:
                pass
    return make_icon(64)


# ── commands ─────────────────────────────────────────────────────────────────

def cli_argv(*args: str) -> list[str]:
    """How to invoke the console CLI from here (installed or dev)."""
    if paths.FROZEN:
        return [str(paths.INSTALL_DIR / "smriti.exe"), *args]
    return [sys.executable, str(Path(__file__).resolve().with_name("app.py")), *args]


def open_console(*args: str) -> None:
    """A visible console running `smriti <args>` that stays open."""
    exe = cli_argv(*args)
    subprocess.Popen(["cmd.exe", "/k", subprocess.list2cmdline(exe)], creationflags=NEW_CONSOLE)


def disarm(host: str, run=subprocess.run, sleep=time.sleep, tries: int = 4) -> tuple[bool, str]:
    """Disarm the tablet and verify it. (ok, detail). Never kills anything."""
    opts = ("-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8")
    try:
        run([*deps.ssh_argv(host, *opts), DISARM], capture_output=True, timeout=30,
            creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"disarm ssh failed: {e}"
    detail = "no answer"
    for i in range(tries):
        try:
            r = run([*deps.ssh_argv(host, *opts), VERIFY], capture_output=True, timeout=20,
                    creationflags=NO_WINDOW)
            out = (r.stdout or b"")
            out = out.decode(errors="replace") if isinstance(out, bytes) else out
            kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
            detail = f"armed={kv.get('armed', '?')} stream={kv.get('gms', '?')}"
            if "armed" in kv and kv["armed"].strip() in ("0", "") and kv.get("gms", "").strip() != "active":
                return True, detail
        except (OSError, subprocess.SubprocessError) as e:
            detail = str(e)
        if i < tries - 1:
            sleep(2)                     # smriti-eye stop finishes async (~3 s)
    return False, detail


class Daemon:
    def __init__(self, popen=subprocess.Popen, run=subprocess.run, sleep=time.sleep):
        self.proc = None
        self.host = ""
        self.link = ""
        self.log_path: Path | None = None
        self._popen, self._run, self._sleep = popen, run, sleep

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> bool:
        if self.running():
            return True
        logs = paths.logs_dir()
        logs.mkdir(parents=True, exist_ok=True)
        self.log_path = logs / f"daemon-{time.strftime('%Y%m%d')}.log"
        log = open(self.log_path, "ab")
        log.write(f"\n=== start {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode())
        log.flush()
        env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        self.proc = self._popen(cli_argv("run"), stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, creationflags=NO_WINDOW)
        log.close()
        return True

    def scan_log(self) -> None:
        """Pick up '[link] name: host -> url' from the daemon log."""
        if not self.log_path or not self.log_path.exists():
            return
        try:
            tail = self.log_path.read_bytes()[-20000:].decode("utf-8", "replace")
        except OSError:
            return
        for line in reversed(tail.splitlines()):
            if line.startswith("[link] ") and ":" in line:
                name, rest = line[7:].split(":", 1)
                self.link, self.host = name.strip(), rest.split("->")[0].strip()
                return

    def target_host(self) -> str:
        self.scan_log()
        if self.host:
            return self.host
        import link
        cfg = load_cfg()
        ep = link.select(link.endpoints(cfg), link.reachable)
        return ep.host if ep else ""

    def stop(self, force: bool = False) -> tuple[bool, str]:
        """Disarm -> verify -> only then terminate. force skips the gate."""
        if not self.running():
            return True, "daemon not running"
        detail = "forced"
        if not force:
            host = self.target_host()
            if not host:
                return False, "tablet unreachable: cannot disarm"
            ok, detail = disarm(host, self._run, self._sleep)
            if not ok:
                return False, f"disarm not verified ({detail})"
        self.proc.terminate()
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None
        return True, detail


def load_cfg() -> dict:
    import link
    return link.app_config()


# ── Windows bits: single instance, autostart ─────────────────────────────────

_MUTEX = None


def single_instance(name: str = "Local\\SmritiTray") -> bool:
    global _MUTEX
    if os.name != "nt":
        return True
    import ctypes
    k32 = ctypes.windll.kernel32
    _MUTEX = k32.CreateMutexW(None, False, name)
    return k32.GetLastError() != 183            # ERROR_ALREADY_EXISTS


def autostart_cmd() -> str:
    return f'"{paths.INSTALL_DIR / "SmritiTray.exe"}"'


def autostart_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, "Smriti")[0])
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "Smriti", 0, winreg.REG_SZ, autostart_cmd())
        else:
            try:
                winreg.DeleteValue(k, "Smriti")
            except FileNotFoundError:
                pass


OVERLAY_TEMPLATE = """\
# Smriti — your settings (this file overrides the shipped defaults).
# Uncomment a line to change it. CLI equivalents in brackets.
#
# [capture]
# tailnet = "100.64.0.10"        # tablet tailnet IP / MagicDNS   [smriti link set-tailnet]
# wifi_ip = "172.20.10.2"          # tablet IP on Wi-Fi / hotspot  [smriti link set-wifi]
#
# [tex]
# pdflatex = "C:/Program Files/MiKTeX/miktex/bin/x64/pdflatex.exe"   [smriti deps set pdflatex]
#
# [ssh]
# ssh_bin = "C:/Windows/System32/OpenSSH/ssh.exe"                     [smriti deps set ssh]
#
# Brain / endpoints / sign-in: smriti auth --help
"""


def edit_config() -> Path:
    p = paths.user_cfg()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(OVERLAY_TEMPLATE, encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(p)])
    return p


def _open_folder(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    os.startfile(str(p))  # type: ignore[attr-defined]


# ── the tray ─────────────────────────────────────────────────────────────────

def main() -> int:
    import pystray
    from pystray import Menu, MenuItem as Item

    if not single_instance():
        return 0
    d = Daemon()
    state = {"stuck": False, "want": False, "last_start": 0.0}

    def notify(msg: str, title: str = "Smriti") -> None:
        try:
            icon.notify(msg, title)
        except Exception:                         # noqa: BLE001
            pass

    def status(_item) -> str:
        return f"Link: {d.link or '-'}  ·  daemon {'running' if d.running() else 'stopped'}"

    def start(_i=None, _it=None):
        state["want"] = True
        state["last_start"] = time.time()
        d.start()
        notify("Daemon started — log in the logs folder.")

    def stop(quit_after: bool = False, force: bool = False):
        state["want"] = False
        def work():
            ok, detail = d.stop(force=force)
            state["stuck"] = not ok
            if not ok:
                state["want"] = True            # still running: keep supervising
            icon.update_menu()
            if not ok:
                notify(f"Tablet NOT disarmed: {detail}. Daemon left running. "
                       "Wake the tablet / check the link and retry, or use Force stop.", "Smriti — stop blocked")
                return
            notify(f"Stopped ({detail}).")
            if quit_after:
                icon.stop()
        threading.Thread(target=work, daemon=True).start()

    def link_test(_i=None, _it=None):
        def work():
            import link
            rows = link.report(load_cfg())
            msg = ", ".join(f"{'PASS' if ok else 'FAIL'} {ep.name}" for ep, ok in rows) or "no endpoints"
            notify(msg, "Smriti link test")
        threading.Thread(target=work, daemon=True).start()

    def toggle_autostart(_i, _it):
        set_autostart(not autostart_enabled())

    def find_wifi(_i=None, _it=None):
        def work():
            import scan
            try:
                hits = scan.scan()
            except Exception as e:                    # noqa: BLE001
                notify(f"scan failed: {e}", "Smriti Wi-Fi scan")
                return
            good = [h for h in hits if h.verified]
            if len(good) == 1:
                paths.set_user("capture", "wifi_ip", good[0].ip)
                notify(f"Tablet found at {good[0].ip} — saved. Restart the daemon to use it.",
                       "Smriti Wi-Fi scan")
            elif hits:
                notify("; ".join(f"{h.ip}: {h.detail}" for h in hits)[:250], "Smriti Wi-Fi scan")
            else:
                notify("No tablet on this Wi-Fi. Same network? Tablet awake?", "Smriti Wi-Fi scan")
        threading.Thread(target=work, daemon=True).start()

    menu = Menu(
        Item(status, None, enabled=False),
        Menu.SEPARATOR,
        Item("Start", start, enabled=lambda _: not d.running()),
        Item("Stop (disarm first)", lambda i, it: stop(), enabled=lambda _: d.running()),
        Item("Force stop (skip disarm check)", lambda i, it: stop(force=True),
             visible=lambda _: state["stuck"]),
        Item("Link test", link_test),
        Item("Find tablet on Wi-Fi", find_wifi),
        Item("Set tablet Wi-Fi IP…", lambda i, it: open_console("link", "set-wifi")),
        Item("Doctor", lambda i, it: open_console("doctor")),
        Item("Sign in", Menu(
            Item("ChatGPT", lambda i, it: open_console("auth", "login", "chatgpt")),
            Item("Claude", lambda i, it: open_console("auth", "login", "claude")),
            Item("Set API keys", lambda i, it: open_console("auth", "--help")),
            Item("Status", lambda i, it: open_console("auth", "status")),
        )),
        Item("Settings", Menu(
            Item("Edit config", lambda i, it: edit_config()),
            Item("Pair tablet (enter password)", lambda i, it: open_console("pair")),
            Item("Dependencies", lambda i, it: open_console("deps")),
            Item("Start with Windows", toggle_autostart, checked=lambda _: autostart_enabled()),
        )),
        Item("Open logs folder", lambda i, it: _open_folder(paths.logs_dir())),
        Item("Open data folder", lambda i, it: _open_folder(paths.data_dir())),
        Menu.SEPARATOR,
        Item("Quit", lambda i, it: stop(quit_after=True) if d.running() else icon.stop()),
        Item("Force quit", lambda i, it: stop(quit_after=True, force=True),
             visible=lambda _: state["stuck"]),
    )
    icon = pystray.Icon("Smriti", icon_image(), "Smriti", menu)

    halt = threading.Event()                     # set when the tray loop ends -> watcher exits

    def watch():
        while not halt.wait(3):
            d.scan_log()
            if not state.get("key_warned") and d.log_path and d.log_path.exists():
                try:
                    tail = d.log_path.read_bytes()[-4000:].decode("utf-8", "replace")
                except OSError:
                    tail = ""
                if "Permission denied" in tail:
                    state["key_warned"] = True
                    notify("The tablet refused Smriti's SSH key. Tray > Settings > "
                           "Pair tablet (enter password), or Settings > Edit config.",
                           "Smriti — SSH key")
            # supervisor: the daemon should run but died (crash, sleep/resume)
            if state["want"] and not d.running() and time.time() - state["last_start"] > 20:
                state["last_start"] = time.time()
                d.start()
            try:
                icon.update_menu()
            except Exception:                     # noqa: BLE001
                pass
    watcher = threading.Thread(target=watch, daemon=True, name="smriti-tray-watch")
    watcher.start()
    try:
        if load_cfg().get("app", {}).get("start_daemon", True):
            start()                              # open the tray = Smriti ready, no extra click
        icon.run()
    finally:
        halt.set()                               # no supervisor restarts once the tray is gone
        watcher.join(timeout=5)
    return 0
