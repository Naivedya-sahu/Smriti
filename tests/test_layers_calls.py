"""QML round trips per ink (device 2026-09-29: 14 calls x 0.48 s = 6.7 s of an
11.6 s greeting). Entering an existing layer + restoring must cost <= 3."""
# pyright: reportMissingImports=false

import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import layers


class FakeTablet:
    def __init__(self):
        self.st = {"current": 0, "count": 2, "names": ["Layer 1", "Monke"],
                   "visible": [True, True], "color": 7, "thickness": 2, "tool": 17}
        self.calls = 0

    def q(self, host, js):
        self.calls += 1
        for i in re.findall(r"setCurrentLayer\((\d+)\)", js):
            self.st["current"] = int(i)
        for k, v in re.findall(r"activePen\.(\w+) = (\d+)", js):
            self.st[k] = int(v)
        return json.dumps(self.st) if "JSON.stringify" in js else "1"


class OnLayerCallsTest(unittest.TestCase):
    def test_existing_layer_enter_and_restore_in_three_calls(self):
        t = FakeTablet()
        with patch.object(layers, "_q", t.q):
            with layers.on_layer("h", color=0):
                self.assertEqual((t.st["current"], t.st["color"]), (1, 0))
        self.assertEqual({k: t.st[k] for k in ("current", "color", "thickness", "tool")},
                         {"current": 0, "color": 7, "thickness": 2, "tool": 17})
        self.assertLessEqual(t.calls, 3)


if __name__ == "__main__":
    unittest.main()
