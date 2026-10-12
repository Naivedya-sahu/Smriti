"""plots.py — deterministic Signals & Systems / DSP plot executors.

The model only supplies a GraphSpec (parameters); the host validates it,
computes the samples and draws vector strokes directly (no pdflatex), so a
graph inks in milliseconds and is registered like any other Graph."""
# pyright: reportMissingImports=false

import json
import math
import re
import sys
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import graph
import plots

RECT_SINC = {"type": "function", "title": "rect and sinc", "xlabel": "t", "ylabel": "x(t)",
             "x": [-3, 3], "curves": [{"expr": "rect(t)", "label": "rect(t)"},
                                      {"expr": "sinc(t)", "label": "sinc(t)"}]}
STEM = {"type": "stem", "title": "x[n] = 0.8^n u[n]", "xlabel": "n", "n": [-3, 12],
        "curves": [{"expr": "0.8**n*u(n)", "label": None}]}
PZ = {"type": "pole_zero", "title": "H(z)", "domain": "z", "b": [1, 0], "a": [1, -0.9]}
MA = {"type": "freq_response", "title": "3-point moving average", "domain": "z",
      "b": [1 / 3, 1 / 3, 1 / 3], "a": [1], "scale": "linear", "show": "mag",
      "marks": [{"x": 2 * math.pi / 3, "label": "2π/3"}]}
RC = {"type": "freq_response", "title": "RC low-pass", "domain": "s", "b": [1],
      "a": [1e-3, 1], "scale": "db", "show": "mag",
      "marks": [{"x": 1000, "label": "ω_c"}]}
SPECS = [RECT_SINC, STEM, PZ, MA, RC]


def pts(st):
    return [tuple(map(tuple, s)) for s in st]


def inside(st, box):
    x0, y0, x1, y1 = box
    return all(x0 <= x <= x1 and y0 <= y <= y1 for s in st for x, y in s)


class ValidateTest(unittest.TestCase):
    def assertRejected(self, spec, word):
        why = plots.validate(spec)
        self.assertIsInstance(why, str, spec)
        self.assertRegex(why.lower(), word, spec)

    def test_good_specs_pass(self):
        for spec in SPECS:
            self.assertIsNone(plots.validate(spec), spec)

    def test_skill_examples_validate(self):
        md = (ROOT / "skill/smriti/kinds/graph.md").read_text(encoding="utf-8")
        found = re.findall(r'^\s*(\{"type".*\})\s*$', md, re.M)
        self.assertGreaterEqual(len(found), 6)
        for line in found:
            self.assertIsNone(plots.validate(json.loads(line)), line)

    def test_unknown_function(self):
        self.assertRejected({"type": "function", "curves": [{"expr": "foo(t)"}]}, "foo")

    def test_comparison_is_a_zero_one_indicator(self):
        # live 2026-10-10: model wrote rect as (abs(x)<=1); it was rejected and
        # fell back to slow pgfplots. A comparison of whitelisted operands is
        # a 0/1 indicator; chained bounds too.
        import numpy as np
        for expr in ("(abs(x)<=1)", "(x>=-1)*(x<=1)", "-1<=x<=1"):
            spec = {"type": "function", "x": [-2, 2], "curves": [{"expr": expr}]}
            self.assertIsNone(plots.validate(spec), expr)
        f = plots.compile_expr("(abs(x)<=1)")
        self.assertEqual(list(f(np.array([-2.0, -1.0, 0.0, 1.0, 1.5]))), [0, 1, 1, 1, 0])
        # comparisons never open a door: operands stay whitelisted
        self.assertRejected({"type": "function", "curves": [{"expr": "x<__import__('os')"}]},
                            "not allowed|unknown")
        self.assertRejected({"type": "function", "curves": [{"expr": "x in (1, 2)"}]},
                            "not allowed")

    def test_code_injection(self):
        for bad in ["__import__('os').system('x')", "t.real", "().__class__",
                    "[c for c in ()]", "lambda: 1", "open('f')", "sin(t, k=1)", "'a'*3"]:
            self.assertRejected({"type": "function", "curves": [{"expr": bad}]}, r"\w")
        self.assertRejected({"type": "function", "curves": [{"expr": "__import__('os')"}]},
                            "__import__")

    def test_huge_ranges(self):
        self.assertRejected({"type": "function", "x": [-1e9, 1e9],
                             "curves": [{"expr": "t"}]}, "range")
        self.assertRejected({"type": "function", "x": [2, 1], "curves": [{"expr": "t"}]}, "range")
        self.assertRejected({"type": "stem", "n": [0, 100000], "curves": [{"expr": "n"}]}, "n")
        self.assertRejected({"type": "stem", "values": list(range(5000))}, "values")

    def test_shape_errors_never_raise(self):
        for spec in [None, [], "plot", {}, {"type": "pie"}, {"type": 3},
                     {"type": "function"}, {"type": "function", "curves": [{"expr": "t"}] * 5},
                     {"type": "freq_response"}, {"type": "freq_response", "b": [1], "a": [0, 1]},
                     {"type": "function", "curves": [{"expr": "t"}], "x": [float("nan"), 1]},
                     {"type": "pole_zero"}, {"type": "stem", "values": ["a"]},
                     {"type": "function", "curves": [{"expr": "sin(delta(t))"}]},
                     {"type": "function", "curves": [{"expr": "t"}], "title": "x" * 300}]:
            self.assertIsInstance(plots.validate(spec), str, spec)
        with self.assertRaises(ValueError):
            plots.render({"type": "pie"})


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for spec in SPECS:                       # glyph cache warm-up (first use only)
            graph.strokes(plots.render(spec))

    def test_each_type_deterministic_inside_box(self):
        for spec in SPECS:
            g1, g2 = plots.render(spec, "G7"), plots.render(json.loads(json.dumps(spec)), "G7")
            s1, s2 = graph.strokes(g1), graph.strokes(g2)
            self.assertGreater(len(s1), 10, spec["type"])
            self.assertEqual(pts(s1), pts(s2), spec["type"])
            self.assertEqual([s.color for s in s1], [s.color for s in s2])
            self.assertTrue(inside(s1, plots.box(g1)), spec["type"])
            g1.x, g1.y, g1.max_w = 300, 900, 400          # placed + shrunk like monke does
            s3 = graph.strokes(g1)
            self.assertTrue(inside(s3, plots.box(g1)), spec["type"])
            self.assertLessEqual(plots.box(g1)[2] - plots.box(g1)[0], 401)

    def test_graph_registers_like_old_graphs(self):
        g = plots.render(RECT_SINC, "G2")
        self.assertIsInstance(g, graph.Graph)
        self.assertEqual((g.label, g.kind), ("G2", "function"))
        self.assertEqual(g.curves, ["rect(t)", "sinc(t)"])
        self.assertEqual((g.xmin, g.xmax), (-3, 3))
        self.assertEqual(graph.Graph.new("x", 0, 1).kind, "tex")      # old path untouched

    def test_overlay_adds_only_the_new_curve_on_fixed_axes(self):
        g = plots.render({"type": "function", "x": [0, 5], "curves": [{"expr": "exp(-t)"}]}, "G1")
        before = pts(graph.strokes(g))
        lim = (g.xmin, g.xmax, g.ymin, g.ymax)
        g.curves.append("exp(-2*t)")                     # what monke._plot does for 'add ... to G1'
        new = graph.strokes(g, only_new=True)
        self.assertTrue(new)
        self.assertEqual({s.color for s in new if s.part == "curve1"}, {g.color(1)})
        self.assertEqual(lim, (g.xmin, g.xmax, g.ymin, g.ymax))
        full = pts(graph.strokes(g))
        self.assertTrue(set(before) <= set(full))
        self.assertTrue(set(pts(new)) <= set(full))

    def test_curve_styles_distinct(self):
        styles = [plots.curve_style(i) for i in range(3)]
        self.assertEqual(len(set(styles)), 3)
        self.assertEqual(len({p for p, _ in styles}), 3)
        self.assertEqual(len({d for _, d in styles}), 3)          # e-ink: dash differs too
        g = plots.render({"type": "function", "x": [0, 6], "curves": [
            {"expr": "sin(t)", "label": "a"}, {"expr": "cos(t)", "label": "b"},
            {"expr": "sin(2*t)/2", "label": "c"}]})
        st = graph.strokes(g)
        for i in range(3):
            mine = [s for s in st if s.part == f"curve{i}"]
            self.assertTrue(mine)
            self.assertEqual({s.color for s in mine}, {styles[i][0]})
            self.assertTrue([s for s in st if s.part == f"legend{i}"])
        counts = [len([s for s in st if s.part == f"curve{i}"]) for i in range(3)]
        self.assertEqual(counts[0], 1)                    # solid: one stroke
        self.assertGreater(counts[1], 5)                  # dashed: many
        self.assertNotEqual(counts[1], counts[2])

    def test_rect_has_vertical_edges_not_smears(self):
        g = plots.render({"type": "function", "x": [-2, 2], "y": [-0.5, 1.5],
                          "curves": [{"expr": "rect(t)"}]})
        (curve,) = [s for s in graph.strokes(g) if s.part == "curve0"]
        slanted = [(a, b) for a, b in zip(curve, curve[1:])
                   if abs(b[1] - a[1]) > 3 and abs(b[0] - a[0]) > 1]
        self.assertEqual(slanted, [])

    def test_delta_impulse_drawn(self):
        g = plots.render({"type": "function", "x": [-1, 3], "curves": [{"expr": "2*delta(t-1)"}]})
        self.assertTrue([s for s in graph.strokes(g) if s.part == "impulse0"])

    def test_timing_under_budget(self):
        for spec in SPECS:
            t = time.perf_counter()
            st = graph.strokes(plots.render(spec))
            ms = (time.perf_counter() - t) * 1000
            self.assertTrue(st)
            self.assertLess(ms, 150, f"{spec['type']} took {ms:.0f} ms")


class NumbersTest(unittest.TestCase):
    def test_moving_average(self):
        h = plots.freq_response([1 / 3, 1 / 3, 1 / 3], [1], np.array([0.0, 2 * np.pi / 3]), "z")
        self.assertAlmostEqual(abs(h[0]), 1.0, places=9)
        self.assertLess(abs(h[1]), 1e-9)

    def test_rc_lowpass_minus_3db_at_cutoff(self):
        h = plots.freq_response([1], [1e-3, 1], np.array([1000.0]), "s")
        self.assertAlmostEqual(20 * math.log10(abs(h[0])), -3.0103, places=3)

    def test_pole_zero_from_coefficients(self):
        zeros, poles = plots.zeros_poles(PZ)
        self.assertTrue(np.allclose(sorted(poles, key=abs), [0.9]))
        self.assertTrue(np.allclose(zeros, [0.0]))
        z2, p2 = plots.zeros_poles({"type": "pole_zero", "domain": "z", "b": [1 / 3] * 3, "a": [1]})
        self.assertTrue(np.allclose(np.abs(z2), 1.0) and np.allclose(p2, 0))

    def test_safe_eval_signals(self):
        t = np.array([-1.0, -0.25, 0.0, 0.25, 1.0])
        self.assertEqual(list(plots.evaluate("u(t)", t)), [0, 0, 1, 1, 1])
        self.assertEqual(list(plots.evaluate("rect(t)", t)), [0, 1, 1, 1, 0])
        self.assertTrue(np.allclose(plots.evaluate("tri(t)", t), [0, .75, 1, .75, 0]))
        self.assertTrue(np.allclose(plots.evaluate("sinc(t)", t), np.sinc(t)))
        self.assertTrue(np.allclose(plots.evaluate("x^2 + 2pi", t), t ** 2 + 2 * np.pi))
        n = np.arange(-2, 3.0)
        self.assertEqual(list(plots.evaluate("delta(n-1)", n, discrete=True)), [0, 0, 0, 1, 0])


if __name__ == "__main__":
    unittest.main()
