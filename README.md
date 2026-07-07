# Smriti

Tom Riddle's diary on a reMarkable 2. Write by hand → **Monke** (an AI persona)
reads the page → inks a reply back in handwriting — real, saved notebook ink.

Text → glyph raster (Pillow) → Zhang-Suen skeleton → stroke trace → **synthetic
stylus events** injected on the device; xochitl draws them. No display hacks,
no rm2fb. Technique from [riddle](https://github.com/MaximeRivest/riddle),
reimplemented in Python for RM2 (armv7).

| | |
|---|---|
| ![cursive](docs/example-full-page-cursive.png) | ![captured](docs/example-captured-loop.png) |
| engine output (cursive style) | a page as Monke sees it (captured from pen events) |

## The loop

```
you write on the tablet
   └─ pen events stream to the host over ssh (no device-side server)
      └─ 2.8s idle → page committed → cropped image + persona + last 6 turns
         └─ vision LLM (Gemini free tier / LM Studio / any OpenAI-compatible)
            └─ reply → handwriting strokes → injected back as real ink
```

### The floating eye (in-notebook UI)

With xovi + qt-resource-rebuilder (one click in reManager), Smriti draws a
**floating eye** at the bottom-right of every notebook page — screen UI,
not ink: the pen and eraser can't touch it, and it never costs a lamp
round trip. Install: copy `device/xovi/smriti-eye.qmd` into
`/home/root/xovi/exthome/qt-resource-rebuilder/` and restart xochitl.

The workflow:

1. Open a notebook. Eye shows a **dash** — daemon idle, nothing captured.
2. **Tap the eye** → session starts: the page is screenshotted, the
   workarea parser finds the empty space, Monke greets you there in ink.
3. Write; pause ~3s → your strokes + page context go to the AI endpoint;
   the greeting fades (erased) and the answer is inked into free space —
   prose in handwriting, `$$...$$` LaTeX and circuitikz typeset as real
   previews, never raw code.
4. **Tap the eye again** → session ends, eye shows the dash.

The eye reflects true daemon state (polled every 3s through
`/home/root/.smriti/state`). Online-only by design: tablet ↔ Pi over
tailscale or USB; the Pi does all processing and AI routing.

### Sessions — the corner eye

The daemon boots **idle**: nothing captured, no AI. The bottom-right corner
is the eye — a touch zone (and optionally a drawn ink marker,
`marker_ink = true`; off by default since every marker redraw costs an
ssh+lamp round trip and erases real ink in the corner).

- **Tap the corner** → session starts: Monke scans the visible page
  (screenshot), inks a short greeting below your ink, then watches.
- Write → pause ~3s → reply inks below your writing. Placement uses a
  **workarea parser** on a real screenshot: replies land in free bands
  between existing ink, page-turn only when nothing fits.
- **Hold the corner ~1s** → session off. No laptop needed.
- From the tablet shell: `smriti-eye start|stop|status` (talks to the
  daemon's control port over the tailnet). Same via
  `curl http://<daemon-host>:7333/start|stop|status` from anywhere.

### Smriti toolbox app (xovi/AppLoad, drawn UI — no ink)

With [xovi](https://remarkable.guide/guide/software/xovi.html) +
[AppLoad](https://github.com/asivery/rm-appload) on the tablet (one-click
via reManager), Smriti ships a launcher app: a big eye you tap to
start/stop sessions, plus live daemon state. Pure screen UI — zero lamp
round trips, nothing drawn on your notebook page.

```
device/appload/smriti/   manifest.json + icon + resources.rcc (QML UI)
device/bin/smriti-eye-watch   bridge: app <-> daemon (file cmd/state)
```

Install (from this repo):

```sh
scp device/appload/smriti/{manifest.json,icon.png,resources.rcc} \
    root@rm2:/home/root/xovi/exthome/appload/smriti/
scp device/bin/smriti-eye-watch root@rm2:/home/root/.vellum/bin/
scp device/xovi/smriti-eye-watch.service root@rm2:/etc/systemd/system/
ssh root@rm2 'chmod +x /home/root/.vellum/bin/smriti-eye-watch &&
  systemctl daemon-reload && systemctl enable --now smriti-eye-watch &&
  systemctl restart xochitl'
```

Rebuild the UI after editing `ui/smriti.qml`:
`uvx --from pyside6 pyside6-rcc --binary application.qrc -o resources.rcc`.
How it talks: the QML frontend reads/writes `/home/root/.smriti/{state,eye-cmd}`
via XHR `file://`; `smriti-eye-watch` forwards commands to the daemon over
`tailscale nc` and refreshes the state file. (systemd units on the rootfs
are wiped by OS updates — rerun the install lines after updating.)

Optional riddle mode (`fade = true`): your words dissolve, the answer
appears in their place, lingers, dissolves — page returns clean.

## Quick start (daemon on your PC)

```sh
uv sync
uv run python host/ink.py selfcheck
uv run python host/monke.py          # dash appears; tap it to start a session
```

Manual writing without the daemon:

```sh
ssh rm2 '/home/root/.vellum/bin/smriti-write "hello monke"'
uv run python host/write.py "hello monke" --style cursive
```

## Full setup on a NEW system

Three pieces: tablet package (once per device), host daemon (PC or server),
AI endpoint (any).

### 1. Tablet (reMarkable 2, once)

ssh access assumed (password in Settings → General → Help → Copyrights).

```sh
# on the RM2 — install vellum (package manager, lives in /home/root/.vellum):
wget --no-check-certificate -O bootstrap.sh https://github.com/vellum-dev/vellum-cli/releases/latest/download/bootstrap.sh
echo "18a4b0123160a1b547fa9f396005ce8c9caf2330bf3ff6fa39bb2eb27891cca8  bootstrap.sh" | sha256sum -c && bash bootstrap.sh
exec bash --login

# trust the Smriti apk repo + install:
cd /home/root/.vellum/etc/apk
wget -q --no-check-certificate -O "keys/naivedya.sahu2@gmail.com-6a4bae6f.rsa.pub" \
  "https://naivedya-sahu.github.io/Smriti/naivedya.sahu2@gmail.com-6a4bae6f.rsa.pub"
echo "https://naivedya-sahu.github.io/Smriti" >> repositories
vellum update && vellum add smriti
```

Updates forever after: `vellum update && vellum upgrade`. After a reMarkable
OS update: `vellum reenable`.

### 2. Host — PC, Pi 5, or any Linux VPS

```sh
git clone https://github.com/Naivedya-sahu/Smriti ~/smriti
~/smriti/deploy/setup-server.sh     # uv, venv, keys, systemd unit, checklist
```

The script prints a 5-step checklist; the only manual parts are pasting the
generated pubkey into the tablet's `authorized_keys` and making `ssh rm2`
resolve on YOUR network (USB `10.11.99.1`, LAN IP, or tailscale —
`vellum add tailscale` exists on the tablet; note: userspace networking).

```sh
sudo systemctl enable --now smriti-monke@$USER    # always-on daemon
journalctl -fu smriti-monke@$USER
```

### 3. AI endpoint

`[ai]` in [config.toml](config.toml) — anything speaking
`/v1/chat/completions` with vision. Selection order: env overrides
(`SMRITI_AI_BASE_URL` / `SMRITI_AI_MODEL` / `SMRITI_AI_KEY`) → `[ai]` →
`[[ai_fallback]]` chain, tried in order on any error (default chain:
LM Studio over the tailnet, then a free hosted VL model). Swap models by
editing config.toml — no code changes. Measured on this project:

| Endpoint | Vision latency | Cost |
|---|---|---|
| Gemini flash-lite (default) | ~1.8s | free tier |
| LM Studio, qwen3-vl over tailnet | ~7s | free, offline |
| Groq / OpenRouter | 1-3s | free tiers |

Key goes in env `GEMINI_API_KEY` (Windows: `setx`, read from registry too;
Linux: `~/.config/smriti/env` used by the systemd unit).

With every machine on one tailscale tailnet (tablet runs `vellum add
tailscale`, userspace networking), the same config works from anywhere:
the Pi daemon reaches the desktop's LM Studio (`http://<desktop-ts-ip>:1234/v1`)
and the tablet's screen service (`SMRITI_SCREEN_URL=https://<rm2-ts-ip>:2001`)
with no port forwarding.

**Hermes seam:** the same `[ai]` block is the backend toggle — when a Hermes
agent (memory/KB spine on the Pi) exposes an OpenAI-compatible endpoint,
point `base_url` at it and Monke routes through Hermes. No code change.

## Testing each layer

Every layer has its own CLI — test them independently when something breaks:

| Layer | Command (host repo) | Expect |
|---|---|---|
| tablet ssh | `ssh rm2 'echo ok'` | ok |
| pen events | `uv run python host/capture.py -o page.png` | write → PNG |
| touch decode | `uv run python host/capture.py --touchtest` | finger coords |
| screenshot | `uv run python host/screen.py shot out.png` | current screen |
| workarea | `uv run python host/screen.py bands` | free bands |
| AI chat | `uv run python host/oracle.py ask "hello"` | reply (falls through chain) |
| AI vision | `uv run python host/oracle.py see page.png "read this"` | transcription |
| ink output | `uv run python host/write.py "hi" --style cursive` | ink on tablet |
| LaTeX | `uv run python host/tex.py` (see file docstring) | preview PNG |
| daemon | `curl http://<daemon-host>:7333/status` | watching / idle |
| tablet ctl | `smriti-eye status` (on the RM2) | watching / idle |

## Watching the screen remotely

[goMarkableStream](https://github.com/owulveryck/goMarkableStream) — single
static binary on the tablet, streams the live screen to any browser
(firmware 3.24+, works over tailscale). Useful for verifying Monke's ink
without picking the tablet up.

## Styles / releasing

Styles = font + size + weight + slant + pressure in [styles.toml](styles.toml)
(Dancing Script, Patrick Hand — SIL OFL). Release pipeline:

```sh
deploy/release.sh "message"   # fonts → pkgrel bump → signed apk → gh-pages
```

Device picks it up with `vellum upgrade`. Docker Desktop must be running.

## Layout

```
host/monke.py      the daemon: sessions, eye marker, loop, fade, persona
host/capture.py    pen/touch streams → strokes → page PNG; screenshot +
                   workarea parser (ink floor, free bands)
host/ink.py        text → handwriting strokes; compiles .sf stroke-fonts
host/oracle.py     AI provider (any OpenAI-compatible endpoint)
host/write.py      manual writer (dev tool)
device/            what the apk installs: smriti-write, replay.awk,
                   patched lamp (pen-event injector), stroke-fonts
deploy/            release.sh, setup-server.sh, systemd unit
```

lamp source: Elxnk-era rmkit tool (Archive), patched: distance-scaled event
interpolation for pen AND eraser (~10-20x faster than stock). Rebuild:
debian container + g++-arm-linux-gnueabihf + `pip install okp` + `make compile`.

## Troubleshooting

- **No ink**: notebook page open? `vellum info smriti` on tablet? `ssh rm2 'echo ok'`?
- **Tap does nothing**: daemon running? (`systemctl --user status smriti-monke`
  on the server). Tap must land on the corner marker zone and be short —
  a ≥1s press is "session off".
- **Strokes ignored right after a reply/tap**: the daemon drains its own
  ink echo for ~1s — wait for the open eye before writing.
- **Reply overwrites old ink**: session-start scan needs goMarkableStream
  reachable (`SMRITI_SCREEN_URL`); without it placement falls back to the
  persisted floor.
- **No greeting on tap**: AI endpoint unreachable — check `[ai]` /
  `[ai_fallback]` and the journal log.
- **Upgrade sees nothing**: GitHub Pages CDN caches ≤10 min.
- **Writing looks chunky**: `waypoint_step = 2` in config.toml.
