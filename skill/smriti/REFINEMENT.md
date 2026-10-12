# Smriti skill — refinement backlog (draft v0, 2026-10-09)

## Blocker: the endpoint cannot load a native Claude skill today

`host/oracle.py:180-190` runs `claude -p --setting-sources "" --tools ""|Read
--system-prompt <MONKE_SYSTEM> --max-turns 1|3`. With these flags:
- `--setting-sources ""` → no user/project settings, so `CLAUDE_CONFIG_DIR/skills` is not read.
- `--tools ""` → no Skill tool; the model cannot open a skill even if listed.
- `--max-turns 1` → a Skill call would cost the only turn.

Turning native skills on = an extra tool round-trip per turn (latency + tokens) and
non-deterministic loading. **Monke way:** this folder is the single source of truth in
Agent Skills format; `host/skill.py` composes `SKILL.md` + ONE `kinds/<k>.md` into
`--system-prompt` deterministically. Same files can still be installed as a native skill
in Claude Desktop/Code for offline prompt testing.

## Wiring plan (one bounded Claude Code pass, test first)

1. `host/skill.py`: `load(kind|profile) -> (read_system, reply_system)`; cache by mtime.
2. Regression test: composed text for each profile contains every rule now in
   `MONKE_SYSTEM`, `READ_SYSTEM`, `SKILLS`, `PROFILES` (no rule lost in the move).
3. Swap call sites: `monke.py:1338,1388,1541`, `harness.py:101,240,352`.
4. `tests/sim.py` 20/20 + unittest green before and after.

## Content gaps (datatypes the endpoint does not handle yet)

| # | Datatype / case | Today | Proposal |
|---|---|---|---|
| 1 | Tables (truth tables, data) | Prose or nothing | `$$\begin{array}...$$` rule + room cost |
| 2 | Code snippets | ASCII prose, loses indentation | `$$\begin{verbatim}$$`-like block or monospace ink style |
| 3 | Lists / checklists / habits | Free prose | Numbered short lines, one per unit (streams well) |
| 4 | Timing / logic diagrams | SVG guesswork | `tikz-timing` package rule; logic gates via circuitikz `american and port` |
| 5 | Chemistry / units | ad hoc | `siunitx` for units (`\SI{1.59}{kHz}`), `chemfig` if ever needed |
| 6 | Page / PDF context (annotating a document) | treated as black context | kind `document`: answer about the highlighted region only |
| 7 | Multi-page memory | per-notebook Claude session only | short per-notebook memory file the daemon appends (facts Navy states) |
| 8 | Language | English only | mirror the writer's language for prose — needs a non-ASCII stroke font first |
| 9 | Refusals ("New page needed") | still appear (run 16) | grammar forbids it (added); test via sim canned reply |
| 10 | Greeting | cut by code | move "<= 14 words, no block" into the greet section (added) |

## Persona gaps

- Persona is hard-coded to Navy. Make `persona/` a folder: `monke.md` default, others
  (tutor, tom-riddle, stoic) selectable in the app like riddle/Inka personas.
- Voice rules vs grammar: persona file may change tone and word cap, never the grammar.

## New text in v0 not present in code (review before wiring)

- kinds/math.md: aligned-block advice, `\cdot` over `\times`, units inside blocks.
- kinds/graph.md: explicit ticks for small ranges; 7.5 cm per axis.
- kinds/sketch.md: "labels are literal words" (from run 4 `{}` labels).
- SKILL.md: "never write new page needed", streamed-unit line rule.
