"""Seeded synthetic hand-like strokes for offline geometry tests."""
from __future__ import annotations

import numpy as np


def _hand(points, seed=0, noise=1.5, wobble=3.0):
    p = np.asarray(points, float)
    if len(p) < 2:
        return [(int(round(x)), int(round(y))) for x, y in p]
    d = np.r_[0., np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]
    n = max(2, int(d[-1] / 3) + 1)
    q = np.column_stack([np.interp(np.linspace(0, d[-1], n), d, p[:, j]) for j in range(2)])
    rng = np.random.default_rng(seed)
    tangent = np.gradient(q, axis=0)
    normal = np.column_stack([-tangent[:, 1], tangent[:, 0]])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
    phase = rng.uniform(0, np.pi * 2)
    q += normal * (wobble * np.sin(np.linspace(0, 2 * np.pi * d[-1] / 130, n) + phase))[:, None]
    q += rng.normal(0, noise, q.shape)
    return [(int(round(x)), int(round(y))) for x, y in q]


def line(a, b, seed=0):
    return _hand([a, b], seed)


def circle(center, radius, seed=0, arc=(0., 2 * np.pi)):
    t = np.linspace(arc[0], arc[1], max(24, int(abs(arc[1] - arc[0]) * radius / 3)))
    return _hand(np.column_stack([center[0] + radius * np.cos(t), center[1] + radius * np.sin(t)]), seed)


def ellipse(center, axes, seed=0):
    t = np.linspace(0, 2 * np.pi, max(32, int(2 * np.pi * max(axes) / 3)))
    return _hand(np.column_stack([center[0] + axes[0] * np.cos(t), center[1] + axes[1] * np.sin(t)]), seed)


def polygon(vertices, seed=0):
    """One continuous hand-drawn polygon/polyline through every supplied vertex."""
    return _hand(vertices, seed, noise=.8, wobble=1.5)


def box(bounds=(200, 300, 800, 900), seed=0):
    """Four independently drawn, slightly gapped sides."""
    x0, y0, x1, y1 = bounds
    rng = np.random.default_rng(seed)
    corners = np.array(((x0, y0), (x1, y0), (x1, y1), (x0, y1)), float)
    out = []
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        a = a + rng.normal(0, 4, 2); b = b + rng.normal(0, 4, 2)
        out.append(_hand([a, b], seed + i, noise=.7, wobble=1.2))
    return out


def graph(model="poly2", seed=0):
    out = [line((200, 1000), (1100, 1000), seed), line((350, 1400), (350, 300), seed + 1)]
    for i, x_tick in enumerate(range(450, 1051, 100)):
        out.append(line((x_tick, 992), (x_tick, 1008), seed + 10 + i))
    x = np.linspace(390, 1050, 180)
    u = (x - 390) / 660
    if model in ("sin", "sine"):
        y = 900 - 300 * np.sin(u * 2 * np.pi)
    elif model == "exp":
        y = 1250 - 700 * (np.exp(2 * u) - 1) / (np.exp(2) - 1)
    elif model == "poly1":
        y = 1250 - 650 * u
    elif model == "poly3":
        y = 1250 - 650*u + 700*(u - .5)**3
    elif model == "scribble":
        y = 850 + 170*np.sin(7*np.pi*u) + 90*np.sin(19*np.pi*u + .4)
    else:
        y = 1200 - 650 * (u - .5) ** 2
    out.append(_hand(np.column_stack([x, y]), seed + 2, noise=1.0, wobble=1.5))
    return out


def _symbol_clean(kind):
    if kind == "resistor":
        return [[(100, 200), (130, 200), (150, 170), (180, 230), (210, 170),
                 (240, 230), (270, 170), (290, 200), (330, 200)]]
    if kind == "capacitor":
        return [[(180, 130), (180, 270)], [(250, 130), (250, 270)]]
    if kind == "ground":
        return [[(220, 100), (220, 170)], [(150, 170), (290, 170)],
                [(170, 205), (270, 205)], [(195, 240), (245, 240)]]
    if kind == "arrowhead":
        return [[(140, 140), (240, 200)], [(140, 260), (240, 200)]]
    if kind == "battery":
        return [[(180, 105), (180, 295)], [(250, 145), (250, 255)]]
    raise ValueError(f"unknown symbol: {kind}")


def symbol(kind, seed=0):
    """Jittered built-in circuit/diagram symbol."""
    rng = np.random.default_rng(seed)
    angle = rng.uniform(-.08, .08); scale = rng.uniform(.92, 1.08)
    c, s = np.cos(angle), np.sin(angle)
    transform = scale * np.array(((c, -s), (s, c)))
    shift = rng.normal(0, 3, 2)
    out = []
    for i, stroke in enumerate(_symbol_clean(kind)):
        p = (np.asarray(stroke, float) - (220, 200)) @ transform.T + (220, 200) + shift
        out.append(_hand(p, seed * 17 + i, noise=.8, wobble=1.0))
    return out


def circuit(seed=0, wire_count=6):
    """Connected Manhattan wires plus resistor and capacitor symbols."""
    out = []
    # One connected stepped net; long enough for performance loads without overlap.
    p = np.array((100., 400.))
    for i in range(wire_count):
        delta = np.array((40. if i % 2 == 0 else 0., 0. if i % 2 == 0 else 30.))
        q = p + delta
        out.append(_hand([p, q], seed + i, noise=.35, wobble=.5))
        p = q
    # Required generated component examples, spatially separate from the wire net.
    out.extend(symbol("resistor", seed + 100))
    cap = symbol("capacitor", seed + 101)
    out.extend([[(x + 420, y + 250) for x, y in stroke] for stroke in cap])
    return out


def scribble(seed=0):
    rng = np.random.default_rng(seed)
    p = np.cumsum(rng.normal(0, 35, (35, 2)), axis=0) + (700, 800)
    return [_hand(p, seed, noise=5, wobble=8)]
