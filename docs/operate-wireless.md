# Wireless operation

Smriti resolves tablet routes in this order: **Tailnet → USB → saved Wi-Fi/hotspot**. Every candidate is probed over SSH; capture URL and eye tunnel use the same selected route.

## Installed Windows app

```text
smriti link-test              # read-only: report every configured route
smriti link set-tailnet <ip-or-magicdns>
smriti link set-wifi <ipv4>
smriti link scan --save       # bounded private-local scan; saves only verified rM2
smriti doctor
```

Hotspot IP addresses change. Prefer `link scan --save` or set the current tablet IP explicitly. The scanner never probes the Internet, Tailscale range, USB subnet, loopback or arbitrary address ranges.

## Eye transport

Tablet QML calls `127.0.0.1:7333`. The Windows daemon keeps a reverse SSH forward:

```text
rM2 127.0.0.1:7333 → SSH reverse tunnel → Windows daemon 127.0.0.1:7333
```

Do not replace this with a laptop Tailnet IP. rM2 Tailscale is userspace-only; normal tablet processes cannot open direct sockets to Tailnet peers.

## Start

1. Run `smriti link-test` and correct route/key failures.
2. Run `smriti doctor`.
3. Start Smriti from the tray or `smriti run`.
4. Open AppLoad → Smriti on the tablet, then activate it.
5. Open a notebook. Closed eye = idle; tap it for watching.

## Stop — safety order

1. Deactivate Smriti / disarm tablet.
2. Verify stream is inactive.
3. Stop the host daemon.

The tray enforces this order. Do not kill the daemon first: a live tablet session would retain a stale UI state.

## Power

`smriti-powerwatch` measures tablet battery state. Run it only while unplugged; charging status invalidates drain measurements. A manual remote hold keeps Tailscale up for remote development; remove it when no remote path is needed.
