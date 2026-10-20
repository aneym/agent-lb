#!/usr/bin/env python3

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("seat_finish", Path(__file__).with_name("seat-finish.py"))
finish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finish)


class LaneBoundary(unittest.TestCase):
    def test_owned_lane_boundary(self):
        root = Path.home() / ".agent-rails/lanes"
        own = root / "mobile-qa"
        for brief, env in [
            ("", {"LANE_DIR": str(own)}),
            ("Lane: mobile-qa\n", {}),
            ("Lane mobile-qa, lead w5H:p16A.\n", {}),
        ]:
            with self.subTest(brief=brief, env=env):
                lane = finish.own_lane(brief, env)
                self.assertTrue(finish.allowed_lane_path(own / "walker.py", lane))
                for forbidden in [
                    root / "other/walker.py",
                    own / "../other/walker.py",
                    root / "mobile-qa-sibling/walker.py",
                    root.parent / "factory/state.json",
                ]:
                    self.assertFalse(finish.allowed_lane_path(forbidden, lane))
                rendered = finish.render(brief, env)
                self.assertIn(f"Calling lane directory: `{own.resolve()}`", rendered)
                self.assertIn("lanes other than your own", rendered)
                self.assertNotIn("anything under ~/.agent-rails/lanes/", rendered)

    def test_unresolved_and_invalid_lane(self):
        for env in [{}, {"LANE_DIR": "/tmp/repos"}, {"LANE_DIR": "~/.agent-rails/lanes/../factory"}]:
            self.assertIsNone(finish.own_lane("", env))
            self.assertIn("No lane-directory edits are allowed", finish.render("", env))

    def test_hook_preview(self):
        hook = Path(__file__).resolve().parents[3] / "clients/codex-seat"
        env = dict(os.environ, LANE_DIR=str(Path.home() / ".agent-rails/lanes/mobile-qa"))
        env["ROUTE_TABLE"] = str(hook.parent.parent / "config/coding-agents/routing-table.json")
        with tempfile.TemporaryDirectory() as directory:
            brief = Path(directory) / "brief"
            brief.write_text("Lane: mobile-qa\n")
            result = subprocess.run(
                [
                    "python3",
                    str(hook),
                    "run",
                    "--seat",
                    "codex-verifier-local",
                    "--name",
                    "finish-parity",
                    "--prompt-file",
                    str(brief),
                    "--dry-run",
                ],
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(result.stdout)
            job = Path(row["argv"][row["argv"].index("-o") + 1]).parent
            self.assertIn("Calling lane directory:", (job / "prompt.md").read_text())
            self.assertEqual(row["place"], "local")


if __name__ == "__main__":
    unittest.main(verbosity=2)
