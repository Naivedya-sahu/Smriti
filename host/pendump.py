"""
pendump.py — read-only pen-event dump with a per-stroke source guess:
lamp's injected echo vs Navy's real pen (research-deterministic-middleware §6,
P0 probe). Evidence collector only; nothing in monke.py reads it.

    python host/pendump.py run [--host rm2] [--seconds N] [--slack 1.5] [--hold 5]
    python host/pendump.py report build/pendump/<stamp>.jsonl [--windows F]

`run` opens its OWN `ssh <host> cat /dev/input/event1` (evdev allows many
readers; no grab, nothing written on the tablet), segments strokes
(BTN_TOUCH down..up; pressure>0 spans until a BTN_TOUCH is seen) and appends
one flushed JSON line per stroke to build/pendump/<YYYYmmdd-HHMMSS>.jsonl.
Reconnects when the stream drops; Ctrl-C (or --seconds) exits clean.

Tag guess per stroke (with reasons):
    lamp     inside a lamp window (lampmark, ± slack) AND fingerprint = lamp
             (pressure spread <= tol, no tilt)
    pen      outside every window AND fingerprint = pen (pressure varies or tilt)
    unknown  anything else (Navy writing during a batch, a constant-pressure
             stroke outside windows, no pressure samples)
A stroke is held `hold` s before it is tagged, so the window that lamp()
marks on return can land first. `report` re-tags from the final windows
file — that is the authoritative read for long batches.
"""

from __future__ import annotations

import argparse
import json
import queue
import statistics
import struct
import subprocess

from deps import ssh_argv
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

import lampmark
from capture import (ABS_PRESSURE, ABS_X, ABS_Y, BTN_TOUCH, EV_ABS, EV_FMT, EV_KEY,
                     EV_SIZE, EV_SYN, to_screen)

ABS_DISTANCE, ABS_TILT_X, ABS_TILT_Y = 25, 26, 27
BTN_TOOL_PEN, BTN_TOOL_RUBBER = 320, 321
TOOLS = {BTN_TOOL_PEN: "pen", BTN_TOOL_RUBBER: "rubber"}

SLACK = 1.5          # s around a lamp window: ssh lag of the echo
PRESSURE_TOL = 2     # lamp alternates p / p+1; spread <= tol = "constant"
HOLD = 5.0           # s a finished stroke waits for its lamp window to be marked
import paths
OUT_DIR = (paths.data_dir() / "pendump" if paths.overlay_active()
           else Path(__file__).resolve().parents[1] / "build" / "pendump")
TAGS = ("lamp", "pen", "unknown")

USAGE = ("usage: pendump.py run [--host rm2] [--device /dev/input/event1] [--out DIR] "
         "[--windows FILE] [--slack 1.5] [--tol 2] [--hold 5] [--seconds N]\n"
         "       pendump.py report FILE.jsonl [--windows FILE] [--slack 1.5] [--tol 2]")


def parse(buf: bytes) -> tuple[list[tuple], bytes]:
    """Whole input_events (capture.EV_FMT) out of buf; the partial tail back."""
    n = len(buf) - len(buf) % EV_SIZE
    return list(struct.iter_unpack(EV_FMT, buf[:n])), buf[n:]


def _kt(ev) -> float:
    return ev[0] + ev[1] / 1e6


def _rng(v: list) -> dict:
    return {"present": bool(v), "min": min(v) if v else None, "max": max(v) if v else None}


class Segmenter:
    """Feed (event, host time); get a stroke feature dict back at stroke end."""

    def __init__(self):
        self.ax = self.ay = self.p = 0
        self.tool: str | None = None
        self.saw_touch = False
        self.frame: list = []            # (host_t, ev) since the last SYN, no stroke open
        self.cur: Any = None             # open stroke accumulator

    def feed(self, ev, host_t: float) -> dict | None:
        _, _, et, code, val = ev
        if et == EV_ABS:
            if code == ABS_X:
                self.ax = val
            elif code == ABS_Y:
                self.ay = val
            elif code == ABS_PRESSURE:
                self.p = val
        elif et == EV_KEY and code in TOOLS:
            if val:
                self.tool = TOOLS[code]
            elif self.tool == TOOLS[code]:
                self.tool = None
        if self.cur is not None:
            self._add(ev, host_t)
        else:
            self.frame.append((host_t, ev))
        if et == EV_KEY and code == BTN_TOUCH:
            self.saw_touch = True
            if val and self.cur is None:
                self._open("touch")
            elif val:
                self.cur["seg"] = "touch"          # a pressure span became a touch
            elif self.cur is not None:
                return self._close()
        elif et == EV_SYN:
            if self.cur is None and not self.saw_touch and self.p > 0:
                self._open("pressure")
            if self.cur is not None:
                if self.cur["seg"] == "pressure" and self.p <= 0:
                    return self._close()
                self.cur["pts"].append(to_screen(self.ax, self.ay))
                if self.p > 0:
                    self.cur["p"].append(self.p)
            self.frame = []
        return None

    def _open(self, seg: str) -> None:
        h0, e0 = self.frame[0]
        self.cur = {"seg": seg, "n_events": 0, "t_start": h0, "k_start": _kt(e0),
                    "tool": self.tool, "tx": [], "ty": [], "dist": [], "p": [], "pts": []}
        for h, e in self.frame:
            self._add(e, h)
        self.frame = []

    def _add(self, ev, host_t: float) -> None:
        c = self.cur
        c["n_events"] += 1
        c["t_end"], c["k_end"] = host_t, _kt(ev)
        if ev[2] == EV_ABS:
            key = {ABS_TILT_X: "tx", ABS_TILT_Y: "ty", ABS_DISTANCE: "dist"}.get(ev[3])
            if key:
                c[key].append(ev[4])
        if c["tool"] is None:
            c["tool"] = self.tool

    def _close(self) -> dict:
        c, self.cur = self.cur, None
        ps, pts = c["p"], c["pts"]
        cnt = Counter(ps)
        top3 = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        return {
            "t_start": round(c["t_start"], 3), "t_end": round(c["t_end"], 3),
            "k_start": round(c["k_start"], 6), "k_end": round(c["k_end"], 6),
            "seg": c["seg"], "n_events": c["n_events"], "tool": c["tool"] or "none",
            "pressure": {"n": len(ps), "min": min(ps) if ps else None,
                         "max": max(ps) if ps else None, "n_unique": len(cnt),
                         "top3": [[v, n] for v, n in top3]},
            "tilt_x": _rng(c["tx"]), "tilt_y": _rng(c["ty"]), "distance": _rng(c["dist"]),
            "bbox": [min(xs), min(ys), max(xs), max(ys)] if pts else None,
            "n_points": len(pts),
        }


def _tilt_active(s: dict) -> bool:
    """Tilt reported and not a constant 0 (an injector could send a flat 0)."""
    return any(s[k]["present"] and not (s[k]["min"] == s[k]["max"] == 0)
               for k in ("tilt_x", "tilt_y"))


def classify(s: dict, windows: list, slack: float = SLACK, tol: int = PRESSURE_TOL) -> dict:
    """Adds window / window_tag / fp_tag / tag / reasons to stroke dict s."""
    win = None
    for i, (a, b) in enumerate(windows):
        if s["t_start"] <= b + slack and s["t_end"] >= a - slack:
            win = i
            break
    reasons = [f"outside {len(windows)} lamp window(s) (+-{slack:g} s)" if win is None else
               f"inside lamp window #{win} [{windows[win][0]:.3f}, {windows[win][1]:.3f}] +-{slack:g} s"]
    p, tilt = s["pressure"], _tilt_active(s)
    spread = (p["max"] - p["min"]) if p["n"] else None
    if spread is None:
        reasons.append("no pressure samples")
    elif spread <= tol:
        reasons.append(f"pressure constant {p['min']}-{p['max']} (spread {spread} <= {tol})")
    else:
        reasons.append(f"pressure varies {p['min']}-{p['max']} ({p['n_unique']} values)")
    reasons.append("tilt present" if tilt else "no tilt")
    fp = "pen" if tilt or (spread is not None and spread > tol) else \
         "lamp" if spread is not None else "unknown"
    wt = "pen" if win is None else "lamp"
    s.update(window=win, window_tag=wt, fp_tag=fp, reasons=reasons,
             tag="lamp" if wt == fp == "lamp" else "pen" if wt == fp == "pen" else "unknown")
    return s


def load_windows(path) -> list[tuple[float, float]]:
    return [(float(d["t0"]), float(d["t1"])) for d in lampmark.read(path)]


class Dumper:
    """Segment, hold, tag, append: one flushed JSON line per stroke."""

    def __init__(self, out, windows_path, slack: float = SLACK, tol: int = PRESSURE_TOL,
                 hold: float = HOLD):
        self.out, self.wpath = Path(out), Path(windows_path)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.out, "a", encoding="utf-8")
        self.slack, self.tol, self.hold = slack, tol, hold
        self.seg, self.pending = Segmenter(), []
        self.windows: list = []
        self._wsig = None
        self.n_events = self.n_written = 0

    def feed(self, ev, host_t: float) -> None:
        self.n_events += 1
        s = self.seg.feed(ev, host_t)
        if s:
            self.pending.append(s)

    def reset_stream(self) -> None:
        self.seg = Segmenter()           # new ssh stream: a half stroke is dropped

    def _windows(self) -> list:
        try:
            st = self.wpath.stat()
            sig = (st.st_size, st.st_mtime_ns)
        except OSError:
            sig = None
        if sig != self._wsig:
            self._wsig, self.windows = sig, load_windows(self.wpath) if sig else []
        return self.windows

    def flush(self, now: float | None = None, force: bool = False) -> int:
        now = time.time() if now is None else now
        ready = [s for s in self.pending if force or now - s["t_end"] >= self.hold]
        if not ready:
            return 0
        self.pending = [s for s in self.pending if not (force or now - s["t_end"] >= self.hold)]
        wins = self._windows()
        for s in ready:
            self.f.write(json.dumps(classify(s, wins, self.slack, self.tol)) + "\n")
            self.f.flush()
        self.n_written += len(ready)
        return len(ready)

    def close(self) -> None:
        self.flush(force=True)
        self.f.close()


class Stream:
    """ssh `cat <device>` → queue of (host_t, bytes), None on EOF. Read-only:
    plain cat, no EVIOCGRAB, so xochitl and the daemon keep reading too.
    The remote shell waits on stdin (an open, never-written pipe) and kills
    its cat when ssh goes away — a bare `exec cat` lingered as an orphan
    until the next pen event (smoke test 2026-10-10)."""

    def __init__(self, host: str, device: str):
        self.proc: Any = subprocess.Popen(
            [*ssh_argv(host, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                       "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3"),
             f"echo pendump-ok >&2; cat {device} & p=$!; cat >/dev/null; kill $p"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.q: queue.Queue = queue.Queue()
        self.ok = threading.Event()
        self.err: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()

    def _pump(self) -> None:
        while True:
            try:
                chunk = self.proc.stdout.raw.read(EV_SIZE * 256)
            except (OSError, ValueError):
                chunk = b""
            if not chunk:
                self.q.put(None)
                return
            self.q.put((time.time(), chunk))

    def _stderr(self) -> None:
        for line in self.proc.stderr:
            t = line.decode(errors="replace").strip()
            if t == "pendump-ok":
                self.ok.set()
            elif t:
                self.err.append(t)

    def close(self) -> int | None:
        try:
            self.proc.kill()
            return self.proc.wait(timeout=5)
        except Exception:
            return None


def run(host: str = "rm2", device: str = "/dev/input/event1", out_dir=OUT_DIR,
        windows=None, slack: float = SLACK, tol: int = PRESSURE_TOL, hold: float = HOLD,
        seconds: float | None = None) -> int:
    print(USAGE, flush=True)
    out = Path(out_dir) / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    dumper = Dumper(out, windows or lampmark.PATH, slack, tol, hold)
    print(f"[pendump] {host}:{device} -> {out} (windows {dumper.wpath}, slack {slack:g} s, "
          f"hold {hold:g} s); Ctrl-C stops", flush=True)
    end = time.time() + seconds if seconds else None
    alive = lambda: end is None or time.time() < end   # noqa: E731
    stream = None
    try:
        while alive():
            stream, buf, said = Stream(host, device), b"", False
            dumper.reset_stream()
            dropped = False
            while alive():
                if not said and stream.ok.is_set():
                    print(f"[pendump] connected: {device} open on {host}", flush=True)
                    said = True
                try:
                    item = stream.q.get(timeout=0.2)
                except queue.Empty:
                    dumper.flush()
                    continue
                if item is None:
                    dropped = True
                    break
                evs, buf = parse(buf + item[1])
                for e in evs:
                    dumper.feed(e, item[0])
                dumper.flush()
            if not dropped:
                break
            rc = stream.close()
            why = f": {stream.err[-1]}" if stream.err else ""
            print(f"[pendump] stream dropped (ssh rc={rc}{why}); reconnecting in 2 s", flush=True)
            for _ in range(10):
                if not alive():
                    break
                time.sleep(0.2)
    except KeyboardInterrupt:
        print("[pendump] Ctrl-C", flush=True)
    finally:
        if stream:
            stream.close()
        dumper.close()
    print(f"[pendump] done: {dumper.n_events} events, {dumper.n_written} strokes -> {out}",
          flush=True)
    return 0


def load_records(path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def summarize(recs: list[dict]) -> dict:
    med = lambda v: statistics.median(v) if v else None   # noqa: E731
    by = {}
    for t in TAGS:
        rs = [r for r in recs if r.get("tag") == t]
        if not rs:
            continue
        ps = [r["pressure"] for r in rs if r["pressure"]["n"]]
        by[t] = {"n": len(rs),
                 "p_min": min((p["min"] for p in ps), default=None),
                 "p_max": max((p["max"] for p in ps), default=None),
                 "uniq_med": med([p["n_unique"] for p in ps]),
                 "spread_med": med([p["max"] - p["min"] for p in ps]),
                 "tilt_pct": round(100 * sum(r["tilt_x"]["present"] or r["tilt_y"]["present"]
                                             for r in rs) / len(rs)),
                 "points_med": med([r["n_points"] for r in rs])}
    matrix = {w: {f: 0 for f in TAGS} for w in ("lamp", "pen")}
    for r in recs:
        matrix[r["window_tag"]][r["fp_tag"]] += 1
    fps = [r for r in recs if r["fp_tag"] != "unknown"]
    inw = [r for r in recs if r["window_tag"] == "lamp"]
    outw = [r for r in recs if r["window_tag"] == "pen"]
    return {"n": len(recs), "by_tag": by, "matrix": matrix,
            "agree": sum(r["fp_tag"] == r["window_tag"] for r in fps), "comparable": len(fps),
            "in_window_fp_lamp": [sum(r["fp_tag"] == "lamp" for r in inw), len(inw)],
            "out_window_fp_lamp": [sum(r["fp_tag"] == "lamp" for r in outw), len(outw)]}


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} = {100 * a / b:.1f}%" if b else f"{a}/0 = n/a"


def format_report(s: dict) -> str:
    f = lambda v: "-" if v is None else f"{v:g}" if isinstance(v, float) else str(v)  # noqa: E731
    lines = [f"{s['n']} strokes",
             f"{'tag':8}{'n':>6}{'p_min':>7}{'p_max':>7}{'uniq~':>7}{'spread~':>9}{'tilt%':>7}{'pts~':>6}"]
    for t, v in s["by_tag"].items():
        lines.append(f"{t:8}{v['n']:>6}{f(v['p_min']):>7}{f(v['p_max']):>7}{f(v['uniq_med']):>7}"
                     f"{f(v['spread_med']):>9}{v['tilt_pct']:>7}{f(v['points_med']):>6}")
    head = "window / fingerprint"
    lines += ["", f"{head:22}{'lamp':>6}{'pen':>6}{'unknown':>9}"]
    for w in ("lamp", "pen"):
        m = s["matrix"][w]
        label = "in lamp window" if w == "lamp" else "outside windows"
        lines.append(f"{label:22}{m['lamp']:>6}{m['pen']:>6}{m['unknown']:>9}")
    lines += ["",
              f"agreement window vs fingerprint (fingerprinted strokes): {_pct(s['agree'], s['comparable'])}",
              f"in-window strokes fingerprinting lamp:     {_pct(*s['in_window_fp_lamp'])}  (promote: >= 99%)",
              f"out-of-window strokes fingerprinting lamp: {_pct(*s['out_window_fp_lamp'])}  (promote: 0)"]
    return "\n".join(lines)


def report(path, windows_path=None, slack: float = SLACK, tol: int = PRESSURE_TOL,
           quiet: bool = False) -> dict:
    """Summary of a dump. Re-tags from the windows file (default: lamp-windows.jsonl
    next to the dump) when it exists; else keeps the live guesses."""
    recs = load_records(path)
    wpath = Path(windows_path) if windows_path else Path(path).parent / "lamp-windows.jsonl"
    note = f"live tags (no windows file at {wpath})"
    if wpath.exists():
        wins = load_windows(wpath)
        changed = 0
        for r in recs:
            old = r.get("tag")
            changed += classify(r, wins, slack, tol)["tag"] != old
        note = (f"re-tagged with {len(wins)} window(s) from {wpath} (slack {slack:g} s, "
                f"tol {tol}); {changed} differ from the live guess")
    s = summarize(recs)
    if not quiet:
        print(f"[pendump] {path}: {note}")
        print(format_report(s))
    return s


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("run", "report"):
        print(USAGE)
        return 2
    ap = argparse.ArgumentParser(prog="pendump.py", usage=USAGE)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--host", default="rm2")
    r.add_argument("--device", default="/dev/input/event1")
    r.add_argument("--out", default=str(OUT_DIR))
    r.add_argument("--windows", default=None)
    r.add_argument("--slack", type=float, default=SLACK)
    r.add_argument("--tol", type=int, default=PRESSURE_TOL)
    r.add_argument("--hold", type=float, default=HOLD)
    r.add_argument("--seconds", type=float, default=None)
    p = sub.add_parser("report")
    p.add_argument("file")
    p.add_argument("--windows", default=None)
    p.add_argument("--slack", type=float, default=SLACK)
    p.add_argument("--tol", type=int, default=PRESSURE_TOL)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(a.host, a.device, a.out, a.windows, a.slack, a.tol, a.hold, a.seconds)
    report(a.file, a.windows, a.slack, a.tol)
    return 0


if __name__ == "__main__":
    sys.exit(main())
