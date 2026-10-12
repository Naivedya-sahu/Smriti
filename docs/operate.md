# Smriti — operator commands (laptop + RM2)

Laptop = daemon box. Run the laptop commands in **Git Bash from the repo root**
(`D:\Vault\Archive\Smriti`). RM2 commands run over USB with `ssh rm2 '…'`.
What the app can do: `docs/capabilities.md`.

> **0.5.0 status:** ChatGPT direct transport through Hermes-held auth is default. No local-model/Gemini fallback exists. The daemon is deliberately stopped during quota pause. `smriti-ask.qmd` is **not installed**: tablet qmldiff rejected its current syntax. Start only for a planned test; current evidence is in `STATE.md`.

## 0. Something wrong? Run the doctor first

```bash
PYTHONUTF8=1 uv run python host/monke.py doctor
```
Read-only. Checks every link (USB ssh, eye patch, armed, daemon, eye address, notebook,
screenshot, app route, Claude budget, xochitl memory) and prints the fix under each FAIL.

| Symptom | Usual cause | Fix |
|---|---|---|
| Tap the eye, nothing happens | daemon not running | start it (§2) |
| Greets, then gets erased / session goes idle, no answer | session stopped (hold, or USB reset mid-session) | `doctor`; restart the daemon; tap the eye again |
| Eye shows `!` / no answer | ChatGPT or the explicitly selected Claude route failed | Read daemon log; error is intentional and there is no automatic model fallback |
| Screen freezes, xochitl restarts | xochitl out of memory | `doctor` memory row; report it (the eye leak is fixed in c98e989) |
| Eye missing after a tablet reboot | xovi not started | `ssh rm2 /home/root/xovi/start` |

## 1. Check the tablet

Reachable over USB:
```bash
ssh rm2 uptime
```
Verify: prints the uptime. If it times out, reseat the cable / wake the tablet.

Eye patch loaded (needed after every tablet **reboot**):
```bash
ssh rm2 'journalctl -u xochitl -b --no-pager | grep -c "Loading file smriti-eye"'
```
Verify: `1` or more. If `0`, start xovi (restarts xochitl; reopen your notebook):
```bash
ssh rm2 /home/root/xovi/start
```

Armed (the bridge does nothing until armed):
```bash
ssh rm2 cat /home/root/.smriti/armed
```
Verify: `1`. To arm:
```bash
ssh rm2 'echo 1 > /home/root/.smriti/armed'
```

## 2. Start the daemon (laptop)

```bash
PYTHONUTF8=1 uv run python host/monke.py
```
Same in **PowerShell** (the `VAR=1 cmd` form is Bash-only):
```powershell
$env:PYTHONUTF8="1"; uv run python host/monke.py
```
Verify: the log shows `[control] tablet eye -> 10.11.99.x:7333` (the eye was pointed here;
missing = the tablet was unreachable at start, restart the daemon) and ends with
`monke is idle — TAP the corner eye…`. Keep this window open;
Ctrl-C stops it. **No daemon = the eye does nothing** (bridge state `offline`).

Eye state as the tablet sees it:
```bash
ssh rm2 cat /home/root/.smriti/state
```
Verify: `idle` (daemon up), `watching` (session on), `offline` (no daemon).

## 3. Start / stop a session

On the tablet: **tap the eye** = start (Monke greets), **hold the eye** = stop.
The same from the laptop:
```bash
ssh rm2 /home/root/.vellum/bin/smriti-eye start
```
```bash
ssh rm2 /home/root/.vellum/bin/smriti-eye stop
```
```bash
ssh rm2 /home/root/.vellum/bin/smriti-eye status
```
Verify: `status` prints `watching` / `idle` within a second. Empty after ~10 s (plus shell
"Killed" noise) = daemon not running.

## 4. Claude budget and caps

Today's calls:
```bash
PYTHONUTF8=1 uv run python host/monke.py claude-status
```
Verify selected provider availability. At a quota limit the turn fails visibly; it does not fall back to a local model or Gemini.

Caps live in `config.toml` → `[claude]`: `daily_calls = 50`, `session_calls = 25`.
Raise for one day (edit, then restart the daemon):
```bash
sed -i 's/^daily_calls = 50 /daily_calls = 65 /' config.toml && grep -n "^daily_calls" config.toml
```
Put it back afterwards:
```bash
sed -i 's/^daily_calls = 65 /daily_calls = 50 /' config.toml && grep -n "^daily_calls" config.toml
```

## 5. Other knobs (`config.toml` → `[monke]`, restart the daemon after editing)

| Key | Default | Effect |
|---|---|---|
| `confirm` | true | popup to check / edit the reading before maths, circuits, drawings |
| `greet_fade` | true | greeting disappears when the first answer lands |
| `safe_layer` | true | all Monke ink on its own layer (keep on) |
| `waypoint_step` | 0 | 0 = bends only (fastest); N = also every Nth point |
| `page_bottom` | 1780 | a reply past this line turns the page |
| `stream` | true | ink line by line while the reply is written (not in the file: add `stream = false` to turn off) |
| `verify` | true | screenshot check after ink, redraws missing strokes (not in the file: add `verify = false` to turn off) |

**Handwriting quality** (restart the daemon after a change; edit in Git Bash or VS Code,
not PowerShell `Set-Content`, which adds a BOM):

| Knob | File | Default | Smoother |
|---|---|---|---|
| `waypoint_step` | `config.toml` `[monke]` | 0 (bends ≤2 px, fastest) | 3 (earlier look) or 1 (every point) |
| `decimate` | `styles.toml` `[style.cursive]` | 2 | 1 |
| `reply_size` | `config.toml` `[monke]` | 64 (style) | 72–80 |
| `reply_style` | `config.toml` `[monke]` | cursive | print / cartoon |

Preview without the tablet:
```bash
PYTHONUTF8=1 uv run python host/write.py "Monke ready - smooth test" --style cursive --out preview.png
```

`[capture]` `idle_seconds = 2.8`: the pause after your last stroke before Monke reads.
Model choice (reader / writer / profile) is set in the Smriti app on the tablet.

## 6. Health checks

Layers + pen safety (writes and cleans two probe layers; use a page with room):
```bash
PYTHONUTF8=1 uv run python host/layers.py probe
```
Verify: `6/6 behaviours as expected`.

xochitl memory (the fixed eye stays flat, ~200–250 MB):
```bash
ssh rm2 'grep VmRSS /proc/$(pidof xochitl)/status'
```
Verify: under ~400 MB; if it climbs steadily, stop the daemon and report.

Screen streamer after a xochitl restart (screenshots fail otherwise):
```bash
ssh rm2 systemctl restart goMarkableStream
```

Offline tests (no tablet needed):
```bash
PYTHONUTF8=1 uv run python -m unittest discover -s tests
```
Verify: `OK`.

## 7. Offline replay (no tablet)

Run the real reader and writer prompts against saved page PNGs:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe host/replay.py host/evalpages/text-cursive.png --no-write
```
Compare prompt trees or turn modes:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe host/replay.py host/evalpages/*.png --variant A=skill/smriti --variant B=D:/tmp/skill-b --mode both --repeat 3
```
Results land under `runs/replay/<timestamp>/results.jsonl` and `summary.md`. Add
`--render` for an offline ink preview. Claude aliases are refused unless
`--allow-claude` is explicit.
