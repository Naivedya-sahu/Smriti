"""Static source contract for the deployed (known-good) Smriti eye QMD.

The deployed file evaluates command JS directly in the QML component scope,
where bare ``sceneController`` already resolves to the live SceneController
(confirmed by an armed probe on the original file). The experimental
``smritiSceneController()`` parent-chain resolver and the per-command
``function(sceneController){...}`` wrapper were never validated on-device and
must not come back: the wrapper shadows the scope-resolved ``sceneController``.
"""
from pathlib import Path
import re
import unittest


QMD = Path(__file__).parents[1] / "device" / "xovi" / "smriti-eye.qmd"


def _source():
    return QMD.read_bytes().decode("utf-8")


def _function_body(source, name):
    match = re.search(
        r"function\s+" + re.escape(name) + r"\s*\(\s*\w*\s*\)\s*\{(?P<body>.*?)^\t{3}\}",
        source,
        re.DOTALL | re.MULTILINE,
    )
    return match.group("body") if match else None


class SmritiEyeQmdContractTest(unittest.TestCase):
    def test_affect_envelope_is_valid(self):
        source = _source()
        self.assertRegex(source, r"\AAFFECT \[\[\d+\]\]\n")
        self.assertTrue(source.endswith("END AFFECT\n"))
        self.assertEqual(source.count("AFFECT [["), 1)
        self.assertEqual(source.count("END AFFECT"), 1)

    def test_uses_lf_line_endings(self):
        self.assertNotIn(b"\r", QMD.read_bytes())

    def test_command_eval_runs_c_js_in_scope_with_bare_scene_controller(self):
        source = _source()
        body = _function_body(source, "smritiRun")
        self.assertIsNotNone(body, "smritiRun(c) command handler is missing")
        self.assertRegex(
            body,
            r'if\s*\(\s*c\.cmd\s*===\s*"eval"\s*\)\s*\{\s*'
            r"out\s*=\s*String\(\s*eval\(\s*c\.js\s*\)\s*\)\s*;",
        )
        self.assertRegex(body, r'c\.cmd\s*===\s*"probe"')
        # bare sceneController must stay reachable: no shadowing parameter.
        self.assertNotRegex(source, r"function\s*\(\s*sceneController\s*\)")
        self.assertRegex(
            _function_body(source, "smritiProbe") or "",
            r'"sceneController"',
        )

    def test_command_dispatch_dedupes_by_id_then_runs(self):
        body = _function_body(_source(), "smritiCmdDone")
        self.assertIsNotNone(body, "smritiCmdDone() is missing")
        self.assertRegex(body, r"String\(c\.id\)\s*===\s*smritiEyeUi\.cmdId")
        self.assertRegex(body, r"smritiEyeUi\.smritiRun\(c\)")

    def test_no_experimental_scene_controller_resolver(self):
        self.assertNotIn("smritiSceneController", _source())

    def test_eye_position_accepts_legacy_names_and_xy_fractions(self):
        # settings.json "eye": legacy "bottom-right" | "bottom-left" |
        # "top-middle", or "x,y" normalized fractions (0..1) from the app.
        source = _source()
        body = _function_body(source, "smritiPosDone")
        self.assertIsNotNone(body, "smritiPosDone() is missing")
        self.assertIn('p === "bottom-left") f = [0, 1];', body)
        self.assertIn('p === "top-middle") f = [0.5, 0];', body)
        self.assertIn("var m = p.split(\",\");", body)
        self.assertIn("smritiEyeUi.fx = Math.max(0, Math.min(1, f[0]));", body)
        self.assertIn("smritiEyeUi.fy = Math.max(0, Math.min(1, f[1]));", body)
        # fractions map onto the free page area -> the eye stays on screen
        self.assertIn("x: 18 + smritiEyeUi.fx * Math.max(0, smritiEyeUi.pw - smritiEyeUi.width - 36)", source)
        self.assertIn("y: 18 + smritiEyeUi.fy * Math.max(0, smritiEyeUi.ph - smritiEyeUi.height - 36)", source)
        self.assertNotIn("anchors.right: (smritiEyeUi.atTop", source)

    def test_eye_visuals_follow_live_daemon_stage(self):
        source = _source()
        for token in ('property string stage: ""', 'property int pulse: 0',
                      'smritiStageXhr', '"/tmp/smriti-stage"',
                      'function smritiStageDone()', 'stage === "thinking"',
                      'stage === "inking"', 'smritiEyeUi.closed',
                      'smritiEyeUi.open', 'smritiEyeUi.thinking',
                      'smritiEyeUi.drawing'):
            self.assertIn(token, source)
        self.assertIn('interval: 280', source)       # dots visibly pulse
        self.assertIn('file.charAt(0) === "/"', source)  # /tmp is absolute


if __name__ == "__main__":
    unittest.main()