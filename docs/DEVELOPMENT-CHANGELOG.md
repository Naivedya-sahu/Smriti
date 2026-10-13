# Changelog

## v0.6.0 (2026-10-13) — page context: regions, selection, scroll rebasing

- **Release assets:** Windows installer `Smriti-Setup-0.6.0.exe` and the rM2 Vellum package are attached to the GitHub release (see the release page install guide).
- **Live rM2 run (2026-10-13):** circuit-in-this-area regions, region follow-ups, scroll continuation and page-bottom waiting worked on device. Known issues found live, fixed in 0.6.1/0.7: Monke can re-read its own fresh reply as a new prompt (repeat/overlap); a confirm step waits for an eye tap without a visible cue; an out-of-room reply is logged, not inked, with no on-page cue; lasso-then-pen does not ground on the selection (selection is gone by the time the prompt is read); `pi` is drawn as letters in graph text; very small text is hard to read; writing during a reply is not isolated from the reply's injected strokes.
- **Scroll-aware continuation/rebasing:** reply units stay in page-coordinate units; matched offsets detect view moves; page bottom waits for a still view for 2 s; a measured move never redraws old ink; partial units retry only missing strokes; `page_full` stops the reply. `[monke] page_height` sets the page end (0 = no end).
- **Semantic regions:** stable per-page `R` ids classify Navy and Monke ink by kind; reader and writer receive a `<regions>` block capped at 700 characters; deictic references resolve to a region; directives accept `@target R3`.
- **Selection grounding:** firmware 3.27.1.0 probes `sceneController.boundingRect` and `itemsBoundingRect`; reader and writer receive a `<selection>` block capped at 500 characters, with focus and cleaned crop. An active selection beats regions/deixis, is never deselected, and unsupported probes use a negative cache.
- **One page↔screen contract:** `vectors.to_screen()` and page inverses apply registered-offset scale consistently.
- **Validation:** 634 unit tests OK; 30/31 simulations clean (`pause_mid_prompt` remains pre-existing).
- **Known limits:** before a scrolled prompt offset registers (about 10 s), view-field context fallback is safe against inking over Navy but may be wrong; the selection-rectangle coordinate frame under a real selection is not measured; region clustering is quadratic on very sparse pages (2000 isolated strokes: about 430 ms).

## v0.5.0 (2026-10-10) — host complete; RM2 validation pending

- **Brain:** ChatGPT direct Route B is the default (`gpt-5.6-luna`); one `host/brain.py` entry point; no local inference, LM Studio, Qwen, Gemini, polling, or automatic local fallback. Claude remains an explicit selectable route only.
- **Speed/cost:** direct host-path inference measured 3.45 s, 34 input / 29 output tokens; direct probe first-text 1.2–1.8 s, total 1.4–2.8 s. Hermes CLI remains a slower fallback transport.
- **Robust output:** ChatGPT math blocks such as bare `aligned` are normalized before TeX render; a live answer rendered 77 strokes in 2.4 s.
- **Turn system:** usage estimation for interrupted streams; offline `host/replay.py`; shared writer prompt; `split|merged` read/write mode, with merged routing and lasso protocol simulation covered.
- **Page intelligence:** Smriti Page Protocol v1, target metadata, directive stripping, Set-of-Mark labels, and collision-safe beside placement.
- **Code-first refine:** `beautify.py`, `place.py`, `som.py`, and `jitter.py` refine graphs/circuits/shapes without a model call where safe; low-confidence circuits fall back to the writer.
- **Power:** bridge tailnet start now requires explicit `remote-hold`; it does not start merely because Smriti is armed. Device copy verified; `tailscaled` inactive after cleanup.
- **Validation:** 292 unit tests OK; 26/26 simulations clean.
- **Deployment status:** 0.5.0 host daemon was deployed, then intentionally stopped for quota/test pause. The experimental `smriti-ask.qmd` selection-menu patch was rejected by tablet qmldiff and removed; existing eye UI/xochitl recovered.
- **Checkpoint:** `archive/versions/smriti-0.5.0-2026-10-10b-deployed-daemon-qmd-rollback.zip`.

## Historical: v0.3.1-dev
- Vector hybrid: the page's own strokes (.rm per layer) join the screenshot; placement 0.35 s/frame (e474766)
- Page guard reads the live page id (1ba1cba); the view-change wait/hold is terminated (Navy)
- Notebook context: turns tagged by page, the reader gets the notebook's other pages (d082847)
- `#P` prompts: marker stripped before answering; the prompt's strokes are deleted once the reply inks, only when the page vectors prove exactly those strokes are selected (7937f83, ae346d0) ✔RM2
- `#P` erase fires on "reply inked", not on the floor moving: text-reply prompts were never erased (67075aa)
- Page-turn "Page n of m" label + scrollbar masked as UI: a blank new page read floor 1849 (cf29e1e)
- Pen stream opens before the greeting; writing right after it was lost (eye update took 14 s, then the queue was drained)

## v0.3.0 (2026-09-30)
One line per commit; device-verified items marked ✔RM2.
- Live-test fixes (2026-09-30): greeting one line; one ssh channel (confirm poll 0.45 → 0.015 s) ✔RM2; screen from xochitl memory, gms fallback only (grab 0.53 s) ✔RM2; xovi + LM Studio auto-start; budget tiers sonnet → haiku → local, Gemini dropped; eye "!" on a failed model call
- Bridge watchdog detached from the reply pipe: 10 s → 0.03 s per eye call ✔RM2 (b2abbb6)
- Layers probe counts pixels on RGB frames and accepts a blank page: 6/6 ✔RM2 (c91eb2b)
- Screen keeps native RGB ✔RM2; bend-keeping pen decimation; `workspace.observe()` contract, not wired yet (0c04af8)
- Greeting + LaTeX repair follow the app writer ✔RM2 (greeting); confirm edit replaces the stale summary; reader JSON parse tolerates trailing prose (dba22b5)
- Config: Qwen dropped; local endpoint kept for the personal model; fallback = Gemini only
- Dot-grid validity accepts a period on either axis: a clean page with erase residue was rejected (584fb41)
- Greeting on its own layer, faded by dropping the layer: 95 s + residue → 3.8 s, exact ✔RM2; decimated erase fallback; finer QML poll (17db564)
- Eye command poll 1000 → 150 ms: QML call 1.0 → 0.43 s; greeting ink 19.7 → 11.6 s, fade 2.1 s; layers probe 6/6 ✔RM2
- LaTeX repair checks blocks as the renderer compiles them: no model call for valid maths (2864322)
- `$$\[..\]$$` unwrapped before compiling: the 8 V answer and f_c were silently dropped on device (3fe4dbc)
- Unreachable tablet keeps the last known app route (was: silent fallback to local/Gemini)
- Short `$$maths$$` inside a sentence stays in the sentence: 2 turns per page, full-size (a3dd7b4) ✔RM2
- Reply inked only while its question is still on screen (page-turn race) (content safety) ✔RM2 (4652730)
- Layer switch/restore read state back in the same QML call: ink 11.6 → 7.9 s (14 → 5 calls) ✔RM2
- Stream-ink: a Claude reply inks line by line while it is written; first ink ~11 s after the write starts ✔RM2 (6bfbaeb, 7ac9e21)
- One persistent ssh shell for lamp + QML; one verify frame for prose + drawing; bends-only lamp paths: rich reply ink 16.5 → 11.6 s ✔RM2 (d189910, 777a042)
- Reader streams and stops at its complete JSON (78de7f2) ✔RM2
- Eye: one request object per polled file — fixes a QML leak that OOM-killed xochitl (6.5 MB/min at 150 ms) (c98e989) ✔RM2

## v0.2.0 — local-first AI, Monke layer, one-package install (2026-09-26)
First published v0.2 (the two sections below were built in July, never shipped).
Device-verified on RM2 fw 3.27.1.0 over USB unless marked.
- **Local-first AI (offline)**: primary = LM Studio Qwen3-VL-4B on the daemon
  box; desktop / Gemini / Claude are fallbacks. Eval (`harness.py eval`):
  5/5 content kinds, 90% keywords, ~20 s/page on an RTX 3050 4 GB.
- **Reading harness** (`host/harness.py`): focus crop (dot grid removed,
  768 px) + small overview (384 px — the big one tripled latency), stage-1
  structured JSON reading, per-media skills, stage-2 text-only reply, LaTeX
  compile + one model repair. Line OCR demoted to an optional hint (it
  garbled circuits/graphs/maths).
- **Confirm window**: the eye pops a window with what Monke read (editable,
  on-screen keyboard) + Confirm / Discard before drawings and selections
  (`[monke] confirm`). Selection endpoint `/focus?action=&box=` +
  `smriti-eye focus` ready; lasso-menu qmd anchor still TBD.
- **Monke layer (content safety)**: every Smriti stroke and eraser pass goes
  on the page's "Monke" layer; your layers are never touched, a bad reply
  clears in one call, no verified layer switch = no ink (`[monke]
  safe_layer`, forces input_fade off). `host/layers.py probe` = on-device
  behaviour suite.
- **Colour + graphs**: pen colour set from QML (black/gray/white/blue/red,
  real colours in exports). `host/graph.py`: the model reads `y = ...`,
  Python plots it; `add y = ...` overlays the new curve on the same axes in
  the next colour + dash.
- **One package for the tablet**: the smriti apk now carries the ink engine,
  bridge + service, CLIs, floating eye and the Smriti app; `smriti-setup`
  runs from apk hooks and vellum's post-OS-upgrade hook. reManager (own
  repo) or `deploy/install-tablet.sh` = `vellum add/upgrade`.
- **Offline eye**: `smriti-eye` talks plain TCP to USB/LAN daemons (was
  tailnet-only); the daemon writes its own address into the tablet on start.
- **Install fixes**: `install-daemon.ps1` didn't parse on PowerShell 5.1
  (em-dashes, no BOM) — ASCII now; installers use the checkout they run
  from; LM Studio model step. Screenshot gate no longer rejects inked pages.

## v0.2.0-pre — manual master switch, power gate (unreleased)
- **Manual control**: Smriti is inert until the drawer app arms it (writes
  `/home/root/.smriti/armed`). The floating eye only appears in notebooks
  while armed; **Deactivate Smriti** in the app turns everything off. Boots
  disarmed — opening a document no longer auto-starts anything.
- **Power gate**: `goMarkableStream` off boot/xochitl; `smriti-eye-watch`
  starts it on arm, stops it on disarm (and ends the session).
- **Tailnet auto-gate**: `tailscaled` is boot-disabled and reconciled by the
  bridge to `(armed AND internet reachable) OR a manual hold` — arming with the
  internet live brings it up, disarming (or losing internet) drops it. New
  `device/bin/smriti-remote on|off|status` sets/clears the hold for remote dev /
  external fetches (survives disarm; persists across reboot until cleared).
  Bridge status re-sync throttled 30 s → 120 s to cut radio wakeups.
  `install-tablet.sh` applies the gate, ships `smriti-remote`, and holds the
  tailnet across a tailscale install so it can't sever itself.
- **Document-write mode: prototyped, then deferred.** Encoding the reply to a
  v6 `.rm` and appending it as a new page works and is safe (never touches
  existing pages/PDF), but every append needs a **full xochitl restart** to
  render — no lighter reload exists, and lamp-injected touch can't drive the
  close/reopen UI on this build. Kept as `lamp`-only; the working code and full
  findings are archived (`docs/DOCWRITE-DEFERRED.md`).
- **Smarter reply placement**: `_place` now fits the reply into the largest
  real free band and scales the handwriting down (to a legibility floor) only
  when it would overflow — full size when there's room, shrink to fill a tight
  gap instead of dropping straight to a page-turn. Page-turn only when even the
  smallest size fits nowhere.
- **Docs**: README sections for manual control, power, and reMarkable OS/xovi
  update compatibility (qmldiff anchors are the main update-time break).

## v0.2.0-pre — Claude brain, notebook sessions, local OCR (built 2026-07-16, unreleased)
- **Claude is the primary AI** via the Claude Code CLI (`claude -p`):
  uses the subscription (no API key), `[ai] provider = "claude-cli"`.
  One Claude session **per RM2 notebook** (detected from xochitl metadata),
  resumed with `--resume` — conversation context lives server-side, the
  old 6-screenshot history replay is gone. Fallback chain unchanged
  (Gemini → LM Studio → OpenRouter) when Claude is missing/limited.
- **scribe** (`host/scribe.py`): local handwriting OCR service
  (TrOCR-handwritten; `uv sync --extra scribe`, GPU boxes). The daemon
  reads your strokes as a CLEAN render → OCR → **text-only** AI turns.
  OCR down/empty or you write "monke look" → the page image goes to
  Claude directly (vision fallback).
- **Session-context decision tree** at eye-tap: blank page / unchanged
  page (resume silently) / unknown ink (OCR the changed region, or attach
  it as one cropped image).
- **KB/Elxanima exports**: `sessions/<notebook>/` gets a running
  `transcript.md` plus every raw `$$…$$` / circuitikz / SVG block as its
  own file (`blocks/eq-001.tex`, `circuit-002.tex`, `sketch-003.svg`).
- **Screenshot validity gate** (`screen.looks_valid`): white-stats +
  noise + dot-grid autocorrelation checks; placement facts never come
  from a corrupt/offset frame (falls back to stroke floor).
- **Control port token**: set `SMRITI_CONTROL_TOKEN` and `/start|/stop`
  require it (`?token=` / header) — a start spends AI quota now.
- **Install refactor**: role installers — `install-daemon.sh/.ps1`
  (laptop = primary; `--systemd` = Pi/server), `install-scribe.sh/.ps1`
  (GPU box, CUDA torch, autostart), interactive setup wizard
  (`host/setup_wizard.py`), `device/` documented as a self-contained
  tablet subproject, `docs/UNINSTALL.md`.
- Fixes: `[screen]` user/password now actually reach the screenshot
  client; page-turn placement no longer treats an unreachable screenshot
  as an empty page (blind-ink guard).

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
