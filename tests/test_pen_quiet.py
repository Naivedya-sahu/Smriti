"""Run 12: lamp ink while Navy's pen hovered — lamp and the pen share
/dev/input/event1, xochitl merged them: zigzag lines over the LTI diagram
and strike lines through two replies. Lamp waits for a quiet pen."""
# pyright: reportMissingImports=false

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import capture
import monke


class PenQuietTest(unittest.TestCase):
    def test_idle_pen_does_not_wait(self):
        capture.Capture.pen_last = 0.0
        t = time.time()
        self.assertTrue(monke._pen_quiet(quiet=0.3, cap=2))
        self.assertLess(time.time() - t, 0.1)

    def test_hovering_pen_waits_until_quiet(self):
        stop = time.time() + 0.5

        def hover():
            while time.time() < stop:
                capture.Capture.pen_last = time.time()
                time.sleep(0.01)
        threading.Thread(target=hover, daemon=True).start()
        time.sleep(0.05)
        t = time.time()
        self.assertTrue(monke._pen_quiet(quiet=0.3, cap=5))
        self.assertGreaterEqual(time.time() - t, 0.6)

    def test_cap_gives_up(self):
        stop = time.time() + 2

        def hover():
            while time.time() < stop:
                capture.Capture.pen_last = time.time()
                time.sleep(0.01)
        threading.Thread(target=hover, daemon=True).start()
        time.sleep(0.05)
        self.assertFalse(monke._pen_quiet(quiet=0.3, cap=0.5))

    def test_lamp_waits_first(self):
        calls = []
        orig_q, orig_run = monke._pen_quiet, monke.tablet.run
        monke._pen_quiet = lambda *a, **k: calls.append("quiet") or True
        monke.tablet.run = lambda *a, **k: calls.append("run") or (0, b"")
        try:
            monke.lamp("pen down 1 1\n", "rm2")
        finally:
            monke._pen_quiet, monke.tablet.run = orig_q, orig_run
        self.assertEqual(calls, ["quiet", "run"])


if __name__ == "__main__":
    unittest.main()
