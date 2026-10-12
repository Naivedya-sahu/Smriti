"""Pure geometry contracts for handwriting beautification."""
import sys
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import beautify
import jitter


class BeautifyTest(unittest.TestCase):
    def test_core_fits_and_input_is_unchanged(self):
        line = jitter.line((100, 100), (500, 300), seed=1)
        circle = jitter.circle((700, 500), 150, seed=2)
        original = [list(s) for s in (line, circle)]
        fit = beautify.fit_line(beautify.resample(line, 3))
        self.assertIsNotNone(fit)
        self.assertLess(fit.err, .03)
        c = beautify.fit_circle(beautify.resample(circle, 3))
        self.assertIsNotNone(c)
        self.assertAlmostEqual(c.pts[0, 0], 700, delta=8)
        self.assertEqual([line, circle], original)

    def test_graph_axes_and_curve_are_explained(self):
        strokes = jitter.graph("poly2", seed=4)
        scene = beautify.beautify(strokes, "graph")
        self.assertIsNotNone(scene)
        self.assertGreaterEqual(scene.explained, .6)
        self.assertEqual(scene.kind, "graph")
        self.assertTrue(any(p.kind == "line" for p in scene.prims))
        self.assertTrue(scene.strokes())

    def test_one_stroke_polygons_split_at_corners(self):
        cases = [
            ([(100, 100), (500, 100), (500, 350), (100, 350), (100, 100)], 4),
            ([(100, 100), (500, 100), (300, 400), (100, 100)], 3),
            ([(100, 100), (500, 100), (500, 400)], 2),
        ]
        for seed, (truth, count) in enumerate(cases):
            scene = beautify.beautify([jitter.polygon(truth, seed=seed)], "diagram")
            lines = [p for p in scene.prims if p.kind == "line"]
            self.assertEqual(len(lines), count)
            if count == 4:
                corners = np.vstack([p.pts for p in lines])
                for expected in truth[:-1]:
                    self.assertLess(np.min(np.linalg.norm(corners - expected, axis=1)), 6)
                angles = {round(abs(np.degrees(np.arctan2(*(p.pts[1] - p.pts[0])[::-1]))) % 180) for p in lines}
                self.assertEqual(angles, {0, 90})

    def test_closed_circle_and_ellipse_pipeline(self):
        for stroke, kind, center, axes in [
            (jitter.circle((400, 500), 130, seed=31), "circle", (400, 500), (130, 130)),
            (jitter.ellipse((700, 600), (180, 90), seed=32), "ellipse", (700, 600), (180, 90)),
        ]:
            scene = beautify.beautify([stroke], "diagram")
            prim = next(p for p in scene.prims if p.kind == kind)
            self.assertLess(np.linalg.norm(prim.pts[0, :2] - center), 5)
            got = sorted(prim.pts[0, 2:4] if kind == "ellipse" else [prim.pts[0, 2]] * 2, reverse=True)
            self.assertTrue(all(abs(a - b) / b < .06 for a, b in zip(got, sorted(axes, reverse=True))))

    def test_endpoint_and_parallel_snap_close_multistroke_box(self):
        scene = beautify.beautify(jitter.box((200, 300, 800, 900), seed=40), "diagram")
        lines = [p for p in scene.prims if p.kind == "line"]
        self.assertEqual(len(lines), 4)
        endpoints = np.vstack([p.pts for p in lines])
        unique = np.unique(np.round(endpoints, 5), axis=0)
        self.assertEqual(len(unique), 4)
        self.assertTrue(all(np.count_nonzero(np.all(np.isclose(endpoints, p), axis=1)) == 2 for p in unique))
        self.assertTrue(all(np.ptp(p.pts[:, 0]) == 0 or np.ptp(p.pts[:, 1]) == 0 for p in lines))

    def test_graph_models_and_faithful_fallback(self):
        for model in ("poly1", "poly2", "poly3", "sine", "exp"):
            scene = beautify.beautify(jitter.graph(model, seed=50), "graph")
            self.assertEqual(scene.meta["curve"], model)
            self.assertLess(scene.meta["rmse"], .06 * scene.meta["curve_height"])
            axes = [p for p in scene.prims if p.meta.get("axis")]
            self.assertEqual(len(axes), 2)
            self.assertTrue(all(np.ptp(p.pts[:, 0]) == 0 or np.ptp(p.pts[:, 1]) == 0 for p in axes))
            ticks = sorted(p.pts[:, 0].mean() for p in scene.prims if p.meta.get("tick"))
            self.assertTrue(np.allclose(np.diff(ticks), np.diff(ticks)[0]))
        strokes = jitter.graph("scribble", seed=51)
        scene = beautify.beautify(strokes, "graph")
        self.assertEqual(scene.meta["curve"], "fallback")
        curve = next(p for p in scene.prims if p.meta.get("curve") == "fallback")
        self.assertGreater(len(curve.pts), 20)

    def test_circuit_lattice_and_topology_guard(self):
        good = jitter.circuit(seed=60)
        scene = beautify.beautify(good, "circuit")
        self.assertIsNotNone(scene)
        wires = [p for p in scene.prims if p.kind == "line"]
        self.assertTrue(wires)
        self.assertTrue(all(np.all(np.mod(p.pts, 10) == 0) for p in wires))
        # This endpoint would merge two raw nets only after lattice rounding.
        bad = [[(100, 100), (146, 100)], [(165, 100), (220, 100)]]
        self.assertIsNone(beautify.beautify(bad, "circuit"))

    def test_pdollar_accuracy(self):
        kinds = ("resistor", "capacitor", "ground", "arrowhead", "battery")
        right = 0
        for expected in kinds:
            for seed in range(50):
                got, score = beautify.pdollar(jitter.symbol(expected, seed=seed))
                right += got == expected
        self.assertGreaterEqual(right / 250, .90)
        source = jitter.symbol("battery", seed=77)
        clean = beautify.clean_symbol(source)
        raw_box = np.vstack(source)
        clean_box = np.vstack([p.pts for p in clean])
        self.assertEqual({p.meta["part"] for p in clean}, {"battery"})
        self.assertTrue(np.allclose(clean_box.min(0), raw_box.min(0)))
        self.assertTrue(np.allclose(clean_box.max(0), raw_box.max(0)))

    def test_scene_strokes_keep_source_box_and_curve_spacing(self):
        scene = beautify.beautify([jitter.circle((700, 900), 120, seed=70)], "diagram")
        strokes = scene.strokes()
        points = np.asarray(strokes[0])
        self.assertGreaterEqual(points[:, 0].min(), scene.box[0] - 1)
        self.assertGreaterEqual(points[:, 1].min(), scene.box[1] - 1)
        self.assertLessEqual(np.linalg.norm(np.diff(points, axis=0), axis=1).max(), 3.1)
        line_scene = beautify.beautify([jitter.line((300, 300), (900, 300), 71)], "diagram")
        self.assertEqual(len(line_scene.strokes()[0]), 2)

    def test_scribble_refuses_and_budget_is_small(self):
        scribble = jitter.scribble(seed=7)
        self.assertIsNone(beautify.beautify(scribble, "diagram"))
        strokes = jitter.graph("poly2", seed=90)
        strokes += [jitter.line((400 + i*12, 993), (400 + i*12, 1007), seed=100+i) for i in range(50)]
        timings = []
        for _ in range(5):
            started = time.perf_counter(); beautify.beautify(strokes, "graph")
            timings.append(time.perf_counter() - started)
        self.assertLess(float(np.median(timings)), .150)
        circuit = jitter.circuit(seed=80, wire_count=37)
        timings = []
        for _ in range(5):
            started = time.perf_counter(); beautify.beautify(circuit, "circuit")
            timings.append(time.perf_counter() - started)
        self.assertLess(float(np.median(timings)), .150)


if __name__ == "__main__":
    unittest.main()
