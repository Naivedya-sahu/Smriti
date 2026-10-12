# Smriti — tablet subproject (RM2, armv7)

Everything that lives ON the reMarkable 2. Self-contained: this directory
is the future standalone repo if the tablet app grows its own life
(UI/UX, feature upgrades) — nothing in here imports from `host/`.

| piece | what | ships via |
|---|---|---|
| `smriti-write`, `replay.awk`, `fonts/*.sf` | busybox handwriting replayer + compiled stroke fonts | vellum apk (`VELBUILD` at repo root, `deploy/release.sh`) |
| `bin/lamp` | pen/touch/eraser event injector (patched, distance-scaled) | vellum apk |
| `bin/smriti-eye`, `bin/smriti-eye-watch`, `xovi/smriti-eye-watch.service` | session control CLI + QML↔daemon file bridge | `deploy/install-tablet.sh` |
| `xovi/smriti-eye.qmd` | floating in-notebook eye (qmldiff patch) | `deploy/install-tablet.sh` |
| `qmd/smriti-ask.qmd` | experimental lasso Ask patch — **not deployed**; current source is rejected by tablet qmldiff | do not install until rewritten and tested |
| `appload/smriti/` | toolbox app (AppLoad frontend, `ui/smriti.qml` → `resources.rcc`) | `deploy/install-tablet.sh` |

The host daemon only ever touches the tablet through: ssh (evdev streams,
lamp stdin, metadata grep), goMarkableStream :2001 (screenshots), and the
`smriti-eye` control files. Keep that boundary — it is what makes this
directory extractable.

Rebuild `resources.rcc` after editing `ui/smriti.qml`: pyside6-rcc
(Qt6-compatible with the device's Qt — see HANDOFF notes on rcc/Qt
mismatch producing a blank app).

Daemon address + control token on the device:
`/home/root/.config/smriti-eye` — `SMRITI_DAEMON=ip:7333`,
`SMRITI_TOKEN=<token>` (matching the daemon's SMRITI_CONTROL_TOKEN).

OS updates wipe `/etc/systemd/system` → rerun `deploy/install-tablet.sh`
and `/home/root/gomarkablestream install`.
