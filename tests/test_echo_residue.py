"""Run 12 (2026-09-30 21:15): 'Summarize whole page' written while a reply
inked was never read — the post-reply baseline already held it, page_diff
said 'no change', 15 real strokes were thrown away as echo. Echo residue
after drop_echo is 0-6 strokes (all runs); Navy's writing is more."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import monke

SAME = Image.new("L", (100, 100), 255)
S = [(10, 10), (20, 20)]


class EchoResidueTest(unittest.TestCase):
    def test_navy_strokes_written_during_reply_are_read(self):
        self.assertFalse(monke._echo_only([S] * 15, SAME, SAME))   # run 12 Summarize
        self.assertFalse(monke._echo_only([S] * 22, SAME, SAME))   # run 12 21:11:29

    def test_small_residue_without_page_change_is_echo(self):
        self.assertTrue(monke._echo_only([], SAME, SAME))
        self.assertTrue(monke._echo_only([S], SAME, SAME))
        self.assertTrue(monke._echo_only([S] * 6, SAME, SAME))     # run 10 20:04:40

    def test_small_residue_with_page_change_is_read(self):
        now = SAME.copy()
        now.paste(0, (0, 0, 100, 60))
        self.assertFalse(monke._echo_only([S], SAME, now))


class EchoSpentTest(unittest.TestCase):
    """Sim 2026-10-04 (scroll_right_after_reply): the daemon missed a scroll,
    the echo map kept the reply's old screen spots, and 14 of Navy's next 32
    strokes — written there — were dropped as 'Monke's own ink'. An echo
    comes back once: when it has, the marks are spent."""

    def test_marks_are_spent_once_their_echo_is_back(self):
        reply = [[(100 + 30 * i + k, 300 + 2 * k) for k in range(0, 21, 4)] for i in range(20)]
        monke.echo_reset()
        monke._echo_mark(reply)
        self.assertEqual(monke.drop_echo(list(reply)), [])          # the echo itself
        navy = [[(102 + k, 301 + 2 * k) for k in range(0, 17, 4)]]  # later, on the same screen spot
        self.assertEqual(monke.drop_echo(navy), navy)

    def test_marks_stay_until_the_echo_arrives(self):
        reply = [[(100 + 30 * i + k, 300 + 2 * k) for k in range(0, 21, 4)] for i in range(20)]
        monke.echo_reset()
        monke._echo_mark(reply)
        self.assertEqual(monke.drop_echo([]), [])                   # a commit before the echo
        self.assertEqual(monke.drop_echo(list(reply)), [])


if __name__ == "__main__":
    unittest.main()
