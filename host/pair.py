"""pair.py — `smriti pair <tablet-ip>`: give this Windows user a Smriti-owned
SSH key on the reMarkable, once.

1. ssh-keygen (Windows OpenSSH) -> data_dir()/ssh/id_ed25519, no passphrase.
2. One password login (you type the tablet's root password in this console;
   it is never stored or echoed) appends the public key to
   /home/root/.ssh/authorized_keys — only if that exact line is not there yet.
   The tablet's host key lands in Smriti's own known_hosts (accept-new).
3. Verifies a key-only login (BatchMode), then saves the address as the
   tailnet (100.64.0.0/10) or Wi-Fi endpoint in the user overlay.

Never reads or writes ~/.ssh. Re-running is safe (idempotent).
"""
from __future__ import annotations

import ipaddress
import os
import subprocess
from pathlib import Path

import deps
import link
import paths

AUTH_KEYS = "/home/root/.ssh/authorized_keys"
TAILNET = ipaddress.ip_network("100.64.0.0/10")


def _host(target: str) -> tuple[str, str]:
    """('root@<addr>', addr) from an IP / MagicDNS name, with or without root@."""
    addr = target.strip().removeprefix("root@")
    link.tailnet_endpoint(addr)                    # validates IP or host name
    return f"root@{addr}", addr


def ensure_key(run=subprocess.run) -> Path:
    key = deps.key_path()
    if key.exists() and key.with_suffix(".pub").exists():
        return key
    key.parent.mkdir(parents=True, exist_ok=True)
    comment = f"smriti@{os.environ.get('COMPUTERNAME', 'pc')}-{os.environ.get('USERNAME', 'user')}"
    r = run([deps.keygen_exe(), "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)])
    if r.returncode != 0 or not key.exists():
        raise RuntimeError("ssh-keygen failed (is Windows OpenSSH Client installed? "
                           "Settings > Optional features > OpenSSH Client)")
    if os.name == "nt":                            # OpenSSH refuses keys others can read
        user = os.environ.get("USERNAME", "")
        if user:
            run(["icacls", str(key), "/inheritance:r", "/grant:r", f"{user}:F"],
                capture_output=True)
    return key


def install_cmd(pub: str) -> str:
    """Remote shell: append the key line only if it is not already present."""
    q = pub.replace("'", "")
    return (f"umask 077; mkdir -p /home/root/.ssh && touch {AUTH_KEYS} && "
            f"(grep -qxF '{q}' {AUTH_KEYS} || echo '{q}' >> {AUTH_KEYS}) && echo smriti-key-ok")


def password_argv(host: str) -> list[str]:
    exe, _ = deps.ssh_exe()
    if not exe or exe == "ssh":
        exe = str(deps.WIN_SSH / "ssh.exe") if (deps.WIN_SSH / "ssh.exe").is_file() else "ssh"
    kh = deps.known_hosts().as_posix()
    return [exe, "-F", "none",
            "-o", f"UserKnownHostsFile={deps._quote(kh)}",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "PubkeyAuthentication=no",
            "-o", "PreferredAuthentications=password,keyboard-interactive",
            "-o", "NumberOfPasswordPrompts=3",
            "-o", "ConnectTimeout=10", host]


def verify_argv(host: str) -> list[str]:
    return [*deps.ssh_argv(host, "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"),
            "echo smriti-paired"]


def pair(target: str, save: bool = True, run=subprocess.run) -> int:
    host, addr = _host(target)
    key = ensure_key(run)
    pub = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    print(f"Smriti key: {key}")
    print(f"Pairing {host}. Type the tablet's root password when asked "
          "(Settings > Help > Copyrights and licenses on the tablet). It is not stored.")
    r = run([*password_argv(host), install_cmd(pub)])
    if r.returncode != 0:
        print("FAIL: could not install the key (wrong password, or tablet unreachable?)")
        return 1
    v = run(verify_argv(host), capture_output=True, text=True, timeout=30)
    if v.returncode != 0 or "smriti-paired" not in (v.stdout or ""):
        print("FAIL: key installed but key login did not work:", (v.stderr or "").strip()[:300])
        return 1
    print("PASS: key login works")
    if save:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            ip = None
        if ip is None or ip in TAILNET:
            paths.set_user("capture", "tailnet", addr)
            print(f"saved tailnet endpoint: {addr}")
        elif ip.version == 4:
            paths.set_user("capture", "wifi_ip", str(ip))
            print(f"saved wifi endpoint: {ip}")
    return 0
