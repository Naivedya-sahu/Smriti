# Page-context awareness

## Answer

**Yes, page context awareness is implemented in layers.** Smriti sends visible-page context and fresh writing to the reader, tracks notebook/page history, maps occupancy/free space, and keeps reply units anchored when the view scrolls.

## Implemented and automated-tested

| Layer | Implementation | Evidence |
|---|---|---|
| Visible page + new ink | `host/monke.py:_page_context()` overlays fresh strokes in red on a real screenshot; old page content remains context | `tests/test_inking.py`, `tests/test_screen.py` |
| Placement context | `host/screen.py:stroke_mask`, `ink_floor`, `free_bands`; `monke._place()` fits rendered reply into safe open bands | screen and inking tests |
| Notebook/page hierarchy | `host/vectors.py` reads notebook/page/viewport/layer metadata; `NotebookSession.log_turn()` tags page; `notebook_context()` and `page_log()` supply history | `tests/test_vectors.py` |
| Structured page map | `host/protocol.py:page_map()` builds notebook/page, old-ink, focus, Monke/graph and free-region metadata for reader/writer | `tests/test_protocol.py` |
| Page/view guard | Page changes stop inking; measured viewport moves rebase pending reply units rather than redraw at old screen positions | rebase wiring and simulation tests |

## Implemented, but still needs live rM2 proof

- Page Protocol v1 target metadata and Set-of-Mark labels.
- Model directives and region-aware placement.
- Merged-turn interaction with live page context.

These paths are host/simulation-tested; final physical rM2 live validation remains a release acceptance test.

## Page-context features added in 0.6 development

| Feature | Prompt path | Limits |
|---|---|---|
| Semantic regions | `host/regions.py` builds stable per-page `R` ids for Navy and Monke ink. Reader and writer receive `<regions>` (at most 700 characters); deictic references resolve to a region and directives accept `@target R3`. | Navy kinds are geometry heuristics, not yet live-tested on rM2. Very sparse pages cluster quadratically (2000 isolated strokes: about 430 ms). |
| Selection grounding | An active lasso becomes focus; reader and writer receive `<selection>` (at most 500 characters) and the existing focus crop. It wins over region/deictic resolution and Smriti never deselects it. | Firmware 3.27.1.0 exposes `sceneController.boundingRect` and `itemsBoundingRect`, but their frame under a real selection is not measured. Unsupported probes are negatively cached for the session. |
| Scroll-aware continuation | Reply units are anchored in page coordinates. Matched ink offsets detect a measured view move; pending parts rebase, partial parts retry only missing strokes, and page-bottom continuation waits for 2 s of still view. | Before a scrolled prompt offset registers (about 10 s), view-field fallback can give wrong context. It remains safe: no ink is placed over Navy's visible writing. `page_full` stops rather than guessing. |

`[monke] page_height` controls the page end; 0 means no end. These paths are host/simulation-tested and still need physical rM2 validation.
