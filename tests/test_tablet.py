"""One persistent shell for all tablet calls (device 2026-09-30: every lamp
and QML call spawned ssh, ~0.4 s each, 13+ per rich reply)."""
# pyright: reportMissingImports=false

import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import tablet

BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "needs bash")
class ChannelTest(unittest.TestCase):
    def setUp(self):
        self.ch = tablet.Channel([BASH])

    def tearDown(self):
        self.ch.close()

    def test_stdin_output_and_exit_code_through_one_process(self):
        self.assertEqual(self.ch.run("cat", b"pen down 1 2 2400\npen up\n"),
                         (0, b"pen down 1 2 2400\npen up\n"))
        pid = self.ch.p.pid
        self.assertEqual(self.ch.run("printf abc"), (0, b"abc"))   # no newline
        self.assertEqual(self.ch.run("exit_code() { return 3; }; exit_code")[0], 3)
        self.assertEqual(self.ch.p.pid, pid)                       # reused

    @unittest.skipUnless(shutil.which("openssl"), "needs openssl (tablet has it)")
    def test_binary_stdin_round_trips(self):
        # confirm.png goes through the channel too; the tablet has no base64,
        # it has openssl
        data = bytes(range(256)) * 40
        self.assertEqual(self.ch.run("cat", data), (0, data))

    def test_timeout_resets_the_channel(self):
        with self.assertRaises(TimeoutError):
            self.ch.run("sleep 5", timeout=0.5)
        self.assertEqual(self.ch.run("echo ok"), (0, b"ok\n"))      # fresh shell


class RoutedCallsTest(unittest.TestCase):
    def test_lamp_and_qml_go_through_the_channel(self):
        import json
        from unittest.mock import patch
        import monke
        calls = []

        def fake(host, script, stdin=b"", timeout=30):
            calls.append((host, script, stdin))
            if "qml-cmd" in script:
                cid = json.loads(stdin)["id"]
                return 0, f"{cid}\n2".encode()
            return 0, b""
        with (patch.object(tablet, "run", fake),
              patch.object(monke.subprocess, "run", side_effect=AssertionError("ssh spawned"))):
            monke.lamp("pen down 1 2 2400\npen up\n", "rm2")
            self.assertEqual(monke.qml_eval("rm2", "1+1"), "2")
        self.assertEqual(calls[0][1], monke.LAMP_BIN)
        self.assertEqual(calls[0][2], b"pen down 1 2 2400\npen up\n")

    def test_no_one_shot_ssh_on_the_turn_path(self):
        # each one-shot ssh cost ~0.45 s; confirm polled one every 1.5 s
        from unittest.mock import patch
        import monke
        seen = []

        def fake(host, script, stdin=b"", timeout=30):
            seen.append(script)
            if "confirm-reply" in script and script.startswith("cat"):
                return 0, b"confirm\n"
            return 0, b""
        with (patch.object(tablet, "run", fake),
              patch.object(monke.subprocess, "run", side_effect=AssertionError("ssh spawned"))):
            monke._notebook("rm2")
            monke.eye_state("rm2", "watching")
            monke.tablet_settings("rm2")
            monke.publish_usage("rm2")
            monke._point_tablet("rm2", 7333, "")
            from PIL import Image
            got = monke._confirm(__import__("harness").parse_reading('{"kind": "text", "text": "hi"}'),
                                 Image.new("RGB", (4, 4)), "rm2", timeout=5)
        self.assertIsNotNone(got)
        self.assertTrue(any("confirm.png" in s for s in seen))

    def test_settings_fall_back_when_link_drops(self):
        from unittest.mock import patch
        import monke
        with (patch.object(tablet, "run", side_effect=ConnectionError("gone")),
              patch.object(monke, "_settings_known", return_value={"model": "sonnet"})):
            self.assertEqual(monke.tablet_settings("rm2"), {"model": "sonnet"})

    def test_lamp_failure_raises(self):
        from unittest.mock import patch
        import monke
        with patch.object(tablet, "run", return_value=(1, b"")):
            with self.assertRaises(RuntimeError):
                monke.lamp("pen up\n", "rm2")


class DetachStdinTest(unittest.TestCase):
    # device 2026-09-30: daemon started in a PowerShell console; the pen and
    # touch ssh streams inherited the console as stdin, and every later
    # one-shot ssh (settings, confirm reply) hung to its timeout: "tablet
    # unreachable", confirm stuck on "waiting for Navy"
    def test_children_get_eof_not_the_console(self):
        import os
        import subprocess
        import monke
        saved = os.dup(0)
        try:
            monke._detach_stdin()
            out = subprocess.run([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
        finally:
            os.dup2(saved, 0)
            os.close(saved)
        self.assertEqual(out, "''")


class EnsureXoviTest(unittest.TestCase):
    # after every reboot the eye patch was gone until Navy ran
    # `ssh rm2 /home/root/xovi/start` by hand (HANDOFF 2026-09-30)
    def test_loaded_patch_is_left_alone(self):
        from unittest.mock import patch
        import monke
        calls = []
        with patch.object(tablet, "run", lambda h, s, i=b"", timeout=30: calls.append(s) or (0, b"")):
            self.assertTrue(monke.ensure_xovi("rm2"))
        self.assertEqual(len(calls), 1)
        self.assertIn("xovi.so", calls[0])

    def test_missing_patch_runs_xovi_start(self):
        from unittest.mock import patch
        import monke
        calls = []
        rcs = iter([1, 0])
        with patch.object(tablet, "run", lambda h, s, i=b"", timeout=30: calls.append(s) or (next(rcs), b"")):
            self.assertTrue(monke.ensure_xovi("rm2"))
        self.assertIn("/home/root/xovi/start", calls[1])

    def test_link_down_is_false_not_a_crash(self):
        from unittest.mock import patch
        import monke
        with patch.object(tablet, "run", side_effect=ConnectionError("gone")):
            self.assertFalse(monke.ensure_xovi("rm2"))


class EyeErrorTest(unittest.TestCase):
    # live test 2026-09-30: Gemini 503 -> turn failed, nothing on the page;
    # Navy: eye state only, no ink note
    def test_error_shown_once_and_cleared_once(self):
        from unittest.mock import patch
        import monke
        monke._EYE["error"] = False
        wrote = []
        with patch.object(tablet, "run", lambda h, s, i=b"", timeout=30: wrote.append(s + i.decode()) or (0, b"")):
            monke.eye_error("rm2", True)
            monke.eye_error("rm2", True)
            monke.eye_error("rm2", False)
            monke.eye_error("rm2", False)
        self.assertEqual(len(wrote), 2)
        self.assertIn("watching error", wrote[0])
        self.assertIn("echo watching >", wrote[1])

    def test_eye_draws_the_error_state(self):
        qmd = (Path(__file__).resolve().parents[1] / "device/xovi/smriti-eye.qmd").read_text()
        self.assertIn('indexOf("error")', qmd)


if __name__ == "__main__":
    unittest.main()
