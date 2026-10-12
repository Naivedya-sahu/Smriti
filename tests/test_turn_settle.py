import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

from turn_state import SettleGate


class SettleGateTests(unittest.TestCase):
    def test_requires_idle_then_full_pen_up_settle_period(self):
        gate = SettleGate(idle_seconds=2.8, settle_seconds=3.0)
        gate.note_event(0.0)

        self.assertFalse(gate.poll(2.79, stroke_active=False))
        self.assertFalse(gate.poll(2.8, stroke_active=False))
        self.assertFalse(gate.poll(5.79, stroke_active=False))
        self.assertTrue(gate.poll(5.8, stroke_active=False))

    def test_new_stroke_during_settling_restarts_the_deadline(self):
        gate = SettleGate(idle_seconds=2.8, settle_seconds=3.0)
        gate.note_event(0.0)
        gate.poll(2.8, stroke_active=False)

        gate.note_event(4.0)
        self.assertFalse(gate.poll(6.8, stroke_active=False))
        self.assertFalse(gate.poll(9.79, stroke_active=False))
        self.assertTrue(gate.poll(9.8, stroke_active=False))

    def test_default_settle_window_is_configured(self):
        config = Path(__file__).resolve().parents[1] / "config.toml"
        with config.open("rb") as f:
            capture = tomllib.load(f)["capture"]
        self.assertEqual(capture["idle_seconds"], 2.8)
        self.assertEqual(capture["settle_seconds"], 3.0)


if __name__ == "__main__":
    unittest.main()
