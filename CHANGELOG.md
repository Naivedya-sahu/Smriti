# Changelog

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
