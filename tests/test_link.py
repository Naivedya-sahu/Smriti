"""Offline contract for rM2 multi-link selection."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import link
import monke
from unittest.mock import patch


class EndpointsTest(unittest.TestCase):
    CFG = {
        "capture": {
            "endpoints": [
                {"name": "tailnet", "host": "root@100.64.0.10",
                 "screen_url": "https://100.64.0.10:2001"},
                {"name": "usb", "host": "rm2", "screen_url": "https://10.11.99.1:2001"},
            ]
        }
    }

    def test_declared_endpoints_preserve_priority_and_screen_pairing(self):
        got = link.endpoints(self.CFG, {})
        self.assertEqual([(x.name, x.host, x.screen_url) for x in got], [
            ("tailnet", "root@100.64.0.10", "https://100.64.0.10:2001"),
            ("usb", "rm2", "https://10.11.99.1:2001"),
        ])

    def test_wifi_environment_adds_a_complete_third_endpoint(self):
        got = link.endpoints(self.CFG, {"SMRITI_RM2_WIFI_IP": "192.168.43.27"})
        self.assertEqual((got[-1].name, got[-1].host, got[-1].screen_url),
                         ("wifi", "root@192.168.43.27", "https://192.168.43.27:2001"))

    def test_blank_or_invalid_wifi_environment_is_ignored(self):
        for value in ("", "not-an-ip", "192.168.43.999"):
            self.assertEqual(len(link.endpoints(self.CFG, {"SMRITI_RM2_WIFI_IP": value})), 2)
    def test_resolve_activates_selected_host_and_screen(self):
        cfg = {"capture": {"endpoints": self.CFG["capture"]["endpoints"]}, "screen": {}}
        selected = link.resolve(cfg, {}, probe=lambda ep: ep.name == "usb")
        self.assertEqual(selected.name, "usb")
        self.assertEqual(cfg["capture"]["host"], "rm2")
        self.assertEqual(cfg["screen"]["url"], "https://10.11.99.1:2001")
    def test_link_report_probes_every_candidate(self):
        got = link.report(self.CFG, {}, probe=lambda ep: ep.name != "usb")
        self.assertEqual([(ep.name, ok) for ep, ok in got], [("tailnet", True), ("usb", False)])


class MonkeLinkTest(unittest.TestCase):
    def test_monke_uses_selected_endpoint_for_capture(self):
        cfg = {"capture": {"endpoints": [{"name": "tailnet", "host": "root@100.64.0.10",
                                             "screen_url": "https://100.64.0.10:2001"}]}, "screen": {}}
        with patch.object(link, "reachable", return_value=True):
            self.assertEqual(monke._resolve_link(cfg), "root@100.64.0.10")
        self.assertEqual(cfg["capture"]["host"], "root@100.64.0.10")
        self.assertEqual(cfg["screen"]["url"], "https://100.64.0.10:2001")
    def test_link_test_reports_each_route(self):
        from io import StringIO
        import tempfile
        cfg = Path(tempfile.mkdtemp()) / "config.toml"
        cfg.write_text('[capture]\nhost = "rm2"\n')
        rows = [(link.Endpoint("tailnet", "root@100.64.0.10", "https://100.64.0.10:2001"), True),
                (link.Endpoint("usb", "rm2", "https://10.11.99.1:2001"), False)]
        with (patch.object(monke, "REPO_CFG", cfg),
              patch.object(link, "report", return_value=rows),
              patch("sys.stdout", StringIO()) as out):
            self.assertTrue(monke.link_test())
        self.assertIn("PASS tailnet", out.getvalue())
        self.assertIn("FAIL usb", out.getvalue())


class SelectTest(unittest.TestCase):
    def test_select_stops_at_the_first_healthy_link(self):
        eps = link.endpoints(EndpointsTest.CFG, {})
        seen = []

        def probe(ep):
            seen.append(ep.name)
            return ep.name == "usb"

        got = link.select(eps, probe)
        self.assertEqual(got.name, "usb")
        self.assertEqual(seen, ["tailnet", "usb"])

    def test_select_returns_none_when_every_link_is_down(self):
        eps = link.endpoints(EndpointsTest.CFG, {})
        self.assertIsNone(link.select(eps, lambda ep: False))


if __name__ == "__main__":
    unittest.main()
