"""The tablet bridge must return as soon as the daemon replies, not when its
10 s watchdog expires (device, 2026-09-29: every status call took 10.03 s)."""

import http.server
import os
import shutil
import subprocess
import threading
import time
import unittest
import queue
import urllib.request
import tempfile
from unittest.mock import patch
from pathlib import Path

EYE = Path(__file__).resolve().parents[1] / "device" / "bin" / "smriti-eye"
WATCH = EYE.with_name("smriti-eye-watch")
BASH = shutil.which("bash")


class _Idle(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"idle\n")

    def log_message(self, *a):
        pass


@unittest.skipUnless(BASH, "needs bash")
class EyeBridgeTest(unittest.TestCase):
    def test_status_returns_without_waiting_for_watchdog(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Idle)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = dict(os.environ, SMRITI_DAEMON=f"127.0.0.1:{srv.server_port}")
        t0 = time.time()
        out = subprocess.run([BASH, str(EYE), "status"], env=env,
                             capture_output=True, text=True, timeout=30).stdout
        took = time.time() - t0
        srv.shutdown()
        self.assertEqual(out.strip(), "idle")
        self.assertLess(took, 4)

    def test_mute_daemon_is_still_cut_off(self):
        import socket
        mute = socket.socket()
        mute.bind(("127.0.0.1", 0))
        mute.listen(1)                         # accepts, never replies
        env = dict(os.environ, SMRITI_DAEMON=f"127.0.0.1:{mute.getsockname()[1]}")
        t0 = time.time()
        out = subprocess.run([BASH, str(EYE), "status"], env=env,
                             capture_output=True, text=True, timeout=30).stdout
        took = time.time() - t0
        mute.close()
        self.assertEqual(out.strip(), "")
        self.assertLess(took, 12)

    def test_focus_and_generic_call_build_safe_paths(self):
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path); self.send_response(200); self.end_headers()
            def log_message(self, *a): pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = dict(os.environ, SMRITI_DAEMON=f"127.0.0.1:{srv.server_port}")
        subprocess.run([BASH, str(EYE), "focus", "ask", "auto", "scene"], env=env, timeout=20)
        subprocess.run([BASH, str(EYE), "call", "panel/retry"], env=env, timeout=20)
        srv.shutdown()
        self.assertEqual(seen, ["/focus?action=ask&box=auto&space=scene", "/panel/retry"])


class FocusControlTest(unittest.TestCase):
    def test_scene_box_converts_to_page_and_auto_resolves(self):
        import sys
        import socket
        sys.path.insert(0, str(EYE.parents[2] / "host"))
        import monke
        ctl = queue.Queue()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        old_view = monke._TURN.get("view")
        self.addCleanup(lambda: monke._TURN.__setitem__("view", old_view))
        monke._TURN["view"] = {"ox": 10, "oy": 20}
        with patch.object(monke, "_auto_focus_box", return_value=(5, 6, 50, 60)):
            monke._control_server(ctl, {}, port)
            time.sleep(0.05)
            urllib.request.urlopen(f"http://127.0.0.1:{port}/focus?action=ask&box=1,2,3,4&space=scene").read()
            self.assertEqual(ctl.get(timeout=1), ("focus", "ask", (11, 22, 13, 24)))
            urllib.request.urlopen(f"http://127.0.0.1:{port}/focus?action=ask&box=auto&space=scene").read()
            self.assertEqual(ctl.get(timeout=1), ("focus", "ask", (15, 26, 60, 80)))

    def test_selection_tool_is_restored_before_inking(self):
        import monke
        calls = []
        with patch.object(monke, "qml_eval", side_effect=lambda host, js: calls.append(js) or
                          ("selection" if len(calls) == 1 else "primary")):
            monke._restore_focus_tool("rm2")
        self.assertIn("activeTool = 'primary'", calls[1])


@unittest.skipUnless(BASH, "needs bash")
class EyeWatchForwardTest(unittest.TestCase):
    def invoke(self, command):
        root = Path(tempfile.mkdtemp())
        (root / "armed").write_text("1")
        (root / "eye-cmd").write_text(command)
        log = root / "argv"
        fake = root / "eye"
        fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log.as_posix()}"\n')
        fake.chmod(0o755)
        env = dict(os.environ, SMRITI_DIR=str(root), SMRITI_EYE=str(fake), SMRITI_EYE_ONCE="1")
        subprocess.run([BASH, str(WATCH)], env=env, timeout=10, check=True)
        return log.read_text().splitlines() if log.exists() else []

    def test_forwards_focus_and_panel_but_drops_shell_input(self):
        self.assertEqual(self.invoke("focus ask auto scene"), ["focus ask auto scene"])
        self.assertEqual(self.invoke("panel retry"), ["call panel/retry"])
        self.assertEqual(self.invoke("focus ask auto;reboot scene"), [])


class EyeCommandTimerTest(unittest.TestCase):
    # device 2026-09-29: every layer/pen switch waited for the eye's 1 s
    # qml-cmd poll (14 calls per ink at ~1.05 s each)
    def test_command_poll_is_fast(self):
        import re
        qmd = (EYE.parents[1] / "xovi" / "smriti-eye.qmd").read_text(encoding="utf-8")
        # the last Timer before the qml-cmd GET is the command poll
        block = qmd[:qmd.index('smritiPoll("smritiCmdXhr", "qml-cmd"')]
        interval = int(re.findall(r"interval:\s*(\d+)", block)[-1])
        self.assertLessEqual(interval, 200)


class EyeNoPerTickAllocationTest(unittest.TestCase):
    # device 2026-09-30: every file XMLHttpRequest leaks ~10-17 KB in
    # xochitl's QML engine; a new one per poll tick grew xochitl 6.5 MB/min
    # (150 ms) / 2.1 MB/min (1 s) until the OOM killer took it at 846 MB
    def test_repeating_timers_allocate_no_requests(self):
        import re
        qmd = (EYE.parents[1] / "xovi" / "smriti-eye.qmd").read_text(encoding="utf-8")
        blocks = re.findall(r"Timer \{.*?onTriggered: \{(.*?)\n\t\t\t\}", qmd, re.S)
        self.assertGreaterEqual(len(blocks), 2)
        for block in blocks:
            self.assertNotIn("new XMLHttpRequest", block)
        self.assertGreaterEqual(qmd.count("Timer {"), 2)



class EyeFalseTouchTest(unittest.TestCase):
    """Navy 2026-09-30: a palm near the bottom-right corner started/stopped
    sessions. The daemon's raw corner zone (224x252 px, mirrored bottom-left
    too) is gone — the eye alone toggles, once per tap, debounced; and the
    eye can sit top-middle."""
    def qmd(self):
        return (EYE.parents[1] / "xovi" / "smriti-eye.qmd").read_text(encoding="utf-8")

    def test_corner_touches_are_not_gestures(self):
        import sys
        sys.path.insert(0, str(EYE.parents[2] / "host"))
        import monke
        g = monke.TouchGesture()
        down = [(0, 0, 3, monke.ABS_MT_TRACKING_ID, 1), (0, 0, 3, monke.ABS_MT_POSITION_X, 760),
                (0, 0, 3, monke.ABS_MT_POSITION_Y, 5)]
        up = [(0, 0, 3, monke.ABS_MT_TRACKING_ID, -1)]
        self.assertEqual([r for ev in down + up if (r := g.feed(ev))], [])

    def test_one_toggle_per_tap_and_debounced(self):
        q = self.qmd()
        self.assertEqual(q.count("smritiEyeUi.smritiToggle()"), 1)
        self.assertIn("lastToggle", q)

    def test_position_chosen_in_the_app(self):
        # Navy 2026-09-30: bottom-right | bottom-left | top-middle, set in the
        # Smriti app -> settings.json "eye"; the eye reads it from there
        q = self.qmd()
        self.assertIn('"settings.json"', q)
        for pos in ("bottom-left", "top-middle"):
            self.assertIn(f'"{pos}"', q)
        app = (EYE.parents[1] / "appload" / "smriti" / "ui" / "smriti.qml").read_text(encoding="utf-8")
        self.assertIn('["bottom-right", "bottom-left", "top-middle"]', app)
        self.assertIn("eye: root.eye", app)


if __name__ == "__main__":
    unittest.main()


class PageTurnStartXTest(unittest.TestCase):
    def test_start_x_is_this_contacts_first_position(self):
        import sys
        sys.path.insert(0, str(EYE.parents[2] / "host"))
        import monke
        g = monke.TouchGesture()
        T, X = monke.ABS_MT_TRACKING_ID, monke.ABS_MT_POSITION_X
        ev = lambda c, v: (0, 0, 3, c, v)
        g.feed(ev(X, 700)); g.feed(ev(T, -1))                 # an earlier touch, far right
        seq = [ev(T, 1), ev(X, 50), ev(X, 60), ev(T, -1)]     # a short touch on the left
        self.assertEqual([r for e in seq if (r := g.feed(e))], [])
        seq = [ev(T, 2), ev(X, 700), ev(X, 100), ev(T, -1)]   # a real fast swipe
        self.assertEqual([r for e in seq if (r := g.feed(e))], ["pageturn"])


class EyeQmlNamesTest(unittest.TestCase):
    """2026-09-30 20:54: 'property bool left' (and 'top') shadowed Item's
    anchor lines -> 'Type MainView unavailable', xochitl crash-looped and
    systemd fell back to xochitl without xovi."""
    def test_no_property_shadows_an_item_member(self):
        import re
        qmd = (EYE.parents[1] / "xovi" / "smriti-eye.qmd").read_text(encoding="utf-8")
        names = set(re.findall(r"property \w+ (\w+)", qmd))
        taken = {"left", "right", "top", "bottom", "horizontalCenter", "verticalCenter",
                 "baseline", "x", "y", "z", "width", "height", "visible", "enabled",
                 "state", "states", "parent", "children", "data", "opacity", "scale",
                 "rotation", "clip", "focus", "anchors", "color", "radius", "border"}
        self.assertEqual(names & taken, set())


class AskQmdTest(unittest.TestCase):
    def test_lasso_patch_is_event_driven_and_uses_auto_fallback(self):
        qmd = (EYE.parents[1] / "qmd" / "smriti-ask.qmd").read_text(encoding="utf-8")
        self.assertIn("AFFECT [[3197972396468700595]]", qmd)
        self.assertIn("TRAVERSE ?#[[214642769434]]", qmd)
        self.assertNotIn("Timer {", qmd)
        for action in ("ask", "solve", "plot", "draw", "explain"):
            self.assertIn(f'"{action}"', qmd)
        self.assertIn("focus \" + action + \" auto scene", qmd)
        self.assertIn("clearSelectedItems", qmd)
        self.assertIn("activeTool", qmd)
        self.assertIn("TODO(selection-rect)", qmd)
        build = (EYE.parents[2] / "VELBUILD").read_text(encoding="utf-8")
        self.assertIn("device/qmd/smriti-ask.qmd", build)
