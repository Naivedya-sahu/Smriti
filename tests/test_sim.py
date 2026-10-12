"""Four of the virtual-tablet sessions run with the suite; all of them:
PYTHONUTF8=1 uv run python tests/sim.py  (pages + logs: sessions/_runs/sim/)."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim


class SimTest(unittest.TestCase):
    def test_sessions_play_clean(self):
        for f in (sim.basic, sim.scroll_right_after_reply, sim.hash_p, sim.page_fills_up):
            with self.subTest(f.__name__):
                self.assertEqual(sim.Sim(f.__name__).play(f), [])


if __name__ == "__main__":
    unittest.main()
