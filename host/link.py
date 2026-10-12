"""rM2 transport endpoints: USB, tailnet, and an explicit local Wi-Fi IP.

The resolver does not scan networks. iPhone hotspots and local DHCP leases have
no stable address; the user saves one (`smriti link set-wifi <ip>` -> [capture]
wifi_ip) or passes SMRITI_RM2_WIFI_IP (wins). `[capture] tailnet` replaces the
configured tailnet endpoint. The installed app drops USB endpoints (wireless()).
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence

from deps import ssh_argv


@dataclass(frozen=True)
class Endpoint:
    name: str
    host: str
    screen_url: str


def _entry(raw: object) -> Endpoint | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    host = str(raw.get("host") or "").strip()
    url = str(raw.get("screen_url") or "").strip().rstrip("/")
    return Endpoint(name, host, url) if name and host and url else None


def _wifi(value: object) -> Endpoint | None:
    try:
        ip = str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return None
    return Endpoint("wifi", f"root@{ip}", f"https://{ip}:2001")


def tailnet_endpoint(value: str) -> Endpoint:
    """root@<tailnet IP or MagicDNS name>; raises ValueError on junk."""
    v = str(value).strip().removeprefix("root@")
    try:
        v = str(ipaddress.ip_address(v))
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", v):
            raise ValueError(f"not an IP or host name: {value!r}") from None
    return Endpoint("tailnet", f"root@{v}", f"https://{v}:2001")


def wifi_ip(value: str) -> str:
    """Validated IPv4 for `smriti link set-wifi` (hotspot/LAN address)."""
    ip = ipaddress.ip_address(str(value).strip())     # ValueError on junk
    if ip.version != 4:
        raise ValueError(f"wifi_ip must be IPv4: {value}")
    return str(ip)


def _usb(ep: Mapping) -> bool:
    return str(ep.get("name", "")).lower() == "usb" or "10.11.99." in str(ep.get("screen_url", "")) \
        or "10.11.99." in str(ep.get("host", ""))


def wireless(cfg: dict) -> dict:
    """The installed app is for cable-free use: drop USB endpoints."""
    if not (cfg.get("app") or {}).get("wireless_only", True):
        return cfg
    capture = dict(cfg.get("capture") or {})
    capture["endpoints"] = [e for e in capture.get("endpoints", []) if isinstance(e, Mapping) and not _usb(e)]
    return {**cfg, "capture": capture}


def app_config() -> dict:
    """Effective config as the app sees it (overlay; wireless-only when installed)."""
    import paths
    cfg = paths.load_config()
    return wireless(cfg) if paths.FROZEN else cfg


def endpoints(cfg: Mapping, env: Mapping[str, str] | None = None) -> list[Endpoint]:
    """Configured priority order plus optional current Wi-Fi/hotspot endpoint."""
    capture = cfg.get("capture", {}) if isinstance(cfg, Mapping) else {}
    raw = capture.get("endpoints", []) if isinstance(capture, Mapping) else []
    out = [ep for ep in (_entry(x) for x in raw) if ep is not None]
    if not out and isinstance(capture, Mapping):
        host = str(capture.get("host") or "").strip()
        url = str((cfg.get("screen", {}) or {}).get("url") or "").strip()
        if host and url:
            out.append(Endpoint("configured", host, url.rstrip("/")))
    tailnet = str(capture.get("tailnet") or "").strip() if isinstance(capture, Mapping) else ""
    if tailnet:                                   # `smriti link set-tailnet` override
        ep = tailnet_endpoint(tailnet)
        out = [ep] + [x for x in out if x.name != "tailnet"]
    env = env if env is not None else os.environ
    saved = capture.get("wifi_ip", "") if isinstance(capture, Mapping) else ""
    wifi = _wifi(env.get("SMRITI_RM2_WIFI_IP") or saved)
    if wifi and all(ep.host != wifi.host for ep in out):
        out.append(wifi)
    return out


def select(candidates: Sequence[Endpoint], probe: Callable[[Endpoint], bool]) -> Endpoint | None:
    """First healthy endpoint in explicit priority order."""
    return next((ep for ep in candidates if probe(ep)), None)


def reachable(endpoint: Endpoint, timeout: float = 4.0) -> bool:
    """Bounded non-interactive SSH health check. No tablet state changes.
    stdin is explicitly DEVNULL: the daemon rewires fd 0 (monke._detach_stdin)
    and an inherited, re-pointed handle broke the frozen exe's probe
    (2026-10-11: `smriti run` -> 'no rM2 link reachable' while link-test passed)."""
    try:
        p = subprocess.run([*ssh_argv(endpoint.host, "-n", "-o", "BatchMode=yes",
                                     "-o", f"ConnectTimeout={int(timeout)}"), "true"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout + 2)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[link] {endpoint.name} probe error: {e!r}", file=sys.stderr, flush=True)
        return False
    if p.returncode != 0:
        tail = p.stderr.decode(errors="replace").strip().splitlines()[-1:] or [""]
        print(f"[link] {endpoint.name} ssh rc={p.returncode}: {tail[0][:200]}", file=sys.stderr, flush=True)
    return p.returncode == 0


def activate(cfg: dict, endpoint: Endpoint) -> Endpoint:
    """Point this process's existing capture/screen paths at a selected link."""
    capture = cfg.setdefault("capture", {})
    screen = cfg.setdefault("screen", {})
    capture["host"] = endpoint.host
    screen["url"] = endpoint.screen_url
    os.environ["SMRITI_TABLET_HOST"] = endpoint.host
    os.environ["SMRITI_SCREEN_URL"] = endpoint.screen_url
    return endpoint


def report(cfg: Mapping, env: Mapping[str, str] | None = None,
           probe: Callable[[Endpoint], bool] | None = None) -> list[tuple[Endpoint, bool]]:
    """Probe all configured endpoints; unlike resolve(), never stops early."""
    check = probe or reachable
    return [(endpoint, bool(check(endpoint))) for endpoint in endpoints(cfg, env)]


def resolve(cfg: dict, env: Mapping[str, str] | None = None,
            probe: Callable[[Endpoint], bool] | None = None) -> Endpoint | None:
    """Find and activate the first healthy configured rM2 path."""
    selected = select(endpoints(cfg, env), probe or reachable)
    return activate(cfg, selected) if selected else None
