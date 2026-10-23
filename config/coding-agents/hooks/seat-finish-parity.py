#!/usr/bin/env python3

import importlib.util
import os
import subprocess
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("seat_finish", Path(__file__).with_name("seat-finish.py"))
finish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finish)


class FinishContract(unittest.TestCase):
    def test_hook_preview(self):
        hook = Path(__file__).with_name("codex-local.sh")
        env = dict(os.environ, LANE_DIR=str(Path.home() / ".agent-rails/lanes/mobile-qa"))
        result = subprocess.run(["bash", str(hook), "--dry-run", "task"], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        template = Path(__file__).resolve().parent.parent.joinpath("FINISH.md").read_text()
        self.assertEqual(finish.render("Lane: mobile-qa", env), template)
        self.assertIn(template, result.stdout)
        self.assertIn("host=studio background=false", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
