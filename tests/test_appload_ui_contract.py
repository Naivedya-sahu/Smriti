"""Source contract for the deliberately small AppLoad Smriti control surface.

Slim UI (Navy-approved): status line, start/stop + deactivate, brain toggle
(ChatGPT | Claude), movable eye. Everything else is gone, but the
settings.json wire format (same 9 keys) is unchanged.
"""
import ast
from pathlib import Path
import re
import unittest


REPO = Path(__file__).parents[1]
APPLOAD_UI = REPO / "device" / "appload" / "smriti" / "ui" / "smriti.qml"
BRAIN = REPO / "host" / "brain.py"

# settings.json keys the app has always written (host/monke.py apply_settings
# reads profile/prompt/reader/writer/context/escalate/escalate_next; the
# in-notebook eye qmd reads eye; model stays "" = daemon default).
SETTINGS_KEYS = ["model", "profile", "prompt", "reader", "writer", "context",
                 "escalate", "escalate_next", "eye"]


def _source() -> str:
    return APPLOAD_UI.read_text(encoding="utf-8")


def _host_claude_aliases() -> list:
    m = re.search(r"^CLAUDE_ALIASES\s*=\s*(\(.*?\))\s*$", BRAIN.read_text(encoding="utf-8"), re.M)
    assert m, "CLAUDE_ALIASES not found in host/brain.py"
    return list(ast.literal_eval(m.group(1)))


def _save_settings_payload(source: str) -> str:
    m = re.search(r"function saveSettings\(note\) \{.*?JSON\.stringify\(\{(.*?)\}\)\);", source, re.S)
    assert m, "saveSettings payload not found"
    return m.group(1)


def _function_body(source: str, name: str) -> str:
    m = re.search(r"function " + re.escape(name) + r"\([^)]*\) \{(.*?)\n    \}", source, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


class AppLoadUiContractTest(unittest.TestCase):
    # compact failures: never dump the whole QML source into the report
    def assertIn(self, member, container, msg=None):
        if member not in container:
            self.fail(msg or f"{member!r} not found")

    def assertNotIn(self, member, container, msg=None):
        if member in container:
            self.fail(msg or f"{member!r} unexpectedly found")

    def test_slim_controls_are_present(self):
        source = _source()
        for token in (
            'text: "Deactivate Smriti"',
            'root.command(root.watching ? "stop" : "start")',
            'text: "Brain"',
            'text: "Eye placement"',
        ):
            self.assertIn(token, source)

    def test_removed_controls_are_gone(self):
        source = _source()
        for removed in (
            'Reader (sees the page)',
            'Writer (the reply)',
            'text: "Context"',
            'Task type',
            'root.profiles',
            'System prompt',
            'TextEdit',
            'promptEdit',
            'Save prompt',
            'Hide keyboard',
            'Advanced',
            'Study pack',
            'Signals & Systems',
            'root.packs',
            'function setPack',
            'function packKey',
            'root.reader = modelData.v',
            'root.writer = modelData.v',
            'root.context = modelData',
            'root.profile = modelData',
        ):
            self.assertNotIn(removed, source)

    def test_settings_payload_keeps_the_backend_wire_format(self):
        payload = _save_settings_payload(_source())
        keys = re.findall(r"(\w+)\s*:", payload)
        self.assertEqual(keys, SETTINGS_KEYS)
        for token in ('model: ""', 'profile: "auto"', 'prompt: ""',
                      'reader: root.reader, writer: root.writer', 'context: "auto"',
                      'escalate: root.escalate, escalate_next: false, eye: root.eye'):
            self.assertIn(token, payload)

    def test_brain_toggle_writes_the_same_route_to_reader_and_writer(self):
        source = _source()
        body = _function_body(source, "setBrain")
        self.assertIn('var val = v === "claude" ? root.claudeAlias() : "chatgpt";', body)
        self.assertIn('root.reader = val; root.writer = val;', body)
        # Claude is offered only when models.json publishes a Claude alias
        choices = _function_body(source, "brainChoices")
        self.assertIn('v: "chatgpt", label: "ChatGPT"', choices)
        self.assertIn('if (alias !== "")', choices)
        alias = _function_body(source, "claudeAlias")
        self.assertIn('root.claudeRoutes.indexOf(root.cloud[j].id) >= 0', alias)
        for retired_ui_term in ('LM Studio', 'lms:', 'gemini', 'loaded in', 'type === "llm"'):
            self.assertNotIn(retired_ui_term, source)

    def test_claude_routes_match_the_ids_the_host_publishes(self):
        # host/monke.py publish_models() puts bare Claude aliases ("haiku",
        # "sonnet") and "chatgpt:<id>" entries into models.json "cloud"; a
        # "claude"-prefix filter would hide every Claude route.
        source = _source()
        self.assertNotIn('indexOf("claude")', source)
        m = re.search(r"readonly property var claudeRoutes: (\[.*?\])", source)
        self.assertIsNotNone(m, "claudeRoutes list missing")
        self.assertEqual(ast.literal_eval(m.group(1)), _host_claude_aliases())

    def test_untouched_saved_routes_are_preserved(self):
        # "local", "" or "chatgpt:<id>" run on ChatGPT on the host; the raw
        # value is kept (round-tripped) unless the brain toggle is tapped.
        source = _source()
        self.assertIn('function routeKey(v)', source)
        self.assertIn('root.reader = s.reader || "chatgpt";', source)
        self.assertIn('root.writer = s.writer || "chatgpt";', source)
        self.assertIn('var r = root.routeKey(root.reader), w = root.routeKey(root.writer);',
                      _function_body(source, "brainKey"))

    def test_study_prompt_is_cleared_not_hidden_in_the_ui(self):
        source = _source()
        self.assertNotIn("promptText", source)
        self.assertIn('prompt: ""', _save_settings_payload(source))

    def test_status_reads_stage_file_with_30s_staleness(self):
        source = _source()
        self.assertIn('readFile("/tmp/smriti-stage"', source)
        self.assertIn('readonly property int stageMaxAge: 30', source)
        body = _function_body(source, "stageFrom")
        self.assertIn('nowSec - ts > root.stageMaxAge', body)
        self.assertIn('"Monke online \u00b7 " + root.stage', source)
        self.assertIn('"Monke offline (no brain reachable)"', source)
        self.assertIn('interval: 3000', source)
        self.assertIn('running: root.visible', source)

    def test_status_card_matches_notebook_eye_states(self):
        source = _source()
        for token in ('property string eyeState:', 'stageThinking:', 'stageDrawing:',
                      '"Eye closed · idle"', '"Eye open · listening"',
                      '"Thinking…"', '"Drawing reply…"', 'root.statusPulse'):
            self.assertIn(token, source)

    def test_eye_canvas_writes_legacy_names_or_fractions(self):
        source = _source()
        self.assertIn('height: width * 1872 / 1404', source)
        body = _function_body(source, "eyeValue")
        for legacy in ('"bottom-right"', '"bottom-left"', '"top-middle"'):
            self.assertIn(legacy, body)
        self.assertIn('fx.toFixed(3) + "," + fy.toFixed(3)', body)
        self.assertIn('root.eye = root.eyeValue(', _function_body(source, "placeEye"))
        m = re.search(r"readonly property var eyePresets: (\[.*?\])", source, re.S)
        self.assertIsNotNone(m, "eyePresets missing")
        self.assertEqual(len(re.findall(r"\{ l:", m.group(1))), 8)

    def test_deferred_features_have_no_controls(self):
        source = _source()
        for deferred_term in (
            'escalate next turn:',
            'lasso',
            'merged',
            'tailnet',
            # escalation is deferred: the app must never re-arm the one-shot
            # flag the host clears after use (it only reads settings at open)
            'root.escalateNext',
            'property string usage',
        ):
            self.assertNotIn(deferred_term, source)


if __name__ == "__main__":
    unittest.main()
