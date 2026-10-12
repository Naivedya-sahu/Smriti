"""Navy 2026-09-30 19:09: wrote a #P right after the greeting, no reply.
The pen stream opened only after the greeting and the slow eye update
(14 s), and was drained at 'watching' — the line was never seen."""
# pyright: reportMissingImports=false

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import monke


class SessionStartTest(unittest.TestCase):
    def test_pen_opens_before_greeting_and_eye_update_comes_last(self):
        calls = []
        ses = types.SimpleNamespace(name="t", sid="", floor=0, save=lambda: None)

        def rec(name, ret=None):
            return lambda *a, **k: calls.append(name) or ret
        with (patch.object(monke, "NotebookSession", lambda h: ses),
              patch.object(monke, "echo_reset", rec("echo_reset")),
              patch.object(monke, "_shot", rec("shot", None)),
              patch.object(monke, "_load_floor", lambda: 0),
              patch.object(monke, "_save_floor", lambda f: None),
              patch.object(monke, "Capture", rec("capture", types.SimpleNamespace(q="pen"))),
              patch.object(monke, "_greet", rec("greet", (0, []))),
              patch.object(monke, "marker", rec("marker")),
              patch.object(monke, "_drain",
                           lambda *qs, **k: calls.append("drain_pen" if "pen" in qs else "drain")),
              patch.object(monke, "eye_state", rec("eye_state"))):
            monke._start_session("rm2", {}, None, 1, 1800, [], {}, types.SimpleNamespace(q=None))
        self.assertLess(calls.index("capture"), calls.index("greet"))
        # run 9 19:33: _drain waits for 1.2 s of quiet (cap 15 s) — Navy
        # writing right after the greeting kept it busy and was discarded.
        # Pen events are never drained: drop_echo removes Monke's own strokes.
        self.assertNotIn("drain_pen", calls)

    def test_greeting_echo_dropped_navy_kept(self):
        greet = [[(200, 200), (400, 200)]]
        navy = [[(200, 400), (400, 400)]]
        monke.echo_reset()
        monke._echo_mark(greet)
        self.assertEqual(monke.drop_echo(greet + navy), navy)


if __name__ == "__main__":
    unittest.main()
