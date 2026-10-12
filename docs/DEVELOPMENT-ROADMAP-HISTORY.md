# Smriti — Roadmap

> **Current implementation marker — 2026-10-10.** Build **0.5.0** is complete on the host: Route B ChatGPT, replay/usage accounting, merged-turn option, SPP v1, Set-of-Mark, safe placement and code-first refinement. Unit suite: 292 OK; simulation: 26/26 clean. RM2 live validation is paused for model quota. The lasso Ask QMD experiment was removed after tablet qmldiff rejected its syntax. Current feature truth: [STATE.md](STATE.md); detailed plan: [docs/plan-roadmap-2026-10.md](docs/plan-roadmap-2026-10.md); ermine UI plan: [docs/plan-ermine-mascot.md](docs/plan-ermine-mascot.md).
>
> **Smriti** (स्मृति, "memory / that which is remembered") — the RM2 ink-diary assistant system.
> **Monke** — the persona/voice that lives in it. "Monke lives in Smriti."
> Created 2026-07-06 · Window: ~10–11 days (through ~2026-07-16, MSR ~mid-July).

---

## Future work — added 2026-09-26 (after v0.2)

**Layered inking per content kind.** v0.2 puts ALL Smriti ink on one "Monke"
layer (content safety — Navy's layers are never touched; eraser is
layer-scoped; one clear undoes Monke). Next: a layer per kind — drawing,
graph, handwriting — so each can be hidden, cleared or recoloured alone.
LIMIT: xochitl caps a page at 5 layers (6th addLayer silently ignored) —
budget Navy 1-2 + Monke <= 3; a full page = Monke refuses to ink.
Proven on device (fw 3.27.1.0): `sceneController.addLayer/setCurrentLayer/
setLayerName/setLayerVisible/clearLinesInLayer`, pen colour enum
(0 black · 1 gray · 2 white · 6 blue · 7 red), same-axes graph overlays in
colour (`host/graph.py`), probe suite `host/layers.py probe`. Known: layer ops
are async (verify by readback); `addLayer` also switches current layer late;
eraser removes large chunks (no erase-dither gradients — use pen density).

**Study + tutoring on the page — local model with an MCP toolset.** The local
VL model gets tools instead of free-form replies: plot(expr, overlay),
draw_circuit(netlist), typeset(latex), write(text), layer ops, page context
(screen layer, off-screen document layer via USB PDF export, transcript).
Tutoring loop attached to the screen: Navy writes, Monke reads (confirm
window), checks work step by step, marks mistakes on its own layer.

**Hermes agent = study state.** Hermes (memory/KB spine) holds the study
state across sessions: what was learned, open doubts, spaced-review items per
notebook/subject. Smriti reads/writes it through the same [ai] seam
(OpenAI-compatible endpoint) or a small tool API. Planned upgrade, not v0.2.

**Hermes as the Claude router (Navy 2026-09-26).** Hermes can invoke Claude
Code itself; Smriti would then call Hermes's OpenAI-compatible endpoint for
cloud turns instead of spawning `claude -p` (one place for auth, caps,
memory). v0.2 calls the CLI directly with an isolated profile + hard caps.
Measured 2026-09-26: `hermes -z --provider anthropic -m claude-haiku-4-5
--safe-mode -t ""` works (Hermes holds the OAuth), but adds ~17.5k tokens of
Hermes's own prompt per call (13.7k cache-read + 3.8k write) for a 6-token
answer. `hermes proxy` has no Anthropic upstream (nous, xai only). Revisit
when Hermes can run a minimal-prompt profile.

**OmniRoute = one endpoint for online models.** ChatGPT, Claude, Gemini
behind a single OpenAI-compatible router (OmniRoute), so the app's model
picker lists them next to LM Studio's local models. Planned release. v0.2
ships the picker for LM Studio models only (`models.json` from
`/api/v0/models`, type-aware: text-only models write replies, the vision
model keeps reading).

**Personal model (Navy 2026-09-26: "specific model trained on the use case,
personalised, near-live").** Cloud (Claude) tests showed the limits of a
general model + fixed scripts: token-saving modes skip the screenshot,
placement relies on hand-written stroke heuristics. Direction: fine-tune a
small VLM (Qwen3.5-2B/4B, Qwen3-VL-4B baseline) on Navy's OWN pages. The
confirm window already produces the labels: every Confirm/edit = (focus
crop, corrected reading) pair — log them to sessions/<nb>/train/ as the
dataset. LoRA on the 5060 (Unsloth/GGUF) or the Mac mini (MLX-VLM). Eval with
host/harness.py + the real-notes set. Research the training stack next.

**Also queued:** selection context menu (lasso → Monke action; daemon
`/focus` ready, qmd anchor TBD) · AppLoad model selector (local / ChatGPT /
Claude / Gemini) · Mac Mini M4 as bigger-model host · Qwen3.5-4B vs
Qwen3-VL-4B bake-off (docs/research-local-models.md).

---

## Vision (one line)

Physical reMarkable 2 ink notebook that talks back: write by hand → Monke reads the page → Monke inks a reply on the same page. Backed by Hermes (brain/memory) + Obsidian KB. Manages academics, projects, research, journaling, habits.

Tom Riddle's diary, but the ghost is your own KB.

---

## Architecture lock

```
RM2 ink surface  ──►  Smriti I/O layer  ──►  Monke persona  ──►  Hermes (brain/memory/KB)
   (pen/touch)         (input+output)         (voice/character)     (Qdrant + Obsidian)
        ▲                                                                  │
        └──────────────  ink reply rendered back on page  ◄───────────────┘
```

- Smriti = **frontend**, not a new brain. Reuses Hermes + Qdrant + Obsidian + Pi already running.
- Un-freezing Hermes is required → this overrides the Council freeze (conscious override, see Flags).

---

## RM2 estate consolidation (kill redundancy)

Five+ overlapping variants exist (restart-from-scratch pattern, §2). Rule: **one canonical repo, salvage-not-rewrite. No 6th variant.**

| Repo | Commits | Fate | Salvage |
|---|---|---|---|
| `Archive\RM_Projects\PROJECT.md` | — | anchor spec | full architecture base |
| lamp-v2 | 118 (Dec'25) | salvage | output/display stack |
| Elxnk | 52 (Dec'25) | salvage | `protocol.py` / `rm2_client.py` host draw API |
| penflow | ~60% (Apr'26) | salvage | 3-mode stroke input streaming |
| rm2elxnk | 4 (Apr'26) | reference | genie/lamp/rmkit integration snapshot |
| Elxnkv3 | 2 | **retire** | dead restart |

Canonical target: `Active\Smriti\` assembled from the three salvage sources. Redundant paths listed for manual deletion (never auto-delete).

---

## Dependency chain

```
EMR pen fix ──► writing returns ──► Smriti has input ──► ink loop closable
                                          ▲
Hermes E2E validated ─────────────────────┘ (brain online)
```

EMR pen is the **hard blocker**. No pen = no writing = the month-long stall. Fix first.

---

## Prior art (reference — study, don't fork)

- **`github.com/MaximeRivest/riddle`** — THE Tom Riddle diary (the Instagram showcase). Built with **Fable 5**. Write → ink fades → AI answers in flowing hand, stroke-by-stroke. **Platform: reMarkable Paper Pro (ferrari, aarch64, color), Rust.** We are RM2 (armv7, mono), Python (no Rust). → **salvage technique, not binary.**
  - **Handwriting synth (= our v0.1.1 core):** styled text → rasterize glyphs → **Zhang-Suen thinning** (skeletonize to 1px) → **stroke tracing** → replay as pen strokes. Font = **Dancing Script** (OFL). Reimplement in Python: Pillow + scikit-image/OpenCV skeletonize + path trace → lamp `pen`/`pen_up`.
  - **Loop:** raw evdev (4096 pressure) → idle 2.8s → commit page → PNG → oracle LLM → streams reply sentence-by-sentence → strokes.
  - **Providers:** OpenAI-compatible `/chat/completions` + vision (`RIDDLE_OPENAI_KEY/BASE/MODEL`; OpenAI/OpenRouter/Groq/local). Subscription/OAuth path = their **`pi --mode rpc`** resident (Node, holds sub auth; used when no API key). Persona prompt in `riddle/src/oracle.rs`.
  - **Deploy:** xovi/AppLoad (`/home/root/xovi/exthome/appload/riddle/`, `external.manifest.json` + `appload-launch.sh`), scp release zip. We diverge → vellum git-update (see v0.1.1).
- **`MaximeRivest/remarkable-experiments`** — pen-event injection, `.rm v6` read/write, PDF underlays, partial-refresh terminal, xovi autostart. Mine for RM I/O.
- **`MaximeRivest/remagic`** — one-command dev-mode → AppLoad installer. Reference for smooth install.

## Versioned layer plan (build the app layer-by-layer)

`[DECIDE]` items in BUILD-SPEC get answered per version as we reach them. First versions:

| Version | Scope | AI? |
|---|---|---|
| **v0.1.1** | **lamp writer: multi-font/styling engine + vellum git-update pipeline** (detail below) | none |
| v0.1.2 | pen input capture + stroke→PNG page-commit on idle | none |
| v0.1.3 | AI provider layer (OpenAI-compatible: OpenRouter + local vision) | yes |
| v0.1.4 | closed diary loop: write → commit → vision → styled ink reply on page | yes |
| **v0.1.5** | session UX (tap eye = scan+greet+on, hold = off) + workarea parser + tailnet endpoints — DONE 2026-07-07 | yes |
| v0.1.6 | vellum overlay-UI app: eye marker as a drawn UI (floating-toolbar style), not ink | no |
| v0.2.x | Obsidian MD store + audit; maths/circuit LaTeX (CircuiTikZ + Lcapy) | yes |
| v0.3.x | Elxanima animation; Hermes bridge; subscription/OAuth providers (pi-style) | yes |

### v0.1.6 — overlay eye UI (TODO, researched not built)

Goal: eye marker drawn as a **screen overlay** (like xovi/AppLoad floating
toolbars), not real notebook ink — no eraser wiping real ink in the corner,
no echo-drain after marker redraws.

Findings so far (from STATE.md sessions):
- RM2 has no epdc/`/dev/fb0` path; xochitl draws via software swtcon. Overlay
  needs one of: (a) **xovi + appload** framework on device (what riddle uses:
  `external.manifest.json` + launch script), (b) rm2fb server hooking
  xochitl, or (c) direct write into xochitl's tile buffer (region found at
  `0x60268000`, content TILED — reassembly unsolved).
- Path of least work = (a): install xovi via reManager/vellum, ship a tiny
  appload app that draws the eye + toolbar and forwards taps to the daemon
  (socket or file flag). lamp stays for ink replies.
- Blocker: cross-compile armv7 + xovi install on fw 3.27 unverified.

### TODO (running list, 2026-07-07)

- [x] AI endpoint selection config (env → [ai] → [ai_fallback]) over tailnet
- [x] Workarea parser: screenshot → ink floor + free bands → placement
- [x] Session UX: tap = scan+greet+watch, hold = off, boot idle
- [x] Gemini-from-Pi verified fast (0.46s — old IPv6 hang gone)
- [x] README: sessions, endpoints, tailnet, troubleshooting
- [x] v0.1.6 toolbox UI shipped as AppLoad app (drawn UI, no ink): tap eye
      = start/stop, state via /home/root/.smriti files + smriti-eye-watch
      bridge. Floating in-notebook eye (smriti-eye.qmd qmldiff patch) =
      written, NOT deployed/tested — needs qmldiff comment-syntax check +
      visual verify.
- [x] Floating eye finger-tap VERIFIED working on device (Navy, 2026-07-08)
- [x] SVG shapes densified — rects/polys no longer lose edges to lamp
      decimation (verified junction diagram closed on device)
- [x] AI gets real page dims (1404x1872) + live free-space per reply
- [ ] Navy: open AppLoad → Smriti on tablet, tap eye, confirm session
      starts (journal on Pi) + UI state flips
- [ ] Ink marker now off by default (marker_ink) — remove marker code
      entirely once toolbox app proves out
- [ ] Device-side touch watcher (replace host event2 ssh stream) — less
      tailnet chatter; awk/hexdump parser sketch in session notes
- [x] goMarkableStream corruption auto-heal (screen.grab: detect dense
      noise → restart gms → regrab) — shipped v0.1.6
- [x] Backfeed: screenshot baseline diff gate (page_diff) — shipped v0.1.6
- [x] SVG sketch path + block source logging — shipped v0.1.6
- [ ] Text detection / free space v2: column-aware bands (left/right
      margins), text-line segmentation for tighter reply placement,
      diff-region (not stroke echo) as the red overlay for the AI
- [ ] `sudo loginctl enable-linger monke` on Pi (needs Navy password)
- [ ] Fade mode visual verify with Navy (code commented in monke._reply)
- [ ] rmscene fw 3.27 .rm parsing (parked)
- [ ] On-device tap→greet E2E verify with Navy (needs fresh page + finger)

### v0.1.1 — spec (first shippable)

**Goal:** lamp-equivalent text writer that renders **multiple fonts + styling** on RM2 e-ink, packaged so updates land via **`vellum upgrade`** — no manual scp/install scripts.

- **Engine (Python, host):** text string → styled glyph raster (font, size, slant, weight) → skeletonize → stroke paths → lamp draw commands. Fonts: Dancing Script (cursive) + ≥1 print font. No AI, no input capture yet — feed text directly.
- **Display:** existing lamp/lamp2 (RM2 armv7) via rm2fb. Draw stroke-by-stroke.
- **Packaging + update (the real point):** wrap as a **vellum package**; push code to Navy's git; `vellum update && vellum upgrade` on RM2 pulls the new build. VELBUILD `postupgrade()` runs the swap.
- **Done =** change a font/style in repo → push → `vellum upgrade` on device → new writing style renders on RM2, zero physical install steps.
- **Update mechanism (RESOLVED 2026-07-06):** vellum = static **apk** wrapper (`/etc/apk/repositories` + signing keys). Path = **self-hosted apk repo**: build `.apk` via `vbuild` (Eeems/vbuild) from a VELBUILD (`depends="rm2 remarkable-os>=X"`, `source=` pinned to GitHub `_commit`) → publish signed `.apk` to a static host (GitHub Pages/Releases) → add repo URL to device `/etc/apk/repositories` once → release loop = bump `pkgver`+`_commit`, rebuild, publish, `vellum update && vellum upgrade` on device. No scp/scripts. Public alt = PR into `vellum-dev/vellum`. Build-time sub-checks: `vbuild` env (Docker/Alpine), apk signing-key gen.

## Plan — 10–11 days

### Phase 1 · Days 1–3 · Hardware + Hermes (object wins first)
- [ ] **EMR pen (CRITICAL, unblocks everything):** unzip `Active\EMR_Pen.zip`, verify STL/STEP watertight, slice A1 Mini, print. Silicone-mould grip workflow. Was due a month ago — close it.
- [ ] **Pathika:** finalize hardware for ordering; silicone-mould step; **give real git history** (444 files / 2 commits — audit debt #3), commit in logical chunks.
- [ ] **Hermes:** SCRUB SECRETS FIRST (audit debt #1 — OpenRouter key patterns in `hermes_import\canonical\*.json`, `distill_batch.py`). Then setup on Pi + validate E2E tool-calling (the never-reached step). This is the Smriti brain.

### Phase 2 · Days 4–6 · RM2 consolidate
- [ ] Assemble canonical repo per `PROJECT.md`: penflow-input + Elxnk-draw + lamp-v2-output → `Active\Smriti\`.
- [x] **Deployment stack researched** (see SPEC §4): vellum VELBUILD package → reManager install → AppLoader launch → rm2fb draw; xovi optional for xochitl UI hook. [ ] Verify all coexist on current firmware.
- [ ] Scrub RM2 setup notes for creds (§8).
- [ ] List redundant repo paths → Navy deletes manually.

### Phase 3 · Days 7–9 · Smriti spec (see SPEC.md)
- [ ] Ink loop: pen-up idle → bufshot → vision → Monke reply → ink render.
- [ ] Persona layer (Monke voice), memory via Hermes, KB read/write boundary.
- [ ] Handwriting render — the one genuinely new build.

### Phase 4 · Days 10–11 · Thin PoC + checkpoint
- [ ] One E2E spike: write a line on RM2 → vision reads → Monke inks a reply on the page.
- [ ] Validate loop ONLY. Checkpoint state to file. **Stop** at MSR.

---

## Open questions
- Silicone moulding — EMR pen grip, Pathika enclosure, or both? (assumed pen grip)
- xovi/vellum/rmManager exact packaging (Phase 2 research).
- KB source-of-truth boundary — resolve in SPEC.md.

---

## Daily routine scope (what Smriti/Monke surfaces daily)

Selected 2026-07-06. This is the tracker set — what the assistant nudges and holds state on.

**Builds:**
- **MSR academics** (MSR-IITD) — top priority; must not slip once term starts.
- **Hermes+KB spine** — daily *for 3 days only*, drops off once the loop closes.
- **Pathika firmware** — steady small commits (Services/Apps layers).

**Life:**
- **Journaling** — core Smriti use-case; RM2 write → Monke reply.
- **Habits** — ties to Pathika + health context (sleep/weight); environmental nudges, not willpower.
- **Hardware finish** (EMR/Pathika) — short punch-list, async, retires when printed/ordered.

Consciously excluded: docsort (finite close-out, not daily), research-reading (left out — note: MSR is a *research* degree, reconsider a light daily reading habit).

## Central spine — 3-day MVP (the real fix)

Thesis: object projects scatter because the KB+Hermes spine is half-built. Finish spine → scatter stops. **Trap-guard: "done" = one validated loop, NOT "complete Hermes."**

| Day | Target | Done = |
|---|---|---|
| 1 | Secrets + tool-call | Scrub keys (debt #1). Hermes runs ONE real tool call E2E, logged. |
| 2 | KB pipe | Qdrant on a subset (not 356GB). Hermes reads one vault note, writes one, verified. |
| 3 | Closed loop | Ask → KB context → act → write Daily note. Seed with PROJECT-AUDIT. Then STOP. |

Hardware runs parallel/async (EMR print + Pathika order = fire-and-forget). Anything off the "done" line is out of scope for these 3 days.

## Flags
- **Meta trap.** Smriti is ~65% agent/KB tooling — the category the audit says gates object work. Hardware (Phase 1) is the object win; front-loaded on purpose.
- **Council override.** L3M-Council's Jun 2026 verdict froze meta-systems until MSR. Building Smriti + un-freezing Hermes bypasses it, ~10 days before its own deadline. Conscious override, logged.
- **Disengage pattern.** Days 10–11 PoC is deliberately time-boxed. "Proof then disengage" is your mode — here it's the plan: build proof, checkpoint, walk into MSR clean.
