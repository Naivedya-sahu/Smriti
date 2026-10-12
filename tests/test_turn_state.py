import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

from turn_state import TurnState


class TurnStateTests(unittest.TestCase):
    def test_writes_canonical_stage_atomically(self):
        path = Path(tempfile.mkdtemp()) / "state"
        state = TurnState(path)

        self.assertEqual(state.set("reading"), "reading")
        self.assertEqual(path.read_text(encoding="utf-8"), "reading\n")
        self.assertFalse(list(path.parent.glob(".state.*.tmp")))

    def test_error_is_the_only_stage_that_accepts_a_reason(self):
        state = TurnState(Path(tempfile.mkdtemp()) / "state")

        self.assertEqual(state.set("error", "reader unreachable"),
                         "error reader unreachable")
        self.assertEqual(state.status(), "error reader unreachable")
        with self.assertRaises(ValueError):
            state.set("reading", "not allowed")


if __name__ == "__main__":
    unittest.main()
