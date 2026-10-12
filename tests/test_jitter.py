"""Seeded ground-truth generators."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import jitter


class JitterTest(unittest.TestCase):
    def test_new_generators_are_seeded(self):
        calls = [
            lambda: jitter.polygon([(0, 0), (100, 0), (100, 100)], seed=4),
            lambda: jitter.box((100, 100, 500, 500), seed=4),
            lambda: jitter.circuit(seed=4),
        ]
        for call in calls:
            self.assertEqual(call(), call())
            self.assertTrue(call())


if __name__ == "__main__":
    unittest.main()