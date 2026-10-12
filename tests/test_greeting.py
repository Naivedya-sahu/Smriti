"""Greeting is ONE short line, enforced in code (live test 2026-09-30: Gemini
answered a paragraph + a tikz plot, 329 strokes, for 'ONE short line')."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import harness

LIVE = ("Monke here, ready to help.\n\nUse AC/DC module. Set dielectric properties.\n\n"
        "$$\\begin{tikzpicture}\\draw (0,0) -- (4,0);\\end{tikzpicture}$$")


class GreetingLineTest(unittest.TestCase):
    def test_live_reply_cut_to_first_line(self):
        self.assertEqual(harness.greeting_line(LIVE), "Monke here, ready to help.")

    def test_block_first_is_dropped(self):
        self.assertEqual(harness.greeting_line("$$x^2$$\nHello Navy."), "Hello Navy.")

    def test_long_line_capped_at_first_sentence(self):
        self.assertEqual(harness.greeting_line("Hello Navy. The circuit waits for you today."),
                         "Hello Navy.")

    def test_run_on_line_capped_in_words(self):
        line = harness.greeting_line(" ".join(["word"] * 30))
        self.assertLessEqual(len(line.split()), 14)

    def test_nothing_usable(self):
        self.assertEqual(harness.greeting_line("$$\\int x$$"), "")


if __name__ == "__main__":
    unittest.main()
