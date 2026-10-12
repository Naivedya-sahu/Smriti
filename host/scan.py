"""
scan.py — find the rM2 on the Wi-Fi / hotspot this PC is on.

Bounded and read-only: only this PC's own private IPv4 networks (/24 around
each address), one TCP connect to port 22 per host, then the SSH banner. A
hit counts as the tablet ONLY after an SSH login reads `reMarkable` from
/sys/devices/soc0/machine — an open port 22 on the LAN proves nothing.
Unverified hits (key not paired yet) are reported, never saved.

    smriti link scan [--save]
"""

from __future__ import annotations

import ipaddress
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from deps import ssh_argv

SKIP = [ipaddress.ip_network(n) for n in
        ("100.64.0.0/10",      # tailnet: has its own endpoint
         "169.254.0.0/16",     # link-local
         "127.0.0.0/8",
         "10.11.99.0/24")]     # USB: not part of the wireless app


@dataclass(frozen=True)
class Hit:
    ip: str
    banner: str
    verified: bool
    detail: str


def local_networks(addrs: list[str] | None = None) -> list[ipaddress.IPv4Network]:
    """/24 networks around this PC's private IPv4 addresses."""
    if addrs is None:
        addrs = []
        try:
            addrs += [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
        except OSError:
            pass
        try:                                     # primary route's source address
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.0.2.1", 9))      # TEST-NET: no packet is sent
                addrs.append(s.getsockname()[0])
        except OSError:
            pass
    nets = []
    for a in addrs:
        try:
            ip = ipaddress.IPv4Address(a)
        except ValueError:
            continue
        if not ip.is_private or any(ip in n for n in SKIP):
            continue
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        if net not in nets:
            nets.append(net)
    return nets


def ssh_banner(ip: str, timeout: float = 0.35) -> str:
    """'' if port 22 is closed, else the server's SSH identification line."""
    try:
        with socket.create_connection((ip, 22), timeout=timeout) as s:
            s.settimeout(0.8)
            return (s.recv(80).decode("ascii", "replace").splitlines() or [""])[0].strip()
    except OSError:
        return ""


def verify(ip: str, run=subprocess.run) -> tuple[bool, str]:
    """SSH in (Smriti key) and read the machine name. Never changes anything."""
    try:
        p = run([*ssh_argv(f"root@{ip}", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"),
                 "cat /sys/devices/soc0/machine"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=12)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)[:120]
    out = p.stdout.decode(errors="replace").strip() if isinstance(p.stdout, bytes) else (p.stdout or "").strip()
    if p.returncode == 0 and "reMarkable" in out:
        return True, out
    err = p.stderr.decode(errors="replace") if isinstance(p.stderr, bytes) else (p.stderr or "")
    if "Permission denied" in err:
        return False, "ssh key not accepted: run  smriti pair " + ip
    return False, (out or err.strip())[:120] or f"ssh rc={p.returncode}"


def scan(nets: list[ipaddress.IPv4Network] | None = None, banner=ssh_banner,
         check=verify, workers: int = 64) -> list[Hit]:
    nets = local_networks() if nets is None else nets
    hosts = [str(h) for n in nets for h in n.hosts()]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        banners = list(pool.map(banner, hosts))
    ssh = [(ip, b) for ip, b in zip(hosts, banners) if b.startswith("SSH-")]
    rm = [(ip, b) for ip, b in ssh if "dropbear" in b.lower()] or ssh   # rM2 runs dropbear
    hits = []
    for ip, b in rm:
        ok, detail = check(ip)
        hits.append(Hit(ip, b, ok, detail))
    return sorted(hits, key=lambda h: (not h.verified, h.ip))


def find_tablet(**kw) -> str | None:
    """The one verified rM2 address on the local networks, else None."""
    hits = [h for h in scan(**kw) if h.verified]
    return hits[0].ip if len(hits) == 1 else None
