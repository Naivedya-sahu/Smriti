"""Offline contract for the app shell: CLI switches, dependency resolution,
ssh identity, wireless links, tray stop order."""
# pyright: reportMissingImports=false

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import app
import deps
import link
import paths
import tray


class CliTest(unittest.TestCase):
    def test_switches_parse(self):
        p = app.parser()
        self.assertEqual(p.parse_args(["run"]).cmd, "run")
        self.assertEqual(p.parse_args(["doctor", "rm2"]).host, "rm2")
        a = p.parse_args(["link", "set-wifi", "172.20.10.2"])
        self.assertEqual((a.cmd, a.action, a.ip), ("link", "set-wifi", "172.20.10.2"))
        a = p.parse_args(["deps", "set", "pdflatex", "C:/x/pdflatex.exe"])
        self.assertEqual((a.action, a.tool, a.path), ("set", "pdflatex", "C:/x/pdflatex.exe"))
        self.assertEqual(p.parse_args(["pair", "100.64.0.10"]).tablet, "100.64.0.10")
        for c in ("tex-test", "paths", "version", "link-test", "tray"):
            self.assertEqual(p.parse_args([c]).cmd, c)
        self.assertIsNone(p.parse_args([]).cmd)

    def test_auth_dispatches_raw_argv(self):
        fake = MagicMock()
        fake.cli.return_value = 0
        with patch.dict(sys.modules, {"auth": fake}):
            self.assertEqual(app.main(["auth", "key", "set", "openai"]), 0)
        fake.cli.assert_called_once_with(["key", "set", "openai"])


class PdflatexOrderTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.bundled = self.d / "tinytex" / "bin" / "windows" / "pdflatex.exe"
        self.bundled.parent.mkdir(parents=True)
        self.bundled.write_text("")
        self.mine = self.d / "mine.exe"
        self.mine.write_text("")

    def resolve(self, cfg, env):
        with (patch.object(paths, "APP_ROOT", self.d), patch.dict(os.environ, env, clear=False)):
            if "SMRITI_PDFLATEX" not in env:
                os.environ.pop("SMRITI_PDFLATEX", None)
            return deps.pdflatex(cfg)

    def test_configured_beats_env_beats_bundled(self):
        cfg = {"tex": {"pdflatex": str(self.mine)}}
        self.assertEqual(self.resolve(cfg, {"SMRITI_PDFLATEX": "env.exe"}), (str(self.mine), "configured"))
        self.assertEqual(self.resolve({}, {"SMRITI_PDFLATEX": "env.exe"}), ("env.exe", "env SMRITI_PDFLATEX"))
        self.assertEqual(self.resolve({}, {}), (str(self.bundled), "bundled TinyTeX"))

    def test_frozen_never_falls_back_to_path(self):
        self.bundled.unlink()
        with (patch.object(paths, "FROZEN", True), patch.object(deps.shutil, "which", return_value="C:/tex/pdflatex.exe")):
            self.assertEqual(self.resolve({}, {}), ("", "missing"))

    def test_bundled_gets_private_texmf(self):
        with patch.object(paths, "APP_ROOT", self.d), patch.dict(os.environ, {"SMRITI_HOME": str(self.d / "home")}):
            env = deps.tex_env(str(self.bundled))
            self.assertTrue(env["TEXMFVAR"].startswith(str(self.d / "home")))
            self.assertIsNone(deps.tex_env(str(self.mine)))

    def test_set_tool_validates(self):
        with patch.dict(os.environ, {"SMRITI_HOME": str(self.d / "home")}):
            with self.assertRaises(ValueError):
                deps.set_tool("pdflatex", str(self.d / "nope.exe"))
            deps.set_tool("pdflatex", str(self.mine))
            self.assertEqual(paths.load_user()["tex"]["pdflatex"], str(self.mine.resolve()))


class SshArgvTest(unittest.TestCase):
    def test_dev_without_key_is_plain_ssh(self):
        home = Path(tempfile.mkdtemp())
        with (patch.object(paths, "FROZEN", False), patch.dict(os.environ, {}, clear=False),
              patch.object(deps, "key_path", return_value=home / "none")):
            os.environ.pop("SMRITI_HOME", None)
            self.assertEqual(deps.ssh_argv("rm2", "-o", "X=1", cfg={}), ["ssh", "-o", "X=1", "rm2"])

    def test_smriti_key_never_reads_user_ssh(self):
        home = Path(tempfile.mkdtemp())
        with patch.dict(os.environ, {"SMRITI_HOME": str(home)}):
            argv = deps.ssh_argv("root@1.2.3.4", "-n", cfg={})
        self.assertEqual(argv[-2:], ["-n", "root@1.2.3.4"])
        self.assertIn("none", argv[argv.index("-F") + 1])
        self.assertIn(f"IdentityFile={(home / 'ssh' / 'id_ed25519').as_posix()}", argv)
        self.assertNotIn("-i", argv)            # missing -i file => ssh falls back to ~/.ssh
        self.assertIn("IdentitiesOnly=yes", argv)
        kh = next(x for x in argv if x.startswith("UserKnownHostsFile="))
        self.assertIn((home / "ssh" / "known_hosts").as_posix(), kh)


class WirelessLinkTest(unittest.TestCase):
    CFG = {"capture": {"endpoints": [
        {"name": "tailnet", "host": "root@100.64.0.10", "screen_url": "https://100.64.0.10:2001"},
        {"name": "usb", "host": "rm2", "screen_url": "https://10.11.99.1:2001"}]}}

    def test_overlay_wifi_ip_and_env_wins(self):
        cfg = {"capture": dict(self.CFG["capture"], wifi_ip="172.20.10.2")}
        self.assertEqual(link.endpoints(cfg, {})[-1].host, "root@172.20.10.2")
        self.assertEqual(link.endpoints(cfg, {"SMRITI_RM2_WIFI_IP": "192.168.1.9"})[-1].host,
                         "root@192.168.1.9")

    def test_wireless_drops_usb_and_tailnet_override(self):
        cfg = link.wireless(self.CFG)
        cfg["capture"]["tailnet"] = "rm2-smriti.tail88dec1.ts.net"
        got = [(e.name, e.host) for e in link.endpoints(cfg, {})]
        self.assertEqual(got, [("tailnet", "root@rm2-smriti.tail88dec1.ts.net")])

    def test_validation(self):
        self.assertEqual(link.wifi_ip(" 172.20.10.2 "), "172.20.10.2")
        for bad in ("999.1.1.1", "fe80::1", "rm2; rm -rf /"):
            with self.assertRaises(ValueError):
                link.wifi_ip(bad)
        with self.assertRaises(ValueError):
            link.tailnet_endpoint("bad host;")


class TrayStopOrderTest(unittest.TestCase):
    def test_disarm_verify_then_terminate(self):
        calls = []
        proc = MagicMock()
        proc.poll.return_value = None
        proc.terminate.side_effect = lambda: calls.append("terminate")

        def run(argv, **kw):
            calls.append("disarm" if tray.DISARM in argv[-1] else "verify")
            return MagicMock(returncode=3, stdout=b"armed=0\ngms=inactive\n")
        d = tray.Daemon(run=run, sleep=lambda s: None)
        d.proc, d.host = proc, "root@100.64.0.10"
        with patch.object(d, "scan_log"):
            ok, _ = d.stop()
        self.assertTrue(ok)
        self.assertEqual(calls, ["disarm", "verify", "terminate"])

    def test_unverified_disarm_keeps_daemon(self):
        proc = MagicMock()
        proc.poll.return_value = None
        run = MagicMock(return_value=MagicMock(returncode=0, stdout=b"armed=1\ngms=active\n"))
        d = tray.Daemon(run=run, sleep=lambda s: None)
        d.proc, d.host = proc, "root@100.64.0.10"
        with patch.object(d, "scan_log"):
            ok, detail = d.stop()
        self.assertFalse(ok)
        self.assertIn("armed=1", detail)
        proc.terminate.assert_not_called()
        ok, _ = d.stop(force=True)                 # force quit still offered
        self.assertTrue(ok)
        proc.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class TrayMenuBuildsTest(unittest.TestCase):
    """2026-10-11: pystray rejects callbacks with >2 positional args; the
    installed tray died at startup with ValueError on `stop`."""

    def test_real_pystray_accepts_every_menu_action(self):
        import pystray
        import tray
        built = []
        with (patch.object(pystray.Icon, "run", lambda self, *a, **k: built.append(len(list(self.menu.items)))),
              patch.object(tray, "single_instance", return_value=True),
              patch.object(tray.Daemon, "start", return_value=True) as started):
            self.assertEqual(tray.main(), 0)
        self.assertTrue(built and built[0] > 5)
        started.assert_called_once()                    # tray launch = daemon starts


class SshFixOfferTest(unittest.TestCase):
    def test_rejected_key_offers_password_pairing_or_config(self):
        import app
        with (patch.object(app.sys.stdin, "isatty", return_value=True),
              patch.object(app, "cmd_pair", return_value=0) as paired):
            self.assertEqual(app.offer_ssh_fix("192.168.43.27", ask=lambda _: "1"), 0)
        paired.assert_called_once_with("192.168.43.27")
        import tray
        with (patch.object(app.sys.stdin, "isatty", return_value=True),
              patch.object(tray, "edit_config", return_value=Path("x.toml")) as edited):
            self.assertEqual(app.offer_ssh_fix("192.168.43.27", ask=lambda _: "2"), 0)
        edited.assert_called_once()

    def test_no_console_prints_the_fix_instead_of_prompting(self):
        import app
        with patch.object(app.sys.stdin, "isatty", return_value=False):
            self.assertEqual(app.offer_ssh_fix("192.168.43.27", ask=lambda _: self.fail("prompted")), 1)


class ScanTest(unittest.TestCase):
    def test_only_private_non_tailnet_non_usb_networks_are_scanned(self):
        import scan
        nets = scan.local_networks(["192.168.43.113", "100.64.0.10", "10.11.99.6",
                                    "8.8.8.8", "169.254.3.4", "172.20.10.3"])
        self.assertEqual([str(n) for n in nets], ["192.168.43.0/24", "172.20.10.0/24"])

    def test_a_hit_needs_ssh_proof_and_dropbear_is_preferred(self):
        import ipaddress
        import scan
        banners = {"192.168.1.5": "SSH-2.0-dropbear_2022.83", "192.168.1.9": "SSH-2.0-OpenSSH_9.6"}
        checked = []

        def check(ip):
            checked.append(ip)
            return ip == "192.168.1.5", "reMarkable 2.0"
        hits = scan.scan([ipaddress.ip_network("192.168.1.0/24")],
                         banner=lambda ip: banners.get(ip, ""), check=check)
        self.assertEqual(checked, ["192.168.1.5"])        # OpenSSH box never touched
        self.assertEqual([(h.ip, h.verified) for h in hits], [("192.168.1.5", True)])
        self.assertEqual(scan.find_tablet(nets=[ipaddress.ip_network("192.168.1.0/24")],
                                          banner=lambda ip: banners.get(ip, ""),
                                          check=lambda ip: (False, "ssh key not accepted")), None)
