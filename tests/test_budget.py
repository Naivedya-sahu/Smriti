"""Claude budget tiers (Navy 2026-09-30): counted from daemon start, sonnet
until ~32 % of the Pro 5 h window, haiku for ~22 % more, then the turn
fails visibly (no local model, no Gemini; ChatGPT is not capped). The CLI
reports no percent, only status + reset time, so spend is API-equivalent $
over a calibrated `window_usd`."""
# pyright: reportMissingImports=false

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import monke
import oracle

CFG = {"window_usd": 10.0, "sonnet_pct": 32, "haiku_pct": 22}


class TierTest(unittest.TestCase):
    def setUp(self):
        monke.BUDGET.update(spent=0.0, resets_at=0, status="allowed")

    def tier(self, model="sonnet"):
        with patch.dict(monke.CLAUDE_CFG, CFG):
            return monke.claude_tier(model)

    def test_fresh_daemon_uses_requested_model(self):
        self.assertEqual(self.tier(), "sonnet")

    def test_past_sonnet_share_drops_to_haiku(self):
        monke.BUDGET["spent"] = 3.3                  # 33 %
        self.assertEqual(self.tier(), "haiku")

    def test_past_both_shares_stops_claude(self):
        monke.BUDGET["spent"] = 5.5                  # 55 %
        self.assertIsNone(self.tier())

    def test_cli_warning_drops_to_haiku(self):
        # live 2026-10-10: allowed_warning is still allowed — cheapest Claude.
        monke.BUDGET["status"] = "allowed_warning"
        self.assertEqual(self.tier(), "haiku")

    def test_cli_rejected_stops_claude(self):
        monke.BUDGET["status"] = "rejected"
        self.assertIsNone(self.tier())

    def test_window_reset_clears_spend(self):
        monke.BUDGET.update(spent=9.0, resets_at=time.time() - 1, status="rejected")
        self.assertEqual(self.tier(), "sonnet")
        self.assertEqual(monke.BUDGET["spent"], 0.0)

    def test_cloud_chat_calls_the_tier_model_and_counts_spend(self):
        monke.BUDGET["spent"] = 3.3
        seen = {}

        def fake(messages, cfg):
            seen["model"] = cfg["model"]
            oracle.LAST_USAGE.update(model=cfg["model"], cost_usd=0.02)
            return "ok"
        with (patch.dict(monke.CLAUDE_CFG, CFG),
              patch.object(oracle, "_claude_compat", fake),
              patch.object(monke, "usage_add", lambda ses, host, rec, role: monke._spend(rec)),
              patch.object(monke, "publish_usage", lambda *a, **k: None)):
            out = monke.cloud_chat("sonnet", None, "rm2", "write")([])
        self.assertEqual((out, seen["model"]), ("ok", "haiku"))
        self.assertAlmostEqual(monke.BUDGET["spent"], 3.32)

    def test_over_budget_never_calls_claude(self):
        monke.BUDGET["spent"] = 9.0
        with (patch.dict(monke.CLAUDE_CFG, CFG),
              patch.object(oracle, "_claude_compat", side_effect=AssertionError("claude")),
              patch.object(monke, "publish_usage", lambda *a, **k: None)):
            with self.assertRaises(RuntimeError):
                monke.cloud_chat("sonnet", None, "rm2", "write")([])

    def test_claude_failure_raises(self):
        with (patch.dict(monke.CLAUDE_CFG, CFG),
              patch.object(oracle, "_claude_compat", side_effect=RuntimeError("rc=1")),
              patch.object(monke, "publish_usage", lambda *a, **k: None)):
            with self.assertRaises(RuntimeError):
                monke.cloud_chat("sonnet", None, "rm2", "write")([])


class CostTest(unittest.TestCase):
    # the reader stops the stream early: the CLI never reports its cost
    def test_estimate_from_tokens(self):
        self.assertAlmostEqual(oracle.estimate_cost("sonnet", 1_000_000, 0), 3.0)
        self.assertAlmostEqual(oracle.estimate_cost("haiku", 0, 1_000_000), 5.0)


if __name__ == "__main__":
    unittest.main()
