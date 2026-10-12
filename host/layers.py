"""
layers.py — Monke writes on ITS OWN xochitl layer, never on Navy's.

Everything Monke inks (replies, plots, greetings, fades) goes to a layer
named "Monke" on the current page; Navy's layer and pen are restored after.
A bad reply is then one clearLinesInLayer away, and no eraser pass or
misplaced stroke can touch his handwriting (the eraser is layer-scoped).

Driven through the eye patch's qml-cmd channel (monke.qml_eval). Layer ops
in xochitl apply ASYNC — every switch is verified by reading back.

    uv run python host/layers.py state          # current page's layers
    uv run python host/layers.py probe          # on-device behaviour suite
Probe = scratch layers only ("probe-user" stands in for Navy's layer); it
measures ink per layer by isolating visibility + screenshot pixel counts in
a free zone, prints PASS/FAIL per behaviour, then deletes its scratch layers.
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager

MONKE = "Monke"
GREET = "Monke-greet"       # transient: the greeting, dropped whole on fade
MAX_LAYERS = 5              # per page; xochitl ignores addLayer beyond this


def _q(host: str, js: str) -> str | None:
    from monke import qml_eval
    return qml_eval(host, js)


_STATE_JS = ("var s=sceneController, n=[], v=[];"
             " for (var i=0;i<s.layerCount;i++){ n.push(s.layerName(i));"
             " v.push(s.isLayerVisible(i)); }"
             " var p=documentViewTools.activePen;"
             " JSON.stringify({current:s.currentLayer, count:s.layerCount,"
             " names:n, visible:v, color:p.color, thickness:p.thickness,"
             " tool:p.tool})")


def _do(host: str, js: str) -> dict | None:
    """Run js and read the state back in the SAME eval: one round trip
    (each costs ~0.5 s; an ink took 14 of them, device 2026-09-29)."""
    out = _q(host, f"{js}; {_STATE_JS}")
    try:
        return json.loads(out) if out else None
    except ValueError:
        return None


def state(host: str) -> dict | None:
    return _do(host, "1")


def _until(host: str, cond, timeout: float = 5.0) -> dict | None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = state(host)
        if st and cond(st):
            return st
        time.sleep(0.3)
    return None


def set_current(host: str, i: int) -> bool:
    st = _do(host, f"sceneController.setCurrentLayer({i})")
    if st and st["current"] == i:
        return True
    return _until(host, lambda s: s["current"] == i) is not None   # applied late


def find_or_add(host: str, name: str) -> int | None:
    st = state(host)
    if st is None:
        return None
    if name in st["names"]:
        return st["names"].index(name)
    n = st["count"]
    if n >= MAX_LAYERS:
        # xochitl silently ignores a 6th addLayer (device, 2026-09-26)
        print(f"[layers] page already has {n} layers (xochitl max "
              f"{MAX_LAYERS}) — no room for {name!r}", flush=True)
        return None
    _q(host, "sceneController.addLayer()")
    st = _until(host, lambda s: s["count"] == n + 1)
    if st is None:
        return None
    new = n                                   # addLayer appends on top
    # addLayer ALSO makes the new layer current — asynchronously, after the
    # count bump. Wait for that, or it lands after a later setCurrentLayer
    # and silently reroutes ink (probe 2026-09-26: user line went to monke)
    _until(host, lambda s: s["current"] == new, timeout=3.0)
    _q(host, f"sceneController.setLayerName({new}, {json.dumps(name)})")
    ok = _until(host, lambda s: s["names"][new] == name)
    return new if ok else None


def pen_js(color=None, thickness=None, tool=None) -> str:
    parts = [f"documentViewTools.activePen.{k} = {int(v)}"
             for k, v in (("tool", tool), ("color", color), ("thickness", thickness))
             if v is not None]
    return "; ".join(parts) + "; 1" if parts else "1"


@contextmanager
def on_layer(host: str, name: str = MONKE, color: int | None = None,
             thickness: int | None = None):
    """Ink inside this block lands on layer `name` (created if missing),
    optionally in pen `color` / `thickness`. On exit — even on error — Navy's
    pen comes back EXACTLY as it was (Navy 2026-09-26): layer, tool, colour
    and width, then verified by readback (one retry)."""
    before = state(host)
    if before and name in before["names"]:
        idx = before["names"].index(name)            # no second state read
    else:
        idx = find_or_add(host, name) if before else None
    # switch + pen + readback in one call; poll only if applied late
    st = _do(host, f"sceneController.setCurrentLayer({idx}); "
                   f"{pen_js(color, thickness)}") if idx is not None else None
    if idx is None or not (st and st["current"] == idx
                           or _until(host, lambda s: s["current"] == idx)):
        raise RuntimeError(f"can't switch to layer {name!r} (eye patch loaded?)")
    try:
        yield idx
    finally:
        restore(host, before)


def restore(host: str, before: dict) -> bool:
    """Put layer + pen (tool, colour, thickness) back; True when verified."""
    keys = ("current", "tool", "color", "thickness")
    ok = lambda st: all(st.get(k) == before.get(k) for k in keys if k in before)
    for _ in range(2):
        now = _do(host, f"{pen_js(before.get('color'), before.get('thickness'), before.get('tool'))}; "
                        f"sceneController.setCurrentLayer({before['current']})") or {}
        if ok(now):
            return True
        time.sleep(0.5)                      # applied late: read once more
        now = state(host) or {}
        if ok(now):
            return True
    print(f"[layers] pen NOT fully restored: was {[before.get(k) for k in keys]} "
          f"now {[now.get(k) for k in keys]}", flush=True)
    return False


def clear(host: str, name: str = MONKE) -> bool:
    """Wipe everything Monke inked on this page — Navy's layers untouched."""
    st = state(host)
    if not st or name not in st["names"]:
        return False
    _q(host, f"sceneController.clearLinesInLayer({st['names'].index(name)})")
    return True


def drop(host: str, name: str) -> bool:
    """Wipe and delete layer `name` (exact, ~2 QML calls): how transient
    Monke ink fades. A stroke-tracing erase of one greeting took 37-95 s and
    left residue (device 2026-09-29). False when the page has no such layer
    (e.g. the page turned since it was inked)."""
    st = state(host)
    if not st or name not in st["names"]:
        return False
    i = st["names"].index(name)
    _q(host, f"sceneController.clearLinesInLayer({i})")
    _q(host, f"sceneController.deleteLayer({i})")
    return _until(host, lambda s: name not in s["names"]) is not None


# ── on-device probe ───────────────────────────────────────────────────────────

def _zone_ink(img, zone) -> int:
    """Dark px in zone. Screenshots are RGB now: gray first, or each pixel
    counts once per channel and colour ink skews the probe."""
    import numpy as np
    a = np.asarray(img.convert("L"))[zone[1]:zone[3], zone[0]:zone[2]]
    return int((a < 200).sum())


def _probe_zone(bands):
    """(x0, y0, x1, y1) in the first free band >= 140 px below the top UI
    (y 150). Clamped, not filtered: a blank page's only band starts at 0."""
    for a, b in bands:
        a = max(a, 150)
        if b - a >= 140:
            return (400, a + 20, 1000, a + 120)
    return None


def _probe(host: str) -> None:
    sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
    import numpy as np
    from monke import lamp
    from screen import screenshot, free_bands

    shot = screenshot()
    zone = _probe_zone(free_bands(shot))
    if zone is None:
        sys.exit("no free band >=140px on this page — open a page with room")
    print(f"zone {zone}")
    base = state(host)
    print("before:", base)
    if base is None or base["count"] > MAX_LAYERS - 2:
        sys.exit(f"probe needs 2 free layer slots (max {MAX_LAYERS}); page has "
                 f"{base and base['count']} — run it on a page with <= 3 layers")
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")

    def ink_only(i):
        """Ink px in zone with only layer i visible (None = all layers)."""
        st = state(host)
        for k in range(st["count"]):
            _q(host, f"sceneController.setLayerVisible({k}, {'true' if i is None or k == i else 'false'})")
        _until(host, lambda s: all(v == (i is None or k == i)
                                   for k, v in enumerate(s["visible"])))
        time.sleep(1.2)                        # e-ink repaint before grab
        return _zone_ink(screenshot(), zone)

    def line(x0, x1, y, erase=False):
        t = "erase" if erase else "pen"
        lamp(f"{t} down {x0} {y} 2600\n{t} move {x1} {y}\n{t} up\n", host)

    try:
        # 1 addLayer is async + appends on top; names round-trip
        u = find_or_add(host, "probe-user")
        m = find_or_add(host, "probe-monke")
        st = state(host)
        check("addLayer appends + setLayerName", u is not None and m == u + 1
              and st["names"][u] == "probe-user", f"u={u} m={m}")
        # 2 ink lands on the CURRENT layer only
        set_current(host, u)
        line(zone[0] + 20, zone[2] - 20, zone[1] + 30)
        set_current(host, m)
        line(zone[0] + 20, zone[2] - 20, zone[1] + 70)
        iu, im = ink_only(u), ink_only(m)
        check("ink goes to current layer", iu > 300 and im > 300, f"user={iu} monke={im}")
        # 3 eraser is layer-scoped: erase across BOTH lines while on monke
        for dx in range(zone[0] + 40, zone[2] - 40, 12):
            lamp(f"erase down {dx} {zone[1] + 5}\nerase move {dx} {zone[3] - 5}\nerase up\n", host)
        iu2, im2 = ink_only(u), ink_only(m)
        check("eraser only hits current layer", iu2 >= iu * 0.9 and im2 < im * 0.6,
              f"user {iu}->{iu2}  monke {im}->{im2}")
        # 4 clearLinesInLayer wipes only that layer
        line(zone[0] + 20, zone[2] - 20, zone[1] + 70)
        _q(host, f"sceneController.clearLinesInLayer({m})")
        time.sleep(0.8)
        iu3, im3 = ink_only(u), ink_only(m)
        # an empty layer still reads the template dot grid (~112 px here)
        check("clearLinesInLayer(monke) spares user", iu3 >= iu * 0.9 and im3 < im * 0.1,
              f"user={iu3} monke={im3}")
        # 5 on_layer restores Navy's current layer + pen colour
        set_current(host, u)
        _q(host, "documentViewTools.activePen.color = 0")
        with on_layer(host, "probe-monke", color=7):
            mid = state(host)
            line(zone[0] + 20, zone[2] - 20, zone[1] + 70)
        after = state(host)
        check("on_layer switches then restores", mid["current"] == m and mid["color"] == 7
              and after["current"] == u and after["color"] == 0,
              f"inside L{mid['current']}/c{mid['color']} after L{after['current']}/c{after['color']}")
        # 6 hiding monke hides only monke ink
        iall = ink_only(None)
        _q(host, f"sceneController.setLayerVisible({m}, false)")
        _until(host, lambda s: not s["visible"][m])
        time.sleep(1.2)
        ihid = _zone_ink(screenshot(), zone)
        check("hide monke layer", ihid < iall and ihid >= iu3 * 0.8, f"all={iall} hidden={ihid}")
    finally:
        st = state(host) or {"names": []}
        for name in ("probe-monke", "probe-user"):          # top first
            if name in st["names"]:
                _q(host, f"sceneController.deleteLayer({st['names'].index(name)})")
                st = _until(host, lambda s, n=name: n not in s["names"]) or st
        for k in range((state(host) or {"count": 0})["count"]):
            _q(host, f"sceneController.setLayerVisible({k}, true)")
        _q(host, f"documentViewTools.activePen.color = {base['color']}")
        set_current(host, base["current"])
        print("after:", state(host))
    print(f"\n{sum(results)}/{len(results)} behaviours as expected")


if __name__ == "__main__":
    sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    host = sys.argv[2] if len(sys.argv) > 2 else "rm2"
    if cmd == "state":
        print(json.dumps(state(host), indent=1))
    elif cmd == "probe":
        _probe(host)
    else:
        sys.exit(__doc__)
