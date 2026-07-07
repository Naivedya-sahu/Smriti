# Changelog

## v0.1.7 — fonts, sizing, on-device maths (2026-07-08)
- **Custom fonts**: drop any ttf/otf in `fonts/`, add a `[style.<name>]`
  block — documented recipe in styles.toml. Ships `cartoon` (Comic Neue,
  OFL). `[monke] reply_size` / `reply_line_height` override size + spacing
  per config, no style edit. `write.py --out preview.png` renders any style
  without a tablet.
- **input_fade**: the user's question is erased with the greeting when the
  answer lands, so the reply reuses that space.
- **pdflatex resolution fixed**: tex.py finds pdflatex via SMRITI_PDFLATEX,
  PATH, or user-space `~/.TinyTeX`. The Pi daemon (systemd, no login PATH)
  couldn't see pdflatex before — that was the real cause of circuits
  degrading to "see log", not the AI model. setup-server.sh now installs
  TinyTeX + circuitikz automatically (SMRITI_NO_TEX=1 to skip).
- Verified on-device from the system alone (no AI): Comic Neue font at a
  custom size, a typeset equation, and a full series-RLC circuitikz
  drawing all inked cleanly.

## v0.1.6 — floating eye + diff guard + SVG (2026-07-07)
- **Floating in-notebook eye** (xovi qmldiff patch): drawn UI bottom-right
  of every notebook page — dash=idle, pupil=watching, tap toggles the
  session. Screen UI, pen/eraser can't touch it. Plus AppLoad toolbox app
  and `smriti-eye` CLI; all bridged to the daemon over the tailnet.
- **Backfeed killed twice**: quiet-window echo drain AND a screenshot
  baseline diff — a commit with no visual change vs the page after
  Smriti's last write is discarded as echo (`screen.page_diff`).
- **Greeting fades**: erased when the first answer lands or session ends.
- **SVG sketches**: AI can emit `<svg>` blocks → drawn as pen strokes
  (`host/svg.py`, zero deps). LaTeX stays for maths/circuits/block
  diagrams. Source of every block logged to ~/.config/smriti/blocks.log —
  the page only ever shows the rendered preview, never code.
- **Screenshot layer split** to `host/screen.py` with self-healing grab
  (corrupt gms frames auto-restart the stream service and regrab).
- **Fallback chain**: `[[ai_fallback]]` entries tried in order
  (LM Studio tailnet → free OpenRouter VL by default).
- Installers split: `deploy/install-tablet.sh` (all tablet pieces) +
  `deploy/setup-server.sh` (daemon); per-layer test table in README.

## v0.1.5 — sessions + workarea (2026-07-07)
- Session UX: daemon boots IDLE (dash marker). **Tap the eye** → scan page
  (screenshot) + short Monke ink greeting + watch. **Hold ~1s** → session off.
- Eye marker: open eye (circle+pupil) = watching, filled = thinking, dash = idle.
- Workarea parser (`capture.free_bands`): screenshot → free horizontal bands;
  replies placed into the first band that fits instead of blind floor+50.
  `python host/capture.py --workarea` = live debug.
- Endpoint selection documented + retargeted for the tailnet: primary Gemini
  (now fast from the Pi — old ~540s hang gone), fallback = desktop LM Studio
  `qwen/qwen3-vl-8b` over tailscale IP. Env override order in config.toml.
- oracle: IPv4 preferred (fixes the Pi→Gemini IPv6 blackhole hang); 60s
  timeout so the fallback actually gets a turn.
- HTTP control plane (:7333 /start /stop /status) + on-tablet `smriti-eye`
  CLI (rides `tailscale nc` — userspace tailscaled has no tun for wget).
- Ink eye marker now optional and OFF by default (`marker_ink`) — each
  redraw cost an ssh+lamp round trip and erased real corner ink; the corner
  gesture zone works without it.
- Pen stream (event1) opened only while a session is on — hover flooded
  the ssh link even when idle.
- capture: `--touchtest` (live finger coords, both x orientations),
  `--workarea` (live floor + free bands).
- **Smriti toolbox app** (xovi/AppLoad): drawn launcher UI — tap the big
  eye to start/stop sessions, live daemon state. No ink, no lamp round
  trips. QML frontend + `smriti-eye-watch` file bridge
  (`/home/root/.smriti/{eye-cmd,state}`) + `tailscale nc` to the daemon.
- Experimental (repo only, not deployed): `device/xovi/smriti-eye.qmd` —
  floating in-notebook eye via qt-resource-rebuilder qmldiff patch.

## v0.1.4 — the loop (2026-07-06 → 07-07)
- Monke daemon: write → idle commit → vision LLM → handwritten ink reply.
- Ink status marker (bottom-right): hollow=watching, filled=thinking, dash=paused; finger-hold ~1s toggles.
- Conversation memory (last 6 turns of page images + replies).
- LaTeX + CircuiTikZ in replies: `$$...$$` typeset to pen strokes (tex.py, MiKTeX + pypdfium2).
- Real screenshots via goMarkableStream `/screenshot` (JWT); placement (ink floor, page-turn landing, overflow) verified against actual pixels; page-full degrades to log.
- Screen-as-context: vision input = real page screenshot with fresh strokes overlaid red.
- Providers: any OpenAI-compatible; `[ai_fallback]` chain; env overrides `SMRITI_AI_BASE_URL/MODEL/KEY`, `SMRITI_SCREEN_URL`.
- Pi 5 deployment: `deploy/setup-server.sh`, systemd user service; tablet reached over tailnet (`ssh rm2-smriti`).
- Riddle fade mode built, disabled pending visual verify.

## v0.1.3 — oracle (2026-07-06)
- oracle.py: zero-dep OpenAI-compatible chat+vision (urllib). LM Studio local + Gemini tested.

## v0.1.2 — pen capture (2026-07-06)
- capture.py: wacom evdev streamed over plain ssh (no device-side server); strokes → PNG on idle.

## v0.1.1 — engine + packaging (2026-07-06), r0→r5
- ink.py: text → raster → Zhang-Suen skeleton → stroke trace → pen strokes. Styles: cursive (Dancing Script), print (Patrick Hand), cursive-bold.
- Device: busybox sh+awk replayer + `.sf` stroke-fonts; lamp = pen-event injector (real xochitl ink), patched: distance-scaled interpolation, pen r4 + eraser r5 (~10-20× faster).
- Packaging: VELBUILD apk via abuild-in-docker; signed repo on gh-pages; `vellum upgrade` delivers. Round-trip proven (style change → upgrade → new hand).
