# Research: deterministic render middleware + Monke-layer input ownership

2026-10-10 · research only (no code, no tablet, no model calls) · builds on slice 1 `host/render_plan.py`
(raw reply → ordered Blocks `text|latex|graph|circuit|diagram`, role→colour), which another lane is building now.
Canonical workload: an AC series RLC answer with prose, LaTeX, one circuit, two graphs (X_L and X_C on one,
the resonance curve on the other) and coloured elements.

## 0. Verdicts

| Question | Verdict |
|---|---|
| Middleware shape | **One call returns a typed render plan, then a deterministic validator/canonicalizer, deterministic executors and a verify step.** No agent loop. The model call is the only nondeterministic step; the only extra call is a repair (at most 1 per block). |
| Plan source, ChatGPT direct | **Structured Outputs `text.format` (`json_schema`, `strict: true`) on the same `/codex/responses` request.** Codex CLI sends exactly this field for `--output-schema` [5][6]. Probe before building: the backend accepts it for `gpt-5.6-*` (unverified). |
| Plan source, Claude CLI | **Keep the text grammar and run it through `render_plan.py`**, so streaming survives. `claude -p --json-schema` checks the result and re-prompts when it fails; the result comes only at the end, with nothing streamed [12][13]. Use it only for turns that don't stream. |
| Constrained decoding (Outlines/XGrammar/llguidance) | Not reachable: both transports are hosted with no logit access. OpenAI already runs llguidance server-side [2][14], so Smriti gets it through Structured Outputs. |
| Layer ownership for the input echo | **The `.rm` layer is the right truth for ink that is already saved, but it is wrong as the input filter** (≈10 s flush lag; Navy's ink written while Monke's layer is current also lands on `Monke`). Replace the screen-space mask with **provenance at the event source**: a lamp event fingerprint plus an injection window. Keep the mask only as a fallback, kept in page coordinates. |
| Placement | Placing one unit at a time breaks with 3+ figures. Add a **plan-level layout pre-pass** (an `outline` header arrives before the blocks) and record each block's region in the session. |

## 1. Today (code facts)

| Stage | Where | Output parsed | Weak point for the RLC workload |
|---|---|---|---|
| Transport, ChatGPT | `brain._direct` L239–303: Responses SSE, `reasoning.effort=none`, parses only `response.output_text.delta` | free text | no schema; function/custom-tool deltas ignored |
| Transport, Claude | `oracle.claude_chat` L58–131: `claude -p --tools "" --max-turns 1`, stream-json | free text | no schema; one turn only |
| Reader | `harness.read` L185 + `parse_reading` L154 (first JSON object, lenient) | JSON reading | prompt-only contract; `kind`/`intent` normalised by hand |
| Writer grammar | `skill/smriti/SKILL.md` §Stage 2: prose lines, `$$…$$`, `<svg>` | text | graphs are **model-authored pgfplots** (compile errors, colours guessed from `\addplot[blue]`) |
| Split + render | `monke._segments` L2879, `_layout` L2831, `_drawing` L2770, `harness.stream_units` L307 | regex segments | inline `$` heuristics; a failed block is skipped silently; prompt caps the reply at "up to 3 small equations or drawings" (L2501) |
| Repair | `harness.repair_latex` L458 | 1 model call per failing `$$` | the only validation gate |
| Deterministic plots | `monke._plot` L2147 → `graph.py` | `y = …` readings only | not used for graphs *inside* a reply; axis labels are hardcoded `$x$/$y$` (`graph.py` L136) |

## 2. Mechanism comparison

| Mechanism | Guarantee | ChatGPT direct | Claude CLI | Latency cost | Determinism | Use? |
|---|---|---|---|---|---|---|
| Prompted grammar + lenient parse (today; `render_plan.py`) | none; parser absorbs the mess | yes | yes | 0 | parse is deterministic, structure is not | **Yes**: the baseline, and the Claude path |
| OpenAI Structured Outputs `text.format` strict | output matches the schema (constrained decoding) [1][3]; keys come out in schema order [1] | **likely**: Codex sends `text.format{type:json_schema,strict,schema,name}` [5] | — | the first request with a new schema pays a preprocessing penalty, then it is cached [3] | structure guaranteed. Content can still be wrong ("can still contain mistakes") [1]. Refusals are separate [1] | **Yes**: primary on ChatGPT |
| OpenAI strict function calling + forced `tool_choice` | args match the schema; `tool_choice` can force one function [2] | unverified: Codex sends `tool_choice` as a string [5]; `brain._direct` ignores `function_call_arguments.delta` [4] | — | same as above | same | No: no gain over `text.format`, more unknowns |
| OpenAI custom tool + Lark/regex CFG | free text constrained by a grammar via LLGuidance [2] | plausible: Codex's `apply_patch` is a Lark freeform tool [7] | — | grammar compile | a user reports output that broke the CFG with regex terminals [24] | Later, maybe: could constrain today's *text* grammar |
| Anthropic strict tools / `output_config.format` | grammar-constrained sampling [8][9]; forced `tool_choice` [10] | — | **no**: an API-key feature; Smriti's Claude path is the subscription CLI | first schema compiles, cached 24 h [8] | strong | No (not reachable) |
| `claude -p --json-schema` | the SDK checks the output and **re-prompts on mismatch**; result in `structured_output` [12][13] | — | **yes** | extra turns on mismatch; nothing until the end | eventual, not constrained | Only for turns that don't stream (needs `--max-turns` > 1, probe) |
| Outlines / XGrammar / llguidance (local) | constrained decoding needs logits [14][15][16] | no | no | ~0 when local | strong | No (no local model in Smriti) |
| BAML SAP | schema-guided error-correcting parse [17] | client side | client side | ms | deterministic parse | **Pattern only**: `render_plan.py` is the stdlib SAP for the text path |
| Instructor validate→reask / Pydantic AI output modes | retry with the validation error; tool/native/prompted output [19][21] | client side | client side | +1 call per retry | eventual | **Pattern only**: generalise `repair_latex` to a per-block repair |
| json_repair | fixes broken or truncated JSON, can be guided by a schema [20] | client side | client side | ms | deterministic | No: strict mode makes it moot; handles the truncated tail at most |
| Partial validation (Pydantic `allow_partial`, Instructor `Partial`) | validates incomplete JSON while it streams [18][22] | client side | client side | ms | deterministic | **Pattern**: a stdlib extractor that emits each block when it closes (§3.4) |

Format restrictions can degrade reasoning [23]. So keep the plan flat, keep long working out of it, and compute numbers in code (§3.2 `calc`).

## 3. Recommended architecture

```
reading ─► writer call ──► raw (JSON plan | text) ──► Plan[Block]   (render_plan.normalize / plan_stream)
                                                        │ per block, as soon as it closes
                                                        ▼
                    gates: schema → canonicalize → compile/sample → size → (1 repair | fallback | drop→log)
                                                        ▼
                    layout: outline pre-pass reserves slots ─► place.resolve per block
                                                        ▼
                    executors (pure → strokes): text=render, latex/circuit=tex.py, graph=graph.py, diagram=svg.py
                                                        ▼
                    ink: one Monke-layer session, colour batches ─► verify: pixels now, .rm colour/layer async
```

### 3.1 Plan contract v1 (strict-safe: root object, every key required, `additionalProperties:false`, nullable as `type:[…,"null"]`, `anyOf` only below the root, ≤10 levels [1])

```json
{"v": 1,
 "outline": {"text_lines": 3, "figures": ["circuit", "graph", "graph"]},
 "place": null,
 "blocks": [
  {"type": "text", "role": "body", "text": "Series RLC: current peaks where XL = XC."},
  {"type": "latex", "role": "result", "tex": "f_0=\\frac{1}{2\\pi\\sqrt{LC}}\\approx 159\\,\\mathrm{Hz}"},
  {"type": "circuit", "role": "figure", "tex": "\\begin{circuitikz}\\draw (0,0) to[sV,l=$V$] (0,3) to[R,l=$R$] (3,3) to[L,l=$L$] (6,3) to[C,l=$C$] (6,0) -- (0,0);\\end{circuitikz}"},
  {"type": "graph", "role": "figure", "id": "G1", "x": {"from": 20, "to": 400, "label": "f (Hz)"}, "y_label": "X (ohm)",
   "curves": [{"expr": "2*pi*x*0.1", "label": "XL", "role": "a"}, {"expr": "1/(2*pi*x*0.00001)", "label": "XC", "role": "b"}]},
  {"type": "graph", "role": "figure", "id": "G2", "x": {"from": 20, "to": 400, "label": "f (Hz)"}, "y_label": "I (A)",
   "curves": [{"expr": "10/sqrt(100+(2*pi*x*0.1-1/(2*pi*x*0.00001))^2)", "label": "I", "role": "highlight"}]}]}
```

- The model picks **roles**, never pen enums. `render_plan.ROLE_COLOR` (slice 1) maps them in one place: `a`→black solid, `b`→red dashed, `highlight`→blue, … These are `graph.STYLES` pairs, because colours show as greys on the RM2 panel and the dash carries the difference (`graph.py` L10–13).
- `outline` comes first. OpenAI emits keys in schema order [1], so the host knows that 3 figures are coming before the first ink.
- Graph `expr` uses the `graph.safe_eval` whitelist, and the canonicalizer renames the variable to `x`. Graphs never reach the model as pgfplots.
- Field names should follow whatever `render_plan.Block` ends up with. The contract is an ordered list, a closed `type` enum, and colour decided in one place.

### 3.2 Gates (all deterministic, host side)

| Gate | Check | Cost | On fail |
|---|---|---|---|
| G0 schema | type enum, required keys, `role` enum; the text path goes through the `render_plan` lenient parse | µs | text path: SAP-style coercion; JSON path: can't fail except on refusal or truncation → keep the blocks already closed |
| G1 canonicalize | `to_ascii`/`clean_reply` on text; strip `$`/`\` from prose; cap words; normalise TeX whitespace; round numbers to 4 significant figures; `(type, canonical src)` = cache key | µs | — |
| G2 compile/sample | latex/circuit: `tex.tex_to_image` (cached); graph: `safe_eval` over the domain, finite ≥ 80 %, y-range sane | 1–3 s per TeX block (pdflatex; missing on the fresh machine, plan-roadmap §3.7) | latex: 1 repair (existing `repair_latex`) → skip + log; graph: fall back to a latex block of the equation; circuit: skip + log (**never ink source**) |
| G3 size | bbox from the compiled strokes or a fixed graph size; fits a reserved slot | ms | shrink 1→.85→.7 (`place.resolve` ladder) → move to the log |
| G4 calc (later) | `{{f0}}` placeholders in text/latex filled from `vars` evaluated by `safe_eval` | µs | leave the literal |
| G5 verify | `_verify_ink` pixels (per **colour** batch, not per pen); async: the `.rm` `Monke` layer holds N new lines in the block colours (`vectors.parse` must keep `Line.color`) | ~2 s, already paid | redraw once (today) |

### 3.3 Executors (pure: block → strokes, no model)

| Block | Executor | Colour source | Note |
|---|---|---|---|
| text | `write.render` / `_layout` prose path | role → MONKE_COLOR | ASCII stroke font |
| latex | `tex.tex_to_strokes` via `_drawing` cache | `color_masks` per pen | thin pen (`Drawn`) |
| circuit | same as latex (circuitikz) | role | later: netlist → series-loop template (removes model TikZ) |
| graph | **`graph.Graph` with several curves, labels, ids** (extend `new()`/`tex()`) | `graph.STYLES[i]` from curve role | registers `ses.graphs[id]` so the page map and "add … to G2" work |
| diagram | `svg.svg_to_strokes` | role | unchanged |

### 3.4 Streaming: each block inks as it closes

- JSON path: `brain._direct` already feeds `output_text.delta` to `on_text`. A ~40-line stdlib scanner (`plan_stream.py`, the JSON twin of `harness.stream_units`) tracks string/escape/depth state and, at each close of a `blocks[i]` element, runs `json.loads(element)`, the gates, then `_Streamer`. The first ink comes after block 0 rather than after the whole plan, which is the same latency profile as today's line units.
- Text path (Claude, or fallback): `stream_units` → `render_plan.normalize(unit)` → the same gates. Both paths meet at `Block`.
- Parallelism: G2 compiles of later blocks run in a thread pool while earlier blocks ink. Inking stays one ordered lane (lamp is serial).

### 3.5 Caching

| Cache | Key | Where | Gain |
|---|---|---|---|
| Schema grammar (server) | byte-stable schema: one constant, never interpolate per-turn data | OpenAI [3] / Anthropic 24 h [8] | avoids the first-schema penalty |
| Prompt prefix | stable system prompt first, per-turn page map last | provider | input latency |
| Strokes | sha256(type + canonical src + renderer version) → strokes | today the in-memory `_DRAWINGS` (L2767); add a disk cache | skips 1–3 s pdflatex on repeats and redraws |

## 4. Transport-specific recommendation

| Transport | Mechanism | Change | Probe first (dev script, no daemon) |
|---|---|---|---|
| ChatGPT `direct` (default) | `body["text"] = {"format": {"type": "json_schema", "name": "smriti_plan", "strict": true, "schema": PLAN_SCHEMA}}` | `brain.chat(..., schema=None)` → `_direct` adds the field; on HTTP 400 that names `text`/schema, fall back to the text grammar for the session | 1) accepted for `gpt-5.6-luna`/`terra`; 2) first vs second call latency; 3) deltas still stream; 4) `effort:none` content quality on the RLC prompt |
| ChatGPT `cli`/`http` | text grammar + `render_plan` | none | — |
| Claude CLI | text grammar + `render_plan` (streams); `--json-schema` only for non-streaming roles | `oracle.claude_chat(..., schema=None)` → `--json-schema`, read `structured_output` | `--max-turns 1` vs 2 with the schema; added latency |

## 5. Phased plan (smallest first)

| # | Change | Files | Exit check |
|---|---|---|---|
| P0 | Probes: (a) `text.format` on `/codex/responses`; (b) a read-only `event1` dump during a lamp batch (pressure constant? `ABS_TILT_X/Y` absent?); (c) `claude -p --json-schema` turns | dev scripts only | numbers in this doc |
| P1 | **Echo provenance** (fixes the live prompt-cut): `StrokeBuilder` tags each stroke `src=lamp\|pen` from the fingerprint; `lamp()` records the injection window on the tablet clock; `drop_echo` filters on `src`; mask only when untagged | `capture.py` L81–107, `monke.lamp` L84, `drop_echo` L152, `_echo_only` L178 | sim: scroll after a reply, then Navy writes over the old reply spot → 0 strokes dropped; existing `test_echo_residue` green |
| P2 | Gates + executor dispatch on top of slice 1: `_layout` consumes `Block`s; per-block fallback table §3.2 | `render_plan.py` (+gates), `monke._layout/_segments`, `_Streamer._unit` | RLC text fixture → 5 blocks, 0 source inked when pdflatex is missing |
| P3 | Graph blocks → `graph.py` (several curves, axis labels, ids, role styles); verify per colour batch | `graph.py` L99–145, `monke._plot` (shared helper), `_verify_ink` L610 | RLC fixture: G1 has 2 curves in 2 styles, G2 has 1; `ses.graphs` holds G1 and G2 |
| P4 | Structured plan on ChatGPT direct + `plan_stream` extractor, `[brain] plan="json"\|"text"` | `brain._direct` L251, `harness.build_writer_prompt`, `skill/smriti/SKILL.md` §Stage 2, new `plan_stream.py` | first-ink time ≤ today's; 0 parse failures over the eval set |
| P5 | Layout pre-pass from `outline`; per-block region log replaces order-pairing | `place.py`, `_Streamer`, `_monke_regions` L2032 (its own ponytail asks for this) | 3 figures + 3 lines placed without overlap, or the overflow goes to the log as a whole block |
| P6 | Optional: `calc` vars, circuit netlist template, disk stroke cache | `render_plan`, new `circuit.py`, `_drawing` | — |

## 6. Monke-layer ownership of input (echo)

**Why the mask fails.** `lamp` writes to the same `/dev/input/event1` that `Capture` reads (`capture.py` L12–13), so Monke's ink comes back as "pen" strokes. `_echo_mark` paints them into a **screen-space** bitmap (L134). A scroll the daemon misses (undetected, `_reply_top` L294) leaves the bitmap stale, so Navy's new strokes over the old screen spot are dropped (≥ 60 % of their points on the mask, L166). `_echo_only` adds a count heuristic (≤ 8 strokes, L174) that already loses short prompts written during a reply.

Already known (`docs/research-rm-vectors.md`, `docs/context-plan.md`): `.rm` is written **9–10 s after the last stroke** while the page is open. The `Monke` layer is identifiable by label. The mapping is only trusted when `agree() ≥ 0.8` (else the warning `[vectors] page strokes don't match the screen` at `monke.py` L769). Page-turn freshness of `.content` is untested, and selection-aware inking is still open.

| Option | How | Added latency | Failure modes | Verdict |
|---|---|---|---|---|
| (a) Navy = non-Monke layers of `.rm` | `vectors.page` + `protocol._navy` (already the page-map source) | **+7–10 s per turn** (commit at idle ≈2.8 s, flush ≈10 s) | file not flushed → `agree<0.8`; **ink Navy writes while Monke's layer is current goes to `Monke`** (the layer is global during `on_layer`; run 12 zigzag, STATE.md L74); lasso moves; view registration on scrolled pages; 5-layer cap | **Truth for ink already saved** (page map, placement, graph-on-screen, refine source, async verify). **Not** the input filter |
| (b) injection-window tag | `lamp()` brackets each batch with tablet `date +%s.%N`; evdev events carry kernel time (`Capture._pump` unpacks it, `StrokeBuilder` drops it) | 0 | Navy writing during a batch (sim: 17 strokes inside a batch in flight, STATE.md L70) is mis-tagged if the window is used alone | **Yes, as the second gate** |
| (c) layer-isolated screenshot diff | `setLayerVisible(Monke,false)`, 1.2 s e-ink wait, grab, restore (`layers._probe` L268–274) | **+3–4 s**, and Monke's ink visibly flickers | pixels only; hides context while Navy writes; needs QML layer ops, which are **broken on the live deployment** (`sceneController = null`, professional-app-audit L28) | No (probe/debug only) |
| (d) **event fingerprint** | lamp strokes carry only the injected pressure (`pen down x y [p]`, `write.to_lamp` L60–72; upstream lamp alternates 4000/4001 and sends no tilt events at all [25]); a real pen varies pressure (tilt: verify in P0) | 0 | patched lamp event set unverified; a stroke mixing both sources (run 12) → treat as Navy's and keep it | **Yes, primary** |
| (e) page-coordinate mask | store `_ECHO` in page units through `vectors.view` offsets | +1 QML call | still positional: a note Navy writes **onto** a reply is dropped | Fallback only |

**Minimal change (P1).** About 30 lines, all host side:
- `StrokeBuilder` keeps per stroke the set of pressures, whether any `ABS_TILT_*` (codes 26/27) was seen, and the kernel time of `BTN_TOUCH`.
- It returns a `list` subclass with `.src`. `src="lamp"` when every pressure in the stroke lies within an injected value (p or p+1) AND (no tilt OR the touch time lies in a recorded lamp window).
- `drop_echo` drops `src=="lamp"` and uses the mask only when the fingerprint is unknown (P0 found no discriminator).
- A watermark pressure (e.g. 2417 instead of 2400) makes the test exact at zero cost.

**What remains.** An accidental human stroke at one constant pressure is near zero, but test it in P0. Navy holding the Monke layer as current is a manual act; the page map would mislabel it. Pen and lamp running together still corrupt xochitl's own ink (`_pen_quiet` L71 checks once per batch); ownership doesn't fix that, so split batches if run-12 zigzags come back.

## 7. Placement with several figures per reply

| Risk | Where | Fix |
|---|---|---|
| Units placed greedily at `self.y`; the 4th figure hits the bottom, waits 20 s for a scroll, then the rest goes to the log | `_Streamer._unit` L383–447, `_await_scroll` L465 | P5: reserve slots from `outline` before the first ink; overflow moves whole blocks, never half a figure |
| `_beside` pairs drawings only when k ≥ 0.7; circuit (`max_w` 850) + graph (11 cm) never pair → stacked, ~1500 px | L275–290, `_drawing` L2781 | pack with `place.free_rects` (E1…E4) as a 2-column shelf; graphs at a fixed scale |
| `@place` pins **all** later units to one column beside a region | `_Streamer` `column_x` L390–402 | the directive applies to its block only |
| `_band_busy` checks one unit at ink time; Navy's new ink moves only that unit | L250, L416 | keep it; the pre-pass recomputes the remaining slots after a move |
| Verify redraws missing strokes with the current pen colour → a missed red curve comes back black | `_Streamer.finish` L501–509 | verify per colour batch (P3) |
| Whole-reply path inks all drawing strokes in **one** lamp call at the layer colour → coloured TeX comes out black (only `_Streamer` batches by colour) | `_ink` L218–227 | the executor output carries colour; one ink routine batches by colour for both paths |
| Page map pairs Monke boxes with replies by count; a 5-figure reply → bare boxes, "the second graph" can't be resolved | `_monke_regions` L2053–2059 | per-block region log `{id, type, box, turn}` (P5) |
| Model pgfplots graphs aren't in `ses.graphs` → no G-ids, no overlays | `graphs_context` L2076 | graph blocks go through `graph.py` (P3) |

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| `/codex/responses` is an unofficial surface (originator header, `brain._direct` L255) and schema support may change or be missing per model | P4 breaks | auto-fallback to the text grammar on 400; the text path stays first-class |
| First-schema latency [3][8] | slow first turn | a constant schema; warm it at session start (greeting call) |
| JSON plan with `effort:none` gives a weaker answer [23] | wrong physics | flat schema, short prose, numbers via `calc`; A/B on the eval set before switching the default |
| Structure guaranteed, content not [1]; refusal or truncation | empty or partial plan | blocks already closed are inked; tail → log; eye `!` |
| pdflatex missing on the fresh machine | latex/circuit blocks fail | G2 fallback order; graphs no longer need model TikZ but still compile via `graph.tex()` |
| Event fingerprint assumption is wrong (patched lamp differs from upstream) | P1 regresses | P0 dump first; keep the mask path behind a flag |
| Colours are greys on the RM2 panel | "multiple colours" invisible on the device | roles → colour + dash; colour is real in the saved file/export |

## Sources

[1] https://developers.openai.com/api/docs/guides/structured-outputs — OpenAI — Structured model outputs
[2] https://developers.openai.com/api/docs/guides/function-calling — OpenAI — Function calling (strict, tool_choice, custom tools/CFG)
[3] https://openai.com/index/introducing-structured-outputs-in-the-api — OpenAI — Introducing Structured Outputs (2024)
[4] https://developers.openai.com/api/reference/resources/responses/streaming-events — OpenAI — Responses streaming events
[5] https://raw.githubusercontent.com/openai/codex/35aaa5d9/codex-rs/codex-api/src/common.rs — openai/codex — codex-api common.rs (TextFormat json_schema strict)
[6] https://learn.chatgpt.com/docs/non-interactive-mode — Codex — non-interactive mode (--output-schema)
[7] https://raw.githubusercontent.com/openai/codex/f1affbac/codex-rs/core/src/tools/handlers/apply_patch_spec.rs — openai/codex — apply_patch freeform (Lark) tool
[8] https://platform.claude.com/docs/en/build-with-claude/structured-outputs — Anthropic — Structured outputs
[9] https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use — Anthropic — Strict tool use
[10] https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools — Anthropic — Define tools / forcing tool_choice
[12] https://code.claude.com/docs/en/headless — Claude Code — headless (-p, --json-schema, structured_output)
[13] https://code.claude.com/docs/en/agent-sdk/structured-outputs — Claude Agent SDK — structured outputs (validate + re-prompt)
[14] https://raw.githubusercontent.com/guidance-ai/llguidance/main/README.md — llguidance README
[15] https://raw.githubusercontent.com/mlc-ai/xgrammar/main/README.md — XGrammar README
[16] https://raw.githubusercontent.com/dottxt-ai/outlines/main/README.md — Outlines README
[17] https://boundaryml.com/blog/schema-aligned-parsing — BAML — Schema-Aligned Parsing
[18] https://python.useinstructor.com/concepts/partial — Instructor — partial streaming
[19] https://python.useinstructor.com/concepts/retrying — Instructor — retries
[20] https://raw.githubusercontent.com/mangiucugna/json_repair/main/README.md — json_repair README
[21] https://ai.pydantic.dev/output — Pydantic AI — Output modes
[22] https://docs.pydantic.dev/latest/concepts/experimental — Pydantic — partial validation (experimental)
[23] https://arxiv.org/abs/2408.02442 — Let Me Speak Freely? (format restrictions vs reasoning)
[24] https://community.openai.com/t/gpt-5-custom-lark-tool-outputs-are-not-guaranteed-to-conform-to-the-cfg/1337673 — OpenAI community — Lark CFG conformance report
[25] https://raw.githubusercontent.com/rmkit-dev/rmkit/master/src/lamp/main.cpy — rmkit lamp source (injected pen events)
