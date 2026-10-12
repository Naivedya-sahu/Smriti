"""Run 13: Navy scrolled by hand (one-finger vertical drag); TouchGesture
flagged only fast horizontal swipes, so floor/baseline/echo stayed on the
old view. A vertical drag now reaches the page-turn handler, which already
labels it 'scrolled (same page)' by the unchanged page id."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import monke

T, X, Y = monke.ABS_MT_TRACKING_ID, monke.ABS_MT_POSITION_X, monke.ABS_MT_POSITION_Y
ev = lambda c, v: (0, 0, 3, c, v)


def feed(g, seq):
    return [r for e in seq if (r := g.feed(e))]


class ScrollGestureTest(unittest.TestCase):
    def test_slow_vertical_drag_is_reported(self):
        g, t = monke.TouchGesture(), [100.0]
        with patch.object(monke.time, "time", lambda: t[0]):
            out = feed(g, [ev(T, 1), ev(X, 400), ev(Y, 800)])
            t[0] += 1.5                                   # a slow, deliberate scroll
            out += feed(g, [ev(Y, 500), ev(Y, 200), ev(X, 410), ev(T, -1)])
        self.assertEqual(out, ["pageturn"])

    def test_short_vertical_wiggle_is_not(self):
        g = monke.TouchGesture()
        self.assertEqual(feed(g, [ev(T, 1), ev(X, 400), ev(Y, 500), ev(Y, 470), ev(T, -1)]), [])

    def test_start_y_is_this_contacts_first_position(self):
        g = monke.TouchGesture()
        feed(g, [ev(Y, 900), ev(T, -1)])                  # an earlier touch
        self.assertEqual(feed(g, [ev(T, 2), ev(X, 400), ev(Y, 100), ev(Y, 110), ev(T, -1)]), [])


if __name__ == "__main__":
    unittest.main()
