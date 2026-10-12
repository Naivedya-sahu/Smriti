"""pendump: read-only pen-event dump that guesses, per stroke, whether it is
lamp's injected echo or Navy's pen (source tagging prototype, research §6).
Synthetic event streams only — no ssh, no tablet."""
# pyright: reportMissingImports=false

import contextlib
import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import capture
import lampmark
import pendump

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, ABS_PRESSURE, ABS_DISTANCE, ABS_TILT_X, ABS_TILT_Y = 0, 1, 24, 25, 26, 27
BTN_TOOL_PEN, BTN_TOUCH = 320, 330


def ev(kt, etype, code, value):
    sec = int(kt)
    return (sec, int(round((kt - sec) * 1e6)), etype, code, value)


def pen_stroke(kt, n=12, x0=5000, y0=8000):
    """Real-pen-like: hover frame, then touch with varying pressure + tilt."""
    evs = [ev(kt, EV_KEY, BTN_TOOL_PEN, 1), ev(kt, EV_ABS, ABS_DISTANCE, 40),
           ev(kt, EV_SYN, 0, 0)]
    for i in range(n):
        t = kt + 0.01 * (i + 1)
        evs += [ev(t, EV_ABS, ABS_X, x0 + 30 * i), ev(t, EV_ABS, ABS_Y, y0 + 20 * i),
                ev(t, EV_ABS, ABS_PRESSURE, 400 + 150 * i),
                ev(t, EV_ABS, ABS_TILT_X, 1200 - 10 * i),
                ev(t, EV_ABS, ABS_TILT_Y, -900 + 7 * i)]
        if i < 2:
            evs.append(ev(t, EV_ABS, ABS_DISTANCE, 5 if i == 0 else 0))
        if i == 0:
            evs.append(ev(t, EV_KEY, BTN_TOUCH, 1))
        evs.append(ev(t, EV_SYN, 0, 0))
    t = kt + 0.01 * (n + 1)
    evs += [ev(t, EV_ABS, ABS_PRESSURE, 0), ev(t, EV_KEY, BTN_TOUCH, 0), ev(t, EV_SYN, 0, 0),
            ev(t, EV_KEY, BTN_TOOL_PEN, 0), ev(t, EV_SYN, 0, 0)]
    return evs


def lamp_stroke(kt, n=10, p=4000, x0=9000, y0=3000):
    """lamp-like: one injected pressure (alternating p/p+1), no tilt, no distance."""
    evs = [ev(kt, EV_ABS, ABS_X, x0), ev(kt, EV_ABS, ABS_Y, y0),
           ev(kt, EV_ABS, ABS_PRESSURE, p), ev(kt, EV_KEY, BTN_TOOL_PEN, 1),
           ev(kt, EV_KEY, BTN_TOUCH, 1), ev(kt, EV_SYN, 0, 0)]
    for i in range(1, n):
        t = kt + 0.005 * i
        evs += [ev(t, EV_ABS, ABS_X, x0 + 40 * i), ev(t, EV_ABS, ABS_Y, y0 - 25 * i),
                ev(t, EV_ABS, ABS_PRESSURE, p + i % 2), ev(t, EV_SYN, 0, 0)]
    t = kt + 0.005 * n
    evs += [ev(t, EV_KEY, BTN_TOUCH, 0), ev(t, EV_ABS, ABS_PRESSURE, 0),
            ev(t, EV_KEY, BTN_TOOL_PEN, 0), ev(t, EV_SYN, 0, 0)]
    return evs


def segment(events, lag=0.0):
    seg, out = pendump.Segmenter(), []
    for e in events:
        done = seg.feed(e, e[0] + e[1] / 1e6 + lag)
        if done:
            out.append(done)
    return out


class ParseTest(unittest.TestCase):
    def test_parse_uses_capture_struct_and_keeps_partial_tail(self):
        a, b = (1, 2, EV_ABS, ABS_X, 7), (1, 3, EV_SYN, 0, 0)
        buf = struct.pack(capture.EV_FMT, *a) + struct.pack(capture.EV_FMT, *b)
        events, rest = pendump.parse(buf + b"\x01" * (capture.EV_SIZE // 2))
        self.assertEqual(events, [a, b])
        self.assertEqual(len(rest), capture.EV_SIZE // 2)


class SegmentTest(unittest.TestCase):
    def test_mixed_stream_segments_three_strokes(self):
        strokes = segment(pen_stroke(100.0) + lamp_stroke(101.0) + pen_stroke(103.0))
        self.assertEqual(len(strokes), 3)
        self.assertEqual([s["n_points"] for s in strokes], [12, 10, 12])
        self.assertEqual({s["tool"] for s in strokes}, {"pen"})

    def test_real_pen_stroke_features(self):
        s, = segment(pen_stroke(100.0), lag=0.25)
        self.assertEqual(s["n_events"], 77)       # touch frame .. BTN_TOUCH 0
        self.assertAlmostEqual(s["k_start"], 100.01, places=4)
        self.assertAlmostEqual(s["k_end"], 100.13, places=4)
        self.assertAlmostEqual(s["t_start"], 100.26, places=4)
        self.assertAlmostEqual(s["t_end"], 100.38, places=4)
        p = s["pressure"]
        self.assertEqual((p["min"], p["max"], p["n_unique"]), (400, 2050, 12))
        self.assertEqual(len(p["top3"]), 3)
        self.assertTrue(s["tilt_x"]["present"])
        self.assertEqual((s["tilt_x"]["min"], s["tilt_x"]["max"]), (1090, 1200))
        self.assertEqual((s["tilt_y"]["min"], s["tilt_y"]["max"]), (-900, -823))
        self.assertEqual((s["distance"]["min"], s["distance"]["max"]), (0, 5))
        pts = [capture.to_screen(5000 + 30 * i, 8000 + 20 * i) for i in range(12)]
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        self.assertEqual(s["bbox"], [min(xs), min(ys), max(xs), max(ys)])

    def test_lamp_stroke_features(self):
        s, = segment(lamp_stroke(50.0))
        p = s["pressure"]
        self.assertEqual((p["min"], p["max"], p["n_unique"]), (4000, 4001, 2))
        self.assertEqual(p["top3"][0], [4000, 5])
        self.assertFalse(s["tilt_x"]["present"])
        self.assertFalse(s["tilt_y"]["present"])
        self.assertFalse(s["distance"]["present"])

    def test_pressure_spans_segment_when_no_btn_touch(self):
        evs = []
        for k, p in enumerate([0, 300, 900, 1200, 0, 0, 500, 700, 0]):
            evs += [ev(10 + k * 0.01, EV_ABS, ABS_PRESSURE, p), ev(10 + k * 0.01, EV_SYN, 0, 0)]
        strokes = segment(evs)
        self.assertEqual([s["n_points"] for s in strokes], [3, 2])
        self.assertEqual({s["seg"] for s in strokes}, {"pressure"})


class ClassifyTest(unittest.TestCase):
    def setUp(self):
        self.pen, self.lamp = segment(pen_stroke(100.0))[0], segment(lamp_stroke(200.0))[0]
        self.win = [(199.5, 200.2)]

    def test_lamp_in_window_is_lamp(self):
        r = pendump.classify(dict(self.lamp), self.win)
        self.assertEqual((r["tag"], r["window_tag"], r["fp_tag"]), ("lamp", "lamp", "lamp"))
        self.assertEqual(r["window"], 0)
        self.assertTrue(r["reasons"])

    def test_real_pen_outside_windows_is_pen(self):
        r = pendump.classify(dict(self.pen), self.win)
        self.assertEqual((r["tag"], r["window_tag"], r["fp_tag"]), ("pen", "pen", "pen"))
        self.assertTrue(any("tilt" in x for x in r["reasons"]))

    def test_navy_writing_during_a_batch_is_unknown(self):
        r = pendump.classify(dict(self.pen), [(99.0, 101.0)])
        self.assertEqual((r["tag"], r["window_tag"], r["fp_tag"]), ("unknown", "lamp", "pen"))

    def test_lamp_like_outside_windows_is_unknown(self):
        r = pendump.classify(dict(self.lamp), [])
        self.assertEqual((r["tag"], r["window_tag"], r["fp_tag"]), ("unknown", "pen", "lamp"))

    def test_slack_covers_ssh_lag(self):
        late = segment(lamp_stroke(200.0), lag=1.0)[0]       # echo 1 s after the window
        win = [(199.0, 200.0)]
        self.assertEqual(pendump.classify(dict(late), win, slack=1.5)["tag"], "lamp")
        self.assertEqual(pendump.classify(dict(late), win, slack=0.5)["tag"], "unknown")


def mixed_records():
    win = [(199.0, 260.0)]
    recs = [segment(lamp_stroke(200.0 + i))[0] for i in range(3)]       # lamp, in window
    recs += [segment(pen_stroke(100.0 + i))[0] for i in range(2)]       # pen, outside
    recs += [segment(pen_stroke(230.0))[0]]                             # pen during batch
    recs += [segment(lamp_stroke(300.0))[0]]                            # lamp-like, outside
    return [pendump.classify(r, win) for r in recs], win


class ReportTest(unittest.TestCase):
    def test_summary_numbers(self):
        recs, _ = mixed_records()
        s = pendump.summarize(recs)
        self.assertEqual(s["n"], 7)
        self.assertEqual({t: v["n"] for t, v in s["by_tag"].items()},
                         {"lamp": 3, "pen": 2, "unknown": 2})
        self.assertEqual(s["by_tag"]["lamp"]["tilt_pct"], 0)
        self.assertEqual(s["by_tag"]["pen"]["tilt_pct"], 100)
        self.assertEqual(s["by_tag"]["lamp"]["p_min"], 4000)
        self.assertEqual(s["matrix"]["lamp"], {"lamp": 3, "pen": 1, "unknown": 0})
        self.assertEqual(s["matrix"]["pen"], {"lamp": 1, "pen": 2, "unknown": 0})
        self.assertEqual((s["agree"], s["comparable"]), (5, 7))
        self.assertEqual(s["in_window_fp_lamp"], [3, 4])
        self.assertEqual(s["out_window_fp_lamp"], [1, 3])
        text = pendump.format_report(s)
        self.assertIn("agreement", text)
        self.assertIn("5/7", text)

    def test_report_cli_retags_with_windows_file(self):
        recs, win = mixed_records()
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "dump.jsonl"
            with open(f, "w", encoding="utf-8") as fh:
                for r in recs:                                  # live guess: no windows known
                    fh.write(json.dumps(pendump.classify(r, [])) + "\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(pendump.main(["report", str(f)]), 0)
            self.assertIn("unknown", out.getvalue())
            lampmark.mark(*win[0], 3, "batch", path=Path(d) / "lamp-windows.jsonl")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(pendump.main(["report", str(f)]), 0)
            s = pendump.report(str(f), quiet=True)
            self.assertEqual(s["by_tag"]["lamp"]["n"], 3)
            self.assertIn("5/7", out.getvalue())

    def test_no_args_prints_usage(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(pendump.main([]), 2)
        self.assertIn("usage", out.getvalue())


class DumperTest(unittest.TestCase):
    def test_writes_one_flushed_line_per_stroke_after_hold(self):
        with tempfile.TemporaryDirectory() as d:
            wpath, out = Path(d) / "lamp-windows.jsonl", Path(d) / "dump.jsonl"
            dumper = pendump.Dumper(out, wpath, slack=1.5, hold=5.0)
            for e in pen_stroke(100.0) + lamp_stroke(200.0):
                dumper.feed(e, e[0] + e[1] / 1e6)
            lampmark.mark(199.9, 200.1, 1, path=wpath)          # marked after the dumper started
            self.assertEqual(dumper.flush(now=201.0), 1)        # pen is old enough, lamp is held
            self.assertEqual(dumper.flush(now=206.0), 1)
            dumper.close()
            lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([x["tag"] for x in lines], ["pen", "lamp"])
            self.assertEqual(dumper.n_written, 2)


class LampmarkTest(unittest.TestCase):
    def test_roundtrip_in_temp_dir(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "lw.jsonl"
            lampmark.mark(1.0, 2.5, 3, "reply", path=p)
            lampmark.mark(4.0, 4.2, 1, path=p)
            got = lampmark.read(p)
            self.assertEqual([(g["t0"], g["t1"], g["n_strokes"], g["note"]) for g in got],
                             [(1.0, 2.5, 3, "reply"), (4.0, 4.2, 1, "")])
            self.assertEqual(pendump.load_windows(p), [(1.0, 2.5), (4.0, 4.2)])

    def test_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            lampmark.mark(1.0, 2.0, 1, path=Path(d))          # a directory, not a file
            lampmark.mark(None, "x", object(), path=Path(d) / "a.jsonl")
            self.assertEqual(lampmark.read(Path(d) / "missing.jsonl"), [])


if __name__ == "__main__":
    unittest.main()
