# Smriti remote architecture — Pi5 brain, rM2 field client

Status: proposal only. No Pi, Docker, Hermes authentication, Funnel, or remote tunnel was changed.

## Target use case

Navy writes live notes in an ADSP class with the rM2 on an iPhone hotspot. Smriti must refine those notes,
not turn the tablet into a general agent computer.

| Capability | Concrete output | Host responsibility |
|---|---|---|
| 1. Resource parsing | handwriting/page → typed render plan | read screenshot, model call, schema validation |
| 2. Quick note assistance | short correction, equation, graph, diagram, circuit | deterministic block executor; first block streams early |
| 3. Context + tools | visible page first; session/page map second; tools only when plan requires one | bounded context builder + render-plan validator |
| 4. Organization/progress | session log, topic tags, unanswered questions, spaced review queue | durable database on Pi; tablet sees a small status only |

## Naming

Rename the host service and process identity to **`smriti-brain`**. Keep `host/monke.py` temporarily as a
compatibility launcher during the migration; do not rename the module in one sweep. `Monke` remains the
notebook ink persona/layer, not the whole system.

Proposed names:

```text
smriti-brain        daemon: capture → plan → render → inject
smriti-edge         rM2 connection/tunnel supervision
smriti-plan         strict schema + deterministic canonicalizer
smriti-tools        graph / LaTeX / circuit / diagram executors
smriti-journal      progress and session organization on Pi
Monke               dedicated rM2 ink layer/persona only
```

## Connectivity — no rM2 Tailscale, no home router port forward

```text
rM2 -- iPhone hotspot internet -- outbound TLS tunnel --> Pi5
                                                     └-> smriti-brain
```

The iPhone is **network only**. iOS cannot route hotspot clients through its Tailscale VPN, and iOS cannot
be a Tailscale subnet router. Do not make the iPhone the daemon host.

### Recommended transport

1. Pi5 uses Tailscale only on the Pi. Test `tailscale funnel` as public TLS entry; this needs no router port
   forward.
2. When rM2 is armed, `smriti-edge` starts one authenticated outbound tunnel to the Pi. It carries:
   - Pi → rM2 control/capture SSH path.
   - rM2 eye → Pi `smriti-brain` status/control path.
3. When disarmed, stop tunnel and all capture streams. Tablet remains low-power.
4. If Funnel cannot expose the needed TCP service, use a small cloud relay: both Pi and rM2 open outbound
   connections. Never expose rM2 SSH directly to the public internet.
5. Long-term: replace SSH tunnel with one authenticated TLS/WebSocket edge protocol. That removes reverse
   SSH complexity and lets the Pi send render strokes directly to the edge.

P0 checks before implementation: Pi Funnel TCP viability; rM2 `openssl`/SSH tunnel support; hotspot RTT and
battery drain for 30 minutes; reconnect behavior after hotspot handoff.

## Hermes authentication and Docker

**Do not copy the laptop's OAuth/token files.** Authenticate a dedicated Hermes/ChatGPT identity on the Pi
through Hermes' supported setup/login flow. Treat Pi auth as an independent revocable credential.

### Safe deployment shape

```text
Pi host
├── Hermes auth/profile (host-owned, private volume; Hermes performs login)
├── Hermes proxy or provider broker (loopback/private Docker network only)
└── Docker Compose
    ├── smriti-brain  (Python, render plan, graph/TeX/circuit executors)
    ├── smriti-edge   (tunnel supervisor; least privilege)
    └── smriti-journal (SQLite/Postgres later; start SQLite)
```

- Hermes owns its auth home and refreshes credentials. The Smriti container receives an internal provider
  endpoint, not a copied browser token.
- If direct ChatGPT transport remains necessary, mount a **dedicated Pi Smriti auth volume** read-only into
  `smriti-brain`; never mount the laptop auth store or a broad home directory.
- Use Docker secrets/files for non-OAuth credentials; never place secrets in compose YAML, Git, or notebook
  settings.
- The first Pi image should be CPU-only: Python, numpy, Pillow, fonts, TeX/circuitikz, and no model weights.
  Build graph/stroke rendering in the image; model inference stays cloud.
- Pin images and run container as non-root. Persist only journal/session state and required render caches.

### Migration gates

| Gate | Evidence required | Stop condition |
|---|---|---|
| Pi Hermes auth | `hermes doctor` + one non-notebook model request | auth is inaccessible from container without copying secrets |
| Container render | offline AC RLC preview and DSP preview match host fixtures | TeX/font mismatch or graph slowdown |
| Tunnel | rM2 hotspot → Pi reconnect, control + screenshot round trip | tunnel needs public rM2 SSH exposure |
| Live safety | Monke layer, read-only pen shadow tags, no prompt cuts | source tags misclassify handwriting |
| Organization | one class session produces a topic/task/review record | needs a model loop to organize |

## Deterministic interaction contract

```text
visible notes + short user ask
  → one structured plan call
  → validate/canonicalize (no model repair loop)
  → deterministic blocks: text | equation | graph | circuit | diagram
  → Monke-layer ink batches + verification
  → journal: topic, blocks, confidence, follow-up/review item
```

No autonomous agent loop on the tablet. Tool calls are only plan blocks validated against an allow-list.
Unknown or malformed blocks fall back to a short text request or a deterministic diagram, never source code
or an unbounded retry.

## Current code facts / next development session

- Strict ChatGPT JSON schema plan measured: first delta ~1.4 s; canonical AC blocks close from ~1.9 s.
- Direct graph executors exist (`host/plots.py`), but live test showed `(abs(x)<=1)` is rejected by the safe
  expression grammar and falls back to slower pgfplots. Add safe comparison normalization before live retry.
- Study packs were removed from the UI; settings prompt is empty.
- `host/pendump.py` + `host/lampmark.py` are shadow-only. Promote source tags only after real pen/lamp
  evidence meets the documented threshold.
- Rename is a planned compatibility migration, not a source-file rename tonight.
