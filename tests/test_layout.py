"""Short inline maths stays in the sentence (device 2026-09-30: $$V_{th}$$ on
its own line filled a page; run 6 turn 03 shrank to 0.42x, illegible)."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import monke


class InlineMathTest(unittest.TestCase):
    def test_symbol_block_mid_sentence_is_prose(self):
        segs = monke._segments("$$V_{th}$$ = open-circuit voltage.")
        self.assertEqual([k for k, _ in segs], ["prose"])
        self.assertIn("Vth = open-circuit voltage.", segs[0][1])

    def test_simple_fraction_mid_sentence_is_prose(self):
        segs = monke._segments(r"Cutoff: signal drops to $$ \frac{1}{\sqrt{2}} $$ of input.")
        self.assertEqual([k for k, _ in segs], ["prose"])
        self.assertIn("1/sqrt(2) of input", segs[0][1])

    def test_equation_on_its_own_line_stays_typeset(self):
        segs = monke._segments("Result:\n$$V_{out} = 12 \times \frac{6}{8} = 9\,V$$")
        self.assertEqual([k for k, _ in segs if _.strip()], ["prose", "tex"])

    def test_circuit_block_never_inlined(self):
        c = r"See $$\begin{circuitikz}\draw (0,0) to[R] (2,0);\end{circuitikz}$$ here"
        self.assertIn("tex", [k for k, _ in monke._segments(c)])


if __name__ == "__main__":
    unittest.main()
