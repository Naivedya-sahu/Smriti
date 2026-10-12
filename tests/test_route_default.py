"""2026-10-02: USB link down at session start -> no app choice known ->
route fell back to the wrong model. 2026-10-10 (brain): the default is the
[brain] model (ChatGPT via Hermes), never local; a claude alias or a
chatgpt:<id> in the app wins."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import brain
import monke

LUNA = brain.default_spec({})     # whatever [brain] ships as default; never local


class RouteDefaultTest(unittest.TestCase):
    def setUp(self):
        monke.CLAUDE_CFG.update(models=["sonnet", "haiku", "opus"])

    def test_no_app_choice_routes_to_the_brain_default(self):
        route = monke.apply_settings({}, {})[2]
        self.assertEqual((route["reader"], route["writer"]), (LUNA, LUNA))

    def test_explicit_choice_is_kept(self):
        route = monke.apply_settings({}, {"reader": "haiku", "writer": "chatgpt:gpt-5.5"})[2]
        self.assertEqual((route["reader"], route["writer"]), ("haiku", "chatgpt:gpt-5.5"))

    def test_old_local_value_falls_back_to_the_default(self):
        route = monke.apply_settings({}, {"reader": "local", "writer": "lms:foo"})[2]
        self.assertEqual((route["reader"], route["writer"]), (LUNA, LUNA))


if __name__ == "__main__":
    unittest.main()
