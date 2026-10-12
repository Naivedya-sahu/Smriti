# Page-context awareness

## Answer

**Yes, page context awareness is implemented in layers.** Smriti sends visible-page context and fresh writing to the reader, tracks notebook/page history, maps occupancy/free space, and refuses to draw if the page/view changed. It does **not** yet provide fully integrated semantic region objects such as “Navy’s circuit at y 300–600.”

## Implemented and automated-tested

| Layer | Implementation | Evidence |
|---|---|---|
| Visible page + new ink | `host/monke.py:_page_context()` overlays fresh strokes in red on a real screenshot; old page content remains context | `tests/test_inking.py`, `tests/test_screen.py` |
| Placement context | `host/screen.py:stroke_mask`, `ink_floor`, `free_bands`; `monke._place()` fits rendered reply into safe open bands | screen and inking tests |
| Notebook/page hierarchy | `host/vectors.py` reads notebook/page/viewport/layer metadata; `NotebookSession.log_turn()` tags page; `notebook_context()` and `page_log()` supply history | `tests/test_vectors.py` |
| Structured page map | `host/protocol.py:page_map()` builds notebook/page, old-ink, focus, Monke/graph and free-region metadata for reader/writer | `tests/test_protocol.py` |
| Page/view guard | `_same_view()` and `_same_page()` refuse inking after page change or viewport movement | `tests/test_vectors.py` |

## Implemented, but still needs live rM2 proof

- Page Protocol v1 target metadata and Set-of-Mark labels.
- Model directives and region-aware placement.
- Merged-turn interaction with live page context.

These paths are host/simulation-tested; final physical rM2 live validation remains a release acceptance test.

## Not integrated yet

`host/workspace.py:observe()` can produce deterministic pen/screen-delta regions, but it is not wired into model context. Therefore exact semantic/region references—such as identifying a specific circuit or graph by bounds—remain roadmap work.

Scroll-aware continuation is intentionally conservative: a view/scroll change blocks the draw rather than guessing page coordinates.
