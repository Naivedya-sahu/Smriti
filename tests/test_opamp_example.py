"""2026-10-02: op-amp drawn with guessed coordinates (wires off the pins).
The writer prompt now carries an anchor-wired example — it must compile."""
# pyright: reportMissingImports=false

import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import monke


class OpAmpExampleTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pdflatex") or list(Path.home().glob(".TinyTeX/bin/*/pdflatex")),
                         "no pdflatex")
    def test_prompt_example_compiles(self):
        src = next(v for v in vars(monke).values() if isinstance(v, str) and "Op-amps: place" in v)
        end = r"node[right]{$v_{out}$};"
        body = src[src.index(r"\draw (0,0) node[op amp]"):src.index(end) + len(end)]
        self.assertGreater(len(monke._drawing("tex", r"\begin{circuitikz}" + body + r"\end{circuitikz}")), 30)


if __name__ == "__main__":
    unittest.main()
