# Smriti Page Protocol (SPP) v1

How the daemon tells the model WHERE things are on the page, WHAT they are, and WHO made
them — and how the model says WHERE its answer goes and WHICH region it acts on.

Coordinates: visible-screen pixels of the reMarkable 2, origin top-left, x right, y down,
1404 x 1872. Boxes are half-open `x0,y0,x1,y1`. The OVERVIEW image is the same screen
scaled; the FOCUS image is the `focus` region scaled up.

## 1. Daemon → model: `<page>` block

Sent to the reader (stage 1) and the writer (stage 2), after the task text.

```xml
<page notebook="Test" page="57/60" screen="1404x1872" scrolled="yes">
  <region id="N1" owner="navy" layer="Layer 1" box="96,210,1180,520" strokes="64" age="old"/>
  <region id="M1" owner="monke" type="text" box="96,540,1010,720" turn="3">Ohm law. V = IR.</region>
  <region id="G1" owner="monke" type="graph" box="100,760,640,1130" turn="4">y = sin(x), x 0..10</region>
  <region id="N2" owner="navy" layer="Layer 1" box="700,780,1300,1120" strokes="41" age="old"/>
  <region id="F" owner="navy" layer="Layer 1" box="120,1180,1150,1290" strokes="22" age="new" focus="yes"/>
  <free box="0,1300,1404,1872"/>
</page>
```

A `<regions>` block follows: `<r id="R3" kind="circuit" owner="navy" box="x0,y0,x1,y1" .../>` in PAGE coords, ids stable across turns; any directive or `target` may use an R id.

| Attribute | Meaning |
|---|---|
| `id` | `N<k>` Navy ink group, `M<k>` Monke reply, `G<k>` Monke graph (same id Navy may write as "G1"), `F` the newest writing (focus) |
| `owner` | `navy` (his layers) or `monke` (the Monke layer). Ownership comes from the layer, never guessed |
| `type` | Known only for Monke's own regions (from the session log). Navy's regions carry no type until a reading classifies them |
| `age` | `new` = written since the last reply; `old` = earlier |
| body text | Monke regions: what was inked (first ~60 chars / equation). Navy regions: empty (the image is the truth) |
| `<free>` | Empty bands where a reply can go |

Regions off-screen are omitted. Max 12 regions; smallest/oldest Navy groups merge first.

## 2. Model → daemon, stage 1 (reader JSON additions)

```json
{"kind": "...", "text": "...", "latex": "...", "circuit": "...",
 "intent": "question|note|solve|plot|draw|write|refine",
 "target": "F|N2|G1|M1|R3",
 "target_kind": "text|math|circuit|graph|sketch|mixed",
 "target_desc": "what the target region contains, exactly enough to redraw it",
 "summary": "..."}
```

- `target` = the region the writing is ABOUT. Default `F` (the writing itself). "this graph",
  "above", "the circuit", an arrow pointing at ink → the referenced region id.
- `intent: refine` = Navy asks to clean up / redraw / beautify a region ("neaten this",
  "make this graph clean", "#P redraw").
- `target_desc` is mandatory when `target != F`: axes, ticks, curve shape and key points for a
  graph; components, values and connections for a circuit; blocks and arrows for a diagram.

## 3. Model → daemon, stage 2 (reply directives)

Optional directive lines at the START of the reply, one per line, never inked:

```
@target N2
@place below N2 | beside N2 | below F | free
@refine N2 as graph|circuit|diagram|text
```

- Without directives: today's behaviour (reply below the newest writing).
- `@refine X as T` = the following block is a clean redraw of region X in type T. Navy's
  original ink is NEVER erased or moved (content safety: his layers are untouchable); the
  redraw goes beside it, else below it.
- Directives reference ids from the `<page>` or `<regions>` block. Unknown id → ignored, logged.
- Plan mode (smriti_plan JSON): no directive lines; the plan's `place` field carries the
  `@place` argument (`"below N2"`), null for the default.

## 4. Refine rules (beautify jagged graphs, circuits, diagrams)

- Keep Navy's meaning, fix only the geometry: straight wires, right angles, even spacing,
  aligned blocks, smooth curves, labelled axes. Never add values he did not write.
- Graph: same axes ranges, ticks at his marks, curve through his key points; pgfplots.
- Circuit: same topology and labels; circuitikz.
- Diagram: same blocks, same arrows, labels as written; SVG.
- Handwriting: re-ink legibly only when asked ("rewrite neat").
