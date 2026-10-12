# Feature set — 0.5.0

## Study loop

1. Start a session from the rM2 eye/AppLoad UI.
2. Capture new pen strokes and visible-page screenshot context.
3. Build a typed plan; model output cannot directly execute shell, filesystem, network or tablet actions.
4. Render prose, LaTeX, plot, circuit or diagram deterministically.
5. Write only after page/view safety checks; use the Smriti/Monke layer when the tablet integration confirms it.

## Current capabilities

| Area | Capability |
|---|---|
| Context | visible screenshot with fresh strokes marked red; notebook/page hierarchy; same-page session log; other-page summaries; free-band placement |
| Safety | armed gate; page/view-change refusal before draw; stale-prompt refusal; safe plotting AST; drawing confirmation where required |
| Maths | TeX normalization/typesetting, equation render plans, plots, pole-zero and frequency-response primitives |
| Diagrams | deterministic render plan for circuit/diagram roles; CircuitikZ/PGFPlots pipeline |
| Input/output | pen capture, handwriting render, injected real notebook ink, shadow-only pen tagging |
| Tablet UI | AppLoad master switch; eye states for idle/watching/thinking/drawing; reverse SSH tunnel to tablet loopback |
| Windows app | installer, tray supervisor, Start Menu/Search, console diagnostics, per-user paths and isolated TinyTeX |
| Connectivity | Tailnet, USB, Wi-Fi/hotspot endpoint resolver; bounded private-subnet scan with authenticated rM2 identity check |
| Providers | direct per-user ChatGPT login, OpenAI key, Anthropic key, optional Claude CLI, explicit local endpoint |

## Deliberate exclusions

- No automatic local-model fallback or public LM Studio server.
- No copied Hermes/Claude/browser authentication.
- No agent loop with unrestricted tools.
- No user-note layer writes when safe-layer verification fails.
- No unbounded network scanning.

## Evidence boundary

Host tests cover current logic. The newest structured page protocol, Set-of-Mark labels and region-aware placement are host/simulation-tested but require final rM2 live validation. See [PAGE-CONTEXT.md](PAGE-CONTEXT.md) and [REQUIREMENTS-AND-TESTS.md](REQUIREMENTS-AND-TESTS.md).