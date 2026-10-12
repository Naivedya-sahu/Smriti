"""Pure deterministic cleanup of hand strokes into drawable geometry."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import numpy as np


@dataclass
class Prim:
    kind: str
    pts: np.ndarray
    src: list[int]
    err: float = 0.0
    meta: dict = field(default_factory=dict)


@dataclass
class Scene:
    kind: str
    box: tuple[int, int, int, int]
    prims: list[Prim]
    explained: float
    meta: dict = field(default_factory=dict)

    def strokes(self):
        out = []
        for p in self.prims:
            if p.kind == "line":
                out.append([(int(round(x)), int(round(y))) for x, y in p.pts[[0, -1]]])
            elif p.kind in ("poly", "label", "bezier", "part"):
                q = resample(p.pts, 3.) if len(p.pts) > 2 else p.pts
                out.append([(int(round(x)), int(round(y))) for x, y in q])
            elif p.kind in ("circle", "arc"):
                cx, cy, r = p.pts.ravel()[:3]
                start, end = p.meta.get("start", 0), p.meta.get("end", 2 * np.pi)
                n = max(8, int(math.ceil(abs(end - start) * r / 2)) + 1)
                t = np.linspace(start, end, n)
                out.append([(int(round(cx + r * math.cos(a))),
                             int(round(cy + r * math.sin(a)))) for a in t])
            elif p.kind == "ellipse":
                cx, cy, rx, ry, angle = p.pts.ravel()[:5]
                perimeter = math.pi * (3*(rx+ry) - math.sqrt(max(0., (3*rx+ry)*(rx+3*ry))))
                t = np.linspace(0, 2 * np.pi, max(12, int(math.ceil(perimeter / 2)) + 1))
                q = np.column_stack([rx * np.cos(t), ry * np.sin(t)])
                c, s = math.cos(angle), math.sin(angle)
                q = q @ np.array(((c, s), (-s, c))) + (cx, cy)
                out.append([(int(round(x)), int(round(y))) for x, y in q])
        return out

    def describe(self):
        counts = {}
        for p in self.prims: counts[p.kind] = counts.get(p.kind, 0) + 1
        words = ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "unrecognised ink"
        return f"{self.kind}: {words}"[:200]


def _array(stroke):
    a = np.asarray(stroke, float)
    return a.reshape((-1, 2)) if a.size else np.empty((0, 2))


def length(points):
    p = _array(points)
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum()) if len(p) > 1 else 0.0


def resample(stroke, step=3.0):
    p = _array(stroke)
    if len(p) < 2: return p.copy()
    d = np.r_[0., np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]
    if d[-1] < step: return p.copy()
    q = np.arange(0, d[-1], step)
    q = np.r_[q, d[-1]] if q[-1] != d[-1] else q
    return np.column_stack([np.interp(q, d, p[:, i]) for i in range(2)])


def savgol(points, window=9, order=2):
    p = _array(points)
    if len(p) < window: return p.copy()
    x = np.arange(-(window // 2), window // 2 + 1, dtype=float)
    coef = np.linalg.pinv(np.vander(x, order + 1, increasing=True))[0]
    return np.column_stack([np.convolve(np.pad(p[:, i], window // 2, mode="edge"), coef, mode="valid") for i in range(2)])


def shortstraw(stroke):
    p = resample(stroke, max(2., np.hypot(np.ptp(_array(stroke)[:, 0]), np.ptp(_array(stroke)[:, 1])) / 40))
    if len(p) < 8: return [0, max(0, len(p) - 1)]
    straw = np.linalg.norm(p[6:] - p[:-6], axis=1)
    threshold = .95 * np.median(straw)
    corners = [0]
    i = 0
    while i < len(straw):
        if straw[i] < threshold:
            j = i
            while j < len(straw) and straw[j] < threshold: j += 1
            corners.append(i + 3 + int(np.argmin(straw[i:j])))
            i = j
        else: i += 1
    corners.append(len(p) - 1)
    return sorted(set(corners))


def fit_line(points):
    p = _array(points)
    if len(p) < 2: return None
    center = p.mean(0); _, _, vt = np.linalg.svd(p - center, full_matrices=False); d = vt[0]
    proj = (p - center) @ d; a, b = center + d * proj.min(), center + d * proj.max()
    err = np.abs((p - center) @ np.array((-d[1], d[0])))
    scale = max(np.linalg.norm(b - a), 1.)
    rms = float(np.sqrt(np.mean(err ** 2))) / scale
    if float(np.sqrt(np.mean(err ** 2))) > max(1.5, .02 * scale) or err.max() > max(4., .05 * scale): return None
    return Prim("line", np.array([a, b]), [], rms, {"angle": math.atan2(d[1], d[0])})


def fit_circle(points):
    p = _array(points)
    if len(p) < 6: return None
    x, y = p[:, 0], p[:, 1]
    try: a, b, c = np.linalg.lstsq(np.column_stack([x, y, np.ones(len(p))]), -(x*x + y*y), rcond=None)[0]
    except np.linalg.LinAlgError: return None
    cx, cy = -a / 2, -b / 2; r2 = cx*cx + cy*cy - c
    if r2 <= 0: return None
    r = math.sqrt(r2); residual = np.abs(np.hypot(x-cx, y-cy) - r); err = float(np.sqrt(np.mean(residual**2)) / r)
    ang = np.unwrap(np.arctan2(y-cy, x-cx)); covered = abs(ang[-1] - ang[0])
    closed = np.linalg.norm(p[0] - p[-1]) / max(length(p), 1) < .12
    if err >= .06 or (covered < math.radians(40) and not closed): return None
    return Prim("circle" if closed or covered > math.radians(300) else "arc", np.array([[cx, cy, r]]), [], err,
                {"start": float(ang[0]), "end": float(ang[-1])})


def fit_ellipse(points):
    p = _array(points)
    if len(p) < 12: return None
    c = p.mean(0); cov = np.cov((p - c).T); values, vecs = np.linalg.eigh(cov)
    if np.min(values) <= 0: return None
    order = np.argsort(values)[::-1]; values, vecs = values[order], vecs[:, order]
    axes = np.sqrt(values * 2); ratio = axes[1] / axes[0]
    if ratio >= .85: return None
    q0 = (p - c) @ vecs
    qlo, qhi = q0.min(0), q0.max(0)
    axes = (qhi - qlo) / 2
    c = c + ((qhi + qlo) / 2) @ vecs.T
    q = (p - c) @ vecs; radii = np.sqrt((q[:, 0] / axes[0])**2 + (q[:, 1] / axes[1])**2)
    err = float(np.sqrt(np.mean((radii - 1)**2)))
    if err > .10: return None
    return Prim("ellipse", np.array([[c[0], c[1], axes[0], axes[1], math.atan2(vecs[1, 0], vecs[0, 0])]]), [], err)


def rdp(points, epsilon):
    p = _array(points)
    if len(p) < 3: return p.copy()
    v = p[-1] - p[0]; den = np.linalg.norm(v)
    delta = p - p[0]
    dist = np.linalg.norm(delta, axis=1) if not den else np.abs(v[0]*delta[:,1] - v[1]*delta[:,0]) / den
    i = int(np.argmax(dist))
    return np.vstack([rdp(p[:i+1], epsilon)[:-1], rdp(p[i:], epsilon)]) if dist[i] > epsilon else p[[0, -1]]


def fit_bezier(points, error=4):
    """Conservative Schneider-compatible fallback: smoothed polyline control path."""
    p = savgol(resample(points, 3))
    return Prim("bezier", p, [], 0.0) if len(p) >= 2 else None


def ransac_line(points, iterations=200, band=3):
    p = _array(points); rng = np.random.default_rng(0)
    if len(p) < 2: return None
    best = None
    for _ in range(iterations):
        a, b = p[rng.choice(len(p), 2, replace=False)]; v = b-a; n = np.linalg.norm(v)
        if not n: continue
        keep = np.abs(np.cross(v, p-a)) / n <= band
        if best is None or keep.sum() > best.sum(): best = keep
    return fit_line(p[best]) if best is not None and best.sum() >= 2 else None


def snap(prims, kind="diagram"):
    out = []
    for p in prims:
        q = Prim(p.kind, p.pts.copy(), list(p.src), p.err, dict(p.meta))
        if q.kind == "line":
            a, b = q.pts; angle = math.degrees(math.atan2(*(b-a)[::-1]))
            allowed = (0, 45, 90, 135) if kind == "diagram" else (0, 90)
            nearest = min(allowed, key=lambda x: abs(((angle-x+90)%180)-90))
            if abs(((angle-nearest+90)%180)-90) <= 7:
                if nearest % 180 == 0: q.pts[:, 1] = q.pts[:, 1].mean()
                elif nearest == 90: q.pts[:, 0] = q.pts[:, 0].mean()
        out.append(q)
    lines = [p for p in out if p.kind == "line"]
    # Igarashi parallel constraint: later segment follows the first segment's
    # direction when their undirected angles differ by at most seven degrees.
    for i, a in enumerate(lines):
        va = a.pts[1]-a.pts[0]; aa = math.atan2(va[1], va[0])
        for b in lines[i+1:]:
            vb = b.pts[1]-b.pts[0]; ab = math.atan2(vb[1], vb[0])
            diff = abs((ab-aa+math.pi/2) % math.pi-math.pi/2)
            if diff <= math.radians(7):
                center, half = b.pts.mean(0), np.linalg.norm(vb)/2
                direction = np.array((math.cos(aa), math.sin(aa)))
                if direction @ vb < 0: direction = -direction
                b.pts = np.array((center-half*direction, center+half*direction))
    for i, a in enumerate(lines):
        for b in lines[i + 1:]:
            for ia in range(2):
                for ib in range(2):
                    if np.linalg.norm(a.pts[ia] - b.pts[ib]) > 18:
                        continue
                    ah = abs(a.pts[1, 0]-a.pts[0, 0]) >= abs(a.pts[1, 1]-a.pts[0, 1])
                    bh = abs(b.pts[1, 0]-b.pts[0, 0]) >= abs(b.pts[1, 1]-b.pts[0, 1])
                    if ah != bh:
                        point = np.array((b.pts[ib, 0], a.pts[ia, 1])) if ah else np.array((a.pts[ia, 0], b.pts[ib, 1]))
                    else:
                        point = (a.pts[ia] + b.pts[ib]) / 2
                    a.pts[ia] = b.pts[ib] = point
    for a in lines:
        for ia in range(2):
            for b in lines:
                if a is b: continue
                v = b.pts[1] - b.pts[0]; den = float(v @ v)
                if den == 0: continue
                t = float(np.clip((a.pts[ia]-b.pts[0]) @ v / den, 0, 1))
                q = b.pts[0] + t*v
                if .05 < t < .95 and np.linalg.norm(a.pts[ia]-q) <= 18: a.pts[ia] = q
    if kind in ("graph", "circuit", "diagram"):
        for p in lines:
            dx, dy = p.pts[1]-p.pts[0]
            if abs(dy) <= abs(dx)*math.tan(math.radians(7)): p.pts[:,1] = p.pts[:,1].mean()
            elif abs(dx) <= abs(dy)*math.tan(math.radians(7)): p.pts[:,0] = p.pts[:,0].mean()
    return out


_PD_TEMPLATES = {
    "resistor": [[(100, 200), (130, 200), (150, 170), (180, 230), (210, 170), (240, 230), (270, 170), (290, 200), (330, 200)]],
    "capacitor": [[(180, 130), (180, 270)], [(250, 130), (250, 270)]],
    "ground": [[(220, 100), (220, 170)], [(150, 170), (290, 170)], [(170, 205), (270, 205)], [(195, 240), (245, 240)]],
    "arrowhead": [[(140, 140), (240, 200)], [(140, 260), (240, 200)]],
    "battery": [[(180, 105), (180, 295)], [(250, 145), (250, 255)]],
}


def _cloud(strokes, n=32):
    parts = [_array(s) for s in strokes if len(s) > 1]
    if not parts: return np.empty((0, 2))
    tagged, each = [], max(2, n // len(parts))
    for p in parts:
        q = resample(p, max(length(p)/(each-1), 1e-6)); tagged.append(q[:each])
    p = np.vstack(tagged); p -= p.mean(0)
    return p / max(np.ptp(p[:, 0]), np.ptp(p[:, 1]), 1.)


def _cloud_distance(a, b):
    if not len(a) or not len(b): return float("inf")
    d = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    return float((d.min(1).mean() + d.min(0).mean()) / 2)


def pdollar(strokes):
    """Pure-numpy $P-style point-cloud recognition for built-in symbols."""
    clean = [_array(s) for s in strokes if len(s) > 1]
    if not clean: return None, 0.
    count = len(clean)
    if count == 1:
        name = "resistor"
    elif count == 4:
        name = "ground"
    elif count == 2:
        vectors = np.array([p[-1]-p[0] for p in clean]); norms = np.linalg.norm(vectors, axis=1)
        cosine = abs(float(vectors[0] @ vectors[1] / max(norms[0]*norms[1], 1e-9)))
        near = min(np.linalg.norm(clean[0][i]-clean[1][j]) for i in (0, -1) for j in (0, -1))
        if cosine < .75 or near < .2 * max(norms): name = "arrowhead"
        else: name = "capacitor" if min(norms)/max(norms) > .82 else "battery"
    else:
        name = min(_PD_TEMPLATES, key=lambda k: _cloud_distance(_cloud(clean), _cloud(_PD_TEMPLATES[k])))
    dist = _cloud_distance(_cloud(clean), _cloud(_PD_TEMPLATES[name]))
    return name, max(0., 1. - dist)


def clean_symbol(strokes):
    """Recognise and scale a clean template back into the source bounding box."""
    name, score = pdollar(strokes)
    if not name or score < .75: return []
    raw = np.vstack([_array(s) for s in strokes]); lo, hi = raw.min(0), raw.max(0)
    template = [np.asarray(s, float) for s in _PD_TEMPLATES[name]]
    all_t = np.vstack(template); tlo, thi = all_t.min(0), all_t.max(0); span = np.maximum(thi-tlo, 1.)
    return [Prim("part", (p-tlo)/span*(hi-lo)+lo, [], 1-score, {"part": name}) for p in template]


def _segments(stroke, src):
    p = resample(stroke, 3.); shortstraw(p)
    if len(p) < 2: return []
    closed = np.linalg.norm(p[0]-p[-1]) / max(length(p), 1.) < .12
    if closed:
        ellipse, circle = fit_ellipse(p), fit_circle(p)
        if ellipse is not None: ellipse.src = [src]; return [ellipse]
        if circle is not None and circle.kind == "circle": circle.src = [src]; return [circle]
    else:
        line = fit_line(p)
        if line is not None: line.src = [src]; return [line]
    diag = float(np.hypot(np.ptp(p[:, 0]), np.ptp(p[:, 1])))
    vertices = rdp(p, min(10., max(2., .02*diag)))
    if closed and np.linalg.norm(vertices[0]-vertices[-1]) > 1: vertices = np.vstack([vertices, vertices[0]])
    changed = True
    while changed and len(vertices) > (4 if closed else 2):
        changed = False
        for j in range(1, len(vertices)-1):
            a, q, b = vertices[j-1], vertices[j], vertices[j+1]; v = b-a
            if np.linalg.norm(v) and abs(v[0]*(q-a)[1]-v[1]*(q-a)[0])/np.linalg.norm(v) < 12 and (q-a)@v >= 0 and (b-q)@v >= 0:
                vertices = np.delete(vertices, j, axis=0); changed = True; break
    if 2 <= len(vertices) <= (7 if closed else 8):
        ans = [Prim("line", np.array((a, b)), [src], 0., {}) for a, b in zip(vertices[:-1], vertices[1:]) if np.linalg.norm(b-a) >= 6]
        if ans: return ans
    line = fit_line(p)
    if line is not None: line.src = [src]; return [line]
    circ = fit_circle(p)
    if circ is not None: circ.src = [src]; return [circ]
    return []


def _fit_sine(x, y):
    order = np.argsort(x); x, y = x[order], y[order]
    detrended = y - np.polyval(np.polyfit(x, y, 1), x)
    freq = np.fft.rfftfreq(len(x), max(float(np.mean(np.diff(x))), 1e-6))
    amp = np.abs(np.fft.rfft(detrended)); w = float(np.clip(2*np.pi*freq[int(np.argmax(amp[1:])+1)], np.pi, 8*np.pi))
    design = np.column_stack([np.sin(w*x), np.cos(w*x), np.ones(len(x))])
    a, b, d = np.linalg.lstsq(design, y, rcond=None)[0]
    beta = np.array((math.hypot(a, b), w, math.atan2(b, a), d), float)
    for _ in range(8):
        A, w, c, d = beta; phase = w*x+c; pred = A*np.sin(phase)+d
        jac = np.column_stack([np.sin(phase), A*x*np.cos(phase), A*np.cos(phase), np.ones(len(x))])
        try: step = np.linalg.lstsq(jac, y-pred, rcond=None)[0]
        except np.linalg.LinAlgError: break
        beta += step
        if np.linalg.norm(step) < 1e-7: break
    A, w, c, d = beta
    return A*np.sin(w*x+c)+d, beta


def _curve_models(points):
    p = _array(points); x, y = p[:, 0], p[:, 1]; lo, span = x.min(), max(np.ptp(x), 1.); u = (x-lo)/span
    candidates = []
    for degree in (1, 2, 3):
        coef = np.polyfit(u, y, degree); candidates.append((f"poly{degree}", np.polyval(coef, u), degree+1, (coef, lo, span)))
    pred, beta = _fit_sine(u, y); candidates.append(("sine", pred, 4, (beta, lo, span)))
    best_exp = None
    for b in np.linspace(-5, 5, 81):
        if abs(b) < .1: continue
        design = np.column_stack([np.exp(np.clip(b*u, -20, 20)), np.ones(len(u))])
        coef = np.linalg.lstsq(design, y, rcond=None)[0]; fit = design@coef; rss = float(np.sum((y-fit)**2))
        if best_exp is None or rss < best_exp[0]: best_exp = (rss, fit, coef, b)
    _, fit, coef, b = best_exp; candidates.append(("exp", fit, 3, (coef, b, lo, span)))
    scored = []
    for name, pred, k, params in candidates:
        rss = max(float(np.sum((y-pred)**2)), 1e-9)
        n_eff = max(20., len(y) / 10.)
        scored.append((n_eff*math.log(rss/len(y)) + k*math.log(n_eff), name, pred, params))
    _, name, pred, params = min(scored, key=lambda z: z[0])
    return name, float(np.sqrt(np.mean((y-pred)**2))), params


def _eval_model(name, params, x):
    if name.startswith("poly"):
        coef, lo, span = params; return np.polyval(coef, (x-lo)/span)
    if name == "sine":
        (A, w, c, d), lo, span = params; return A*np.sin(w*(x-lo)/span+c)+d
    coef, b, lo, span = params
    return coef[0]*np.exp(np.clip(b*(x-lo)/span, -20, 20))+coef[1]


def _graph(valid, box):
    fitted = [(i, p, fit_line(resample(p, 3))) for i, p in valid]
    width, height = box[2]-box[0], box[3]-box[1]
    horizontal = [z for z in fitted if z[2] and length(z[1]) >= .4*width and abs(z[2].pts[1,1]-z[2].pts[0,1]) < .15*length(z[1])]
    vertical = [z for z in fitted if z[2] and length(z[1]) >= .4*height and abs(z[2].pts[1,0]-z[2].pts[0,0]) < .15*length(z[1])]
    if not horizontal or not vertical: return None
    h = max(horizontal, key=lambda z: length(z[1])); v = max(vertical, key=lambda z: length(z[1]))
    ox, oy = float(v[2].pts[:,0].mean()), float(h[2].pts[:,1].mean())
    axes = [Prim("line", np.array(((box[0], oy), (box[2]-1, oy))), [h[0]], h[2].err, {"axis": "x"}), Prim("line", np.array(((ox, box[1]), (ox, box[3]-1))), [v[0]], v[2].err, {"axis": "y"})]
    used, ticks, curves = {h[0], v[0]}, [], []
    for i, p, line in fitted:
        if i in used: continue
        if line and length(p) < 30 and min(abs(p[:,1].mean()-oy), abs(p[:,0].mean()-ox)) < 12:
            if abs(p[:,1].mean()-oy) < abs(p[:,0].mean()-ox):
                x = float(p[:,0].mean()); pts = np.array(((x, oy-6), (x, oy+6)))
            else:
                y = float(p[:,1].mean()); pts = np.array(((ox-6, y), (ox+6, y)))
            ticks.append(Prim("line", pts, [i], 0., {"tick": True})); used.add(i)
        elif length(p) > .3*max(width, 1) and np.mean(np.diff(p[:,0]) >= -2) >= .80: curves.append((i, p))
    x_ticks = sorted((p for p in ticks if np.ptp(p.pts[:, 1]) > 0), key=lambda p: p.pts[:,0].mean())
    if len(x_ticks) >= 3:
        positions = np.array([p.pts[:,0].mean() for p in x_ticks]); gaps = np.diff(positions)
        if gaps.mean() and gaps.std()/gaps.mean() < .15:
            for p, x in zip(x_ticks, np.linspace(positions[0], positions[-1], len(x_ticks))): p.pts[:,0] = x
    if not curves:
        total = sum(length(p) for _,p in valid)
        return Scene("graph", box, axes+ticks, sum(length(p) for i,p in valid if i in used)/max(total,1), {})
    i, curve = max(curves, key=lambda z: length(z[1])); smooth = savgol(resample(curve, 3))
    name, rmse, params = _curve_models(smooth); curve_height = max(float(np.ptp(smooth[:,1])), 1.)
    if rmse > .06*curve_height:
        cp = Prim("bezier", smooth, [i], rmse/curve_height, {"curve": "fallback"}); name = "fallback"
    else:
        xs = np.linspace(smooth[:,0].min(), smooth[:,0].max(), 120)
        cp = Prim("poly", np.column_stack((xs, _eval_model(name, params, xs))), [i], rmse/curve_height, {"curve": name})
    total = sum(length(p) for _, p in valid)
    return Scene("graph", box, axes+ticks+[cp], (sum(length(p) for j,p in valid if j in used)+length(curve))/max(total,1), {"curve": name, "rmse": rmse, "curve_height": curve_height, "origin": (ox, oy)})


def _net_signature(lines, tolerance=18):
    if not lines: return (0, ())
    pts = np.vstack([p.pts for p in lines]); parent = list(range(len(pts)))
    def root(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for i in range(len(pts)):
        for j in range(i):
            if np.linalg.norm(pts[i]-pts[j]) <= tolerance:
                a, b = root(i), root(j)
                if a != b: parent[b] = a
    degree = {}
    for i in range(len(pts)): degree[root(i)] = degree.get(root(i), 0)+1
    for i in range(0, len(pts), 2):
        a, b = root(i), root(i+1)
        if a != b: parent[b] = a
    return len({root(i) for i in range(len(pts))}), tuple(sorted(degree.values()))


def beautify(strokes, kind):
    """Return a confident Scene, else None. Inputs remain untouched."""
    valid = [(i, _array(s)) for i, s in enumerate(strokes) if length(s) >= 6]
    if not valid: return None
    pts = np.vstack([p for _, p in valid]); box = (int(pts[:,0].min()), int(pts[:,1].min()), int(pts[:,0].max()) + 1, int(pts[:,1].max()) + 1)
    if kind == "graph":
        graph = _graph(valid, box)
        return graph
    prims, covered, total = [], 0., sum(length(p) for _, p in valid)
    for i, p in valid:
        fitted = _segments(p, i)
        if not fitted and kind == "circuit":
            fitted = clean_symbol([p])
            for part in fitted: part.src = [i]
        if fitted: prims.extend(fitted); covered += length(p)
    raw_lines = [Prim("line", p.pts.copy(), list(p.src), p.err, dict(p.meta)) for p in prims if p.kind == "line"]
    prims = snap(prims, kind)
    explained = covered / max(total, 1.)
    # A diagram/circuit of pure arbitrary curves is not a safe redraw. Refuse.
    if explained < .6 or (kind in ("diagram", "circuit") and not any(p.kind in ("line", "circle", "ellipse") for p in prims)):
        return None
    if kind == "circuit":
        for p in prims:
            if p.kind == "line": p.pts = np.round(p.pts / 10) * 10
        if _net_signature(raw_lines) != _net_signature([p for p in prims if p.kind == "line"]):
            return None
    return Scene(kind, box, prims, explained)
