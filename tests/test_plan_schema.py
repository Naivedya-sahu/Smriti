"""plan_schema: the smriti_plan contract v1 is strict-safe for OpenAI
Structured Outputs, embeds the shared GraphSpec verbatim, and its
canonicalizer turns any model output into render_plan Blocks
deterministically (coerce / trim / clamp / per-block fallback, never raises).
Proven on the canonical series-RLC plan (tests/fixtures/ac_rlc_plan.json)."""
# pyright: reportMissingImports=false

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import plan_schema as ps
import render_plan as rp

FIXTURE = ROOT / "tests" / "fixtures" / "ac_rlc_plan.json"
GRAPHSPEC_KEYS = ["type", "title", "xlabel", "ylabel", "x", "y", "curves", "n", "values",
                  "zeros", "poles", "b", "a", "domain", "scale", "show", "marks"]


def _walk(node, depth=0, path="$"):
    yield node, depth, path
    if isinstance(node, dict):
        for k, v in node.get("properties", {}).items():
            yield from _walk(v, depth + 1, f"{path}.{k}")
        if isinstance(node.get("items"), dict):
            yield from _walk(node["items"], depth + 1, f"{path}[]")
        for i, v in enumerate(node.get("anyOf", [])):
            yield from _walk(v, depth, f"{path}|{i}")


def _plan():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class SchemaStrictTest(unittest.TestCase):
    def test_root_is_a_closed_object_without_anyof(self):
        s = ps.SCHEMA
        self.assertEqual((s["type"], s["additionalProperties"]), ("object", False))
        self.assertNotIn("anyOf", s)
        self.assertEqual(ps.FORMAT, {"type": "json_schema", "name": "smriti_plan",
                                     "strict": True, "schema": ps.SCHEMA})

    def test_every_object_closed_and_every_key_required(self):
        for node, _, path in _walk(ps.SCHEMA):
            if "properties" in node:
                self.assertIs(node.get("additionalProperties"), False, path)
                self.assertEqual(node["required"], list(node["properties"]), path)

    def test_nullables_are_type_lists_and_only_supported_keywords(self):
        allowed = {"type", "properties", "required", "additionalProperties", "items", "enum",
                   "anyOf", "description"}
        for node, depth, path in _walk(ps.SCHEMA):
            self.assertLessEqual(set(node) - allowed, set(), path)
            self.assertLessEqual(depth, 10, path)
            t = node.get("type")
            if isinstance(t, list):
                self.assertEqual(t[-1], "null", path)
                if "enum" in node:
                    self.assertIn(None, node["enum"], path)

    def test_block_variants_and_byte_stable_format(self):
        kinds = [v["properties"]["type"]["enum"][0]
                 for v in ps.SCHEMA["properties"]["blocks"]["items"]["anyOf"]]
        self.assertEqual(sorted(kinds), sorted(ps.TYPES))
        self.assertEqual(json.dumps(ps.FORMAT), json.dumps(ps.FORMAT))   # a constant: server cache
        for v in ps.SCHEMA["properties"]["blocks"]["items"]["anyOf"]:
            self.assertNotIn("color", v["properties"])                    # roles, never pens

    def test_graph_block_embeds_the_shared_graphspec(self):
        graph = next(v for v in ps.SCHEMA["properties"]["blocks"]["items"]["anyOf"]
                     if v["properties"]["type"]["enum"] == ["graph"])
        plot = graph["properties"]["plot"]
        self.assertIs(plot, ps.PLOT)
        self.assertEqual(list(plot["properties"]), GRAPHSPEC_KEYS)
        p = plot["properties"]
        self.assertEqual(p["type"]["enum"], ["function", "stem", "pole_zero", "freq_response"])
        self.assertEqual(p["domain"]["enum"], ["z", "s", None])
        self.assertEqual(p["scale"]["enum"], ["linear", "db", None])
        self.assertEqual(p["show"]["enum"], ["mag", "phase", None])
        self.assertEqual(list(p["curves"]["items"]["properties"]), ["expr", "label"])
        self.assertEqual(list(p["marks"]["items"]["properties"]), ["x", "label"])


class FixtureTest(unittest.TestCase):
    def test_ac_rlc_plan_validates_clean(self):
        blocks, problems = ps.validate(_plan())
        self.assertEqual(problems, [])
        self.assertEqual([(b.kind, b.role) for b in blocks], [
            ("text", "prose"), ("circuit", "circuit"), ("latex", "equation"),
            ("latex", "equation"), ("latex", "highlight"), ("graph", "graph"),
            ("text", "prose"), ("graph", "graph"), ("latex", "answer"), ("text", "answer")])
        g1, g2 = [b for b in blocks if b.kind == "graph"]
        self.assertEqual((g1.label, g2.label), ("G1", "G2"))
        self.assertEqual(list(g1.spec), GRAPHSPEC_KEYS)
        self.assertEqual([c["expr"] for c in g1.spec["curves"]],
                         ["14.14*sin(1000*t)", "10*sin(1000*t - pi/4)"])
        self.assertEqual(g2.spec["marks"], [{"x": 707.1, "label": "omega0"}])
        self.assertEqual(len(g1.curves), 2)
        self.assertEqual([b.color for b in blocks if b.role == "answer"], [7, 7])
        self.assertEqual(blocks[4].color, 6)
        self.assertEqual(ps.header(_plan()), {"place": None, "outline": {
            "text_lines": 3, "figures": ["circuit", "latex", "latex", "latex", "graph", "graph",
                                         "latex"]}})

    def test_resonance_curve_is_right(self):
        g2 = [b for b in ps.validate(_plan())[0] if b.kind == "graph"][1]
        y = rp.sample(g2.spec["curves"][0]["expr"], np.array([707.1068, 1000.0]))
        self.assertAlmostEqual(float(y[0]), 1.0, places=3)        # I_res = 1 A
        self.assertAlmostEqual(float(y[1]), 0.7071, places=3)     # |I| at 1000 rad/s

    def test_deterministic_and_serializable(self):
        a, b = ps.validate(_plan())[0], ps.validate(json.dumps(_plan()))[0]
        self.assertEqual(json.dumps([x.to_dict() for x in a]), json.dumps([x.to_dict() for x in b]))


class CanonicalizeTest(unittest.TestCase):
    def one(self, raw, taken=()):
        blocks, problems = ps.validate({"blocks": [raw]}, taken)
        return blocks, problems

    def test_aliases_case_and_whitespace(self):
        b, _ = self.one({"type": " Equation ", "role": "Final", "tex": "  $$x = 1$$ "})
        self.assertEqual((b[0].kind, b[0].role, b[0].src, b[0].color), ("latex", "answer", "x = 1", 7))
        b, _ = self.one({"type": "PROSE", "role": "nonsense", "text": "  **Hi**  there "})
        self.assertEqual((b[0].kind, b[0].role, b[0].src), ("text", "prose", "Hi  there"))

    def test_boxed_latex_is_an_answer_and_tikz_axis_is_a_graph(self):
        b, _ = self.one({"type": "latex", "role": "body", "tex": r"\boxed{f=159}"})
        self.assertEqual(b[0].role, "answer")
        b, _ = self.one({"type": "latex", "role": "body",
                         "tex": r"\begin{tikzpicture}\begin{axis}\addplot{x};\end{axis}\end{tikzpicture}"})
        self.assertEqual((b[0].kind, b[0].label, len(b[0].curves)), ("graph", "G1", 1))

    def test_circuit_wrapped_or_dropped_never_source(self):
        b, why = self.one({"type": "circuit", "tex": r"\draw (0,0) to[R] (2,0);"})
        self.assertTrue(b[0].src.startswith(r"\begin{circuitikz}"))
        self.assertIn("wrapped in circuitikz", " ".join(why))
        b, why = self.one({"type": "circuit", "tex": "R then L then C"})
        self.assertEqual(b, [])
        self.assertIn("dropped", why[0])

    def test_text_holding_blocks_is_split(self):
        b, why = self.one({"type": "text", "role": "answer", "text": "So:\n$$Z = 10$$"})
        self.assertEqual([(x.kind, x.role) for x in b], [("text", "answer"), ("latex", "answer")])
        self.assertIn("split", " ".join(why))

    def test_messy_graph_is_coerced(self):
        b, why = self.one({"type": "plot", "id": "g 1", "plot": {
            "title": "  wave  ", "x": ["1e-2", "0"], "y": [3, 3],
            "curves": [{"expr": "y = $\\sin(2*pi*t)^2$", "label": ""}] + [{"expr": "t"}] * 5,
            "n": [0.2, 4.7], "domain": "Z", "scale": "decibel", "show": "Magnitude",
            "marks": [{"x": "0.005", "label": "half"}, {"x": "nan"}], "extra": 1}})
        s = b[0].spec
        self.assertEqual(list(s), GRAPHSPEC_KEYS)
        self.assertEqual((s["type"], s["title"], s["x"], s["y"]), ("function", "wave", [0.0, 0.01], None))
        self.assertEqual(s["curves"][0], {"expr": "sin(2*pi*t)**2", "label": None})
        self.assertEqual(len(s["curves"]), ps.MAX_CURVES)
        self.assertEqual((s["n"], s["domain"], s["scale"], s["show"]), ([0, 5], "z", "db", "mag"))
        self.assertEqual(s["marks"], [{"x": 0.005, "label": "half"}])
        self.assertEqual(b[0].label, "G1")
        self.assertTrue(any("-> function" in w for w in why))

    def test_freq_response_defaults_a_and_stem_needs_data(self):
        b, _ = self.one({"type": "graph", "id": None, "plot": {"type": "bode", "b": [1, 1]}})
        self.assertEqual((b[0].spec["type"], b[0].spec["a"]), ("freq_response", [1.0]))
        b, why = self.one({"type": "graph", "id": None, "plot": {"type": "stem"}})
        self.assertEqual(b, [])
        self.assertIn("dropped", why[-1])

    def test_graph_without_its_data_falls_back_to_prose(self):
        b, why = self.one({"type": "graph", "id": "G7", "plot": {
            "type": "pole_zero", "curves": [{"expr": "exp(-t)", "label": None}]}})
        self.assertEqual((b[0].kind, b[0].src), ("text", "G7: exp(-t)"))
        self.assertIn("as prose", why[-1])

    def test_graph_labels_are_session_unique(self):
        g = {"type": "graph", "id": "G1", "plot": {"type": "function",
                                                  "curves": [{"expr": "t", "label": None}]}}
        blocks, _ = ps.validate({"blocks": [g, g, dict(g, id="G9")]}, taken={"G1"})
        self.assertEqual([b.label for b in blocks], ["G2", "G3", "G9"])

    def test_diagram_from_steps_or_svg(self):
        b, _ = self.one({"type": "diagram", "title": "DSP", "steps": ["ADC", "FIR", "DAC"], "svg": None})
        self.assertTrue(b[0].src.startswith("<svg") and "FIR" in b[0].src)
        svg = '<svg viewBox="0 0 10 10"><line x1="0" y1="0" x2="9" y2="9"/></svg>'
        b, _ = self.one({"type": "diagram", "title": None, "steps": None, "svg": "x " + svg})
        self.assertEqual(b[0].src, svg)

    def test_never_raises_on_garbage(self):
        for junk in (None, "", "not json", "{", 3, [], {}, {"blocks": "x"}, [1, None, {}, "hi"],
                     {"blocks": [{"type": "graph", "plot": {"type": "function",
                                                            "curves": [{"expr": None}]}}]},
                     {"blocks": [{"type": "latex", "tex": {"a": 1}}, {"type": "text", "text": 5}]},
                     {"v": 9, "blocks": [{"type": "graph", "plot": "sin"}]}):
            blocks, problems = ps.validate(junk)
            self.assertIsInstance(blocks, list)
            self.assertIsInstance(problems, list)
        blocks, problems = ps.validate([1, "hi"])
        self.assertEqual([b.src for b in blocks], ["hi"])            # a bare number is no block
        self.assertIn("not an object -> dropped", problems[0])

    def test_block_cap(self):
        blocks, problems = ps.validate({"blocks": [{"type": "text", "text": f"l{i}"}
                                                   for i in range(40)]})
        self.assertEqual(len(blocks), ps.MAX_BLOCKS)
        self.assertIn("first 16", problems[0])


class TextFallbackTest(unittest.TestCase):
    def test_plan_to_text_grammar(self):
        raw = json.dumps(dict(_plan(), place="below N2"))
        text = ps.as_text(raw)
        lines = text.split("\n")
        self.assertEqual(lines[0], "@place below N2")
        self.assertTrue(lines[2].startswith("$$\\begin{circuitikz}"))
        self.assertEqual(text.count("\\begin{axis}"), 2)                 # both graphs: pgfplots
        blocks = rp.normalize(text)
        self.assertEqual([b.kind for b in blocks].count("graph"), 2)
        self.assertEqual([b.kind for b in blocks].count("circuit"), 1)

    def test_text_replies_pass_through(self):
        for t in ("Ohm's law.\n$$V=IR$$", '{"steps": ["a", "b"]}', "", "@place below N1\nHi"):
            self.assertEqual(ps.as_text(t), t)
        self.assertFalse(ps.is_plan("Ohm.\n{x}"))
        self.assertTrue(ps.is_plan('```json\n{"v":1'))


class GraphExecutorTest(unittest.TestCase):
    def test_sample_whitelist_and_signal_functions(self):
        t = np.array([-1.0, -0.25, 0.0, 0.25, 1.0])
        self.assertEqual(list(rp.sample("u(t)", t)), [0, 0, 1, 1, 1])
        self.assertEqual(list(rp.sample("rect(t)", t)), [0, 1, 1, 1, 0])
        self.assertEqual(list(rp.sample("tri(t)", t)), [0, 0.75, 1, 0.75, 0])
        self.assertAlmostEqual(float(rp.sample("sinc(t)", t)[2]), 1.0)
        for bad in ("__import__('os')", "x.real", "open(1)", "().__class__", "t[0]", "lambda: 1"):
            with self.assertRaises(ValueError):
                rp.sample(bad, t)

    def test_graph_tex_for_every_type_restyles_curves(self):
        blocks = ps.validate(_plan())[0]
        g1 = [b for b in blocks if b.kind == "graph"][0]
        src, curves, why = rp._restyle(rp.graph_tex(g1.spec, "G1"), "G1")
        self.assertEqual((len(curves), why), (2, []))
        self.assertIn("title={G1: v\\_s(t) and i(t)}", src)
        for spec in ({"type": "stem", "values": [1, 2, 3], "n": [0, 2]},
                     {"type": "pole_zero", "zeros": [[0, 0]], "poles": [[0.5, 0.5]], "domain": "z"},
                     {"type": "freq_response", "b": [1, 1], "a": [1], "domain": "z", "scale": "db"},
                     {"type": "freq_response", "b": [1], "a": [1, 1], "domain": "s", "show": "phase"}):
            full, _ = ps.plot(spec)
            self.assertIn("\\begin{axis}", rp.graph_tex(full, "G3"), spec["type"])

    def test_plots_dispatch_validate_render_and_fallback(self):
        g1 = [b for b in ps.validate(_plan())[0] if b.kind == "graph"][0]
        seen = []
        fake = types.ModuleType("plots")
        fake.validate = lambda spec: seen.append(spec) or None
        fake.render = lambda spec, label=None: types.SimpleNamespace(label="", curves=["a", "b"])
        with patch.dict(sys.modules, plots=fake):
            g, why = rp.plot_graph(g1)
        self.assertEqual((g.label, why, seen), ("G1", "", [g1.spec]))
        fake.validate = lambda spec: "unknown function"
        with patch.dict(sys.modules, plots=fake):
            g, why = rp.plot_graph(g1)
        self.assertEqual((g, why), (None, "plots.validate: unknown function"))
        with patch.dict(sys.modules, plots=None):                    # import fails
            self.assertEqual(rp.plot_graph(g1), (None, "plots.py not available"))

    def test_graph_batches_one_pen_per_curve(self):
        class G:
            curves = ["a", "b", "c"]

            def color(self, i):
                return [0, 7, 6][i]
        calls = []

        def fn(g, only_new=False):
            calls.append((tuple(g.curves), only_new))
            return [[(len(g.curves), 0), (len(g.curves), 1)]]
        out = rp.graph_batches(G(), strokes_fn=fn)
        self.assertEqual([pen for _, pen in out], [0, 7, 6])
        self.assertEqual(calls, [(("a",), False), (("a", "b"), True), (("a", "b", "c"), True)])

        def broken(g, only_new=False):
            if only_new:
                raise RuntimeError("overlay bbox moved")
            return [[(0, 0), (1, 1)]]
        self.assertEqual(rp.graph_batches(G(), strokes_fn=broken), [([[(0, 0), (1, 1)]], 0)])


if __name__ == "__main__":
    unittest.main()
