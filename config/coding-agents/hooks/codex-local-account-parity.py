#!/usr/bin/env python3

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HELPER = Path(__file__).with_name("codex-local-account.py")
spec = importlib.util.spec_from_file_location("codex_local_account", HELPER)
account = importlib.util.module_from_spec(spec)
spec.loader.exec_module(account)
LANE = "codex-local-account-check"


class OwnerFromBrief(unittest.TestCase):
    def test_owner_table(self):
        home = Path.home() / ".agent-rails"
        cases = [
            ("Run agent-test-account --lane rails-verdicts from ~/x.", {}, "/tmp", ("lane", "rails-verdicts")),
            ("`agent-test-account --agent=prod-pass-a --json`", {}, "/tmp", ("agent", "prod-pass-a")),
            ("AGENT_TEST_ACCOUNT_OWNER=lane:qa agent-test-account --json", {}, "/tmp", ("lane", "qa")),
            ("agent-test-account --json", {"AGENT_TEST_ACCOUNT_OWNER": "agent:rails"}, "/tmp", ("agent", "rails")),
            ("agent-test-account --json", {"LANE_DIR": str(home / "lanes/mobile-qa")}, "/tmp", ("lane", "mobile-qa")),
            ("agent-test-account --json", {}, str(home / "agents/rails"), ("agent", "rails")),
            ("agent-test-account --lane a; agent-test-account --lane b", {}, "/tmp", None),
            ("agent-test-account --json", {}, "/tmp", None),
            ("run the check, no account", {"LANE_DIR": "/tmp/x"}, "/tmp", None),
        ]
        for brief, env, cwd, expected in cases:
            with self.subTest(brief=brief, env=env, cwd=cwd):
                self.assertEqual(account.owner(brief, env, cwd)[0], expected)


@unittest.skipUnless(shutil.which("agent-test-account"), "agent-test-account is not installed on this host")
class SandboxReadsHostPacket(unittest.TestCase):
    def test_sandboxed_seat_gets_the_prepared_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            lane = Path(tmp) / LANE
            lane.mkdir()
            brief = Path(tmp) / "brief.md"
            brief.write_text(f"Verdict seat. Check: agent-test-account --lane {LANE}; never sign in as Alex.\n")
            env = dict(os.environ, LANE_DIR=str(lane), CODEX_SANDBOX="seatbelt")
            env.pop("AGENT_TEST_ACCOUNT_OWNER", None)
            seat = ["agent-test-account", "--lane", LANE]
            before = subprocess.run(seat, env=env, cwd=tmp, capture_output=True, text=True, timeout=60)
            self.assertEqual(before.returncode, 2, before.stdout)
            self.assertIn("prepared state unavailable", before.stderr)
            prep = subprocess.run(
                [sys.executable, str(HELPER), "--prompt-file", str(brief)],
                env=env,
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=180,
            )
            self.assertEqual(prep.returncode, 0)
            self.assertEqual(prep.stdout.strip(), f"lane:{LANE}", prep.stderr)
            self.assertNotIn("http", prep.stdout + prep.stderr)
            after = subprocess.run(seat, env=env, cwd=tmp, capture_output=True, text=True, timeout=60)
            self.assertEqual(after.returncode, 0, after.stderr)
            self.assertRegex(after.stdout.strip(), r"^test-account: \S+$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
