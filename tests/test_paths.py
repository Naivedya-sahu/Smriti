import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import paths  # noqa: E402


class PathsTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.env = patch.dict(os.environ, {"SMRITI_HOME": str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_user_state_lives_only_under_smriti_home(self):
        for p in (paths.user_cfg(), paths.ssh_dir(), paths.claude_home(), paths.credentials_file()):
            self.assertTrue(p.is_relative_to(self.home), p)
            self.assertNotIn("hermes", str(p).lower())

    def test_user_override_wins_and_defaults_survive(self):
        paths.set_user("endpoints", "chatgpt_url", "https://example.test/v1/responses")
        cfg = paths.load_config()
        self.assertEqual(cfg["endpoints"]["chatgpt_url"], "https://example.test/v1/responses")
        self.assertIn("brain", cfg)                      # shipped section kept

    def test_written_overlay_is_valid_toml_with_inline_tables(self):
        eps = [{"name": "tailnet", "host": "root@100.1.2.3", "screen_url": "https://100.1.2.3:2001"}]
        paths.set_user("capture", "endpoints", eps)
        paths.set_user("tex", "pdflatex", r"C:\TeX Live\bin\pdflatex.exe")
        got = tomllib.loads(paths.user_cfg().read_text(encoding="utf-8"))
        self.assertEqual(got["capture"]["endpoints"], eps)
        self.assertEqual(got["tex"]["pdflatex"], r"C:\TeX Live\bin\pdflatex.exe")


if __name__ == "__main__":
    unittest.main()
