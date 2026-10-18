#!/usr/bin/env python3

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
HELPER = HOOKS / "codex-local-account.py"


class AccountPreparation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.lanes = self.home / ".agent-rails/lanes"
        self.lane = self.lanes / "own"
        self.agent = self.home / ".agent-rails/agents/own"
        self.tools = self.root / "bin"
        for directory in (self.lane, self.agent, self.tools, self.home / ".local/bin"):
            directory.mkdir(parents=True)
        self.log = self.root / "backend-calls"
        self.script(
            "backend",
            """import json, os, sys
from pathlib import Path
with open(os.environ['BACKEND_LOG'], 'a') as stream:
    stream.write('called\\n')
command = sys.argv[5]
if command == 'ensure':
    print(json.dumps(dict(account_id='acct_parity', user_id='usr_parity',
        tenant_id='ten_parity', expires_at='2099-01-01T00:00:00Z',
        workspace_origin='https://example.invalid', created=True, renewed=False)))
elif command == 'web':
    print(json.dumps(dict(state='/synthetic/state.json')))
elif command == 'signin-link':
    print('https://example.invalid/synthetic')
else:
    sys.exit(9)
""",
        )
        installed = shutil.which("agent-test-account")
        self.assertIsNotNone(installed, "agent-test-account must be installed")
        (self.tools / "agent-test-account").symlink_to(Path(installed).resolve())
        self.env = dict(
            os.environ,
            HOME=str(self.home),
            LANE_BULLETIN_HOME=str(self.lanes),
            PATH=str(self.tools) + os.pathsep + os.environ["PATH"],
            AGENT_RAILS_PYTHON=str(self.tools / "backend"),
            AGENT_RAILS_REPO=str(self.root),
            BACKEND_LOG=str(self.log),
            LOCAL_SEAT_ADMITTED="1",
        )
        for key in (
            "LANE_DIR",
            "AGENT_TEST_ACCOUNT_OWNER",
            "CODEX_SANDBOX",
            "AGENT_TEST_ACCOUNT_PREPARED",
            "RAILS_TEST_ACCOUNTS_DIR",
            "RAILS_TEST_ENV_HOME",
        ):
            self.env.pop(key, None)
        self.brief = self.root / "brief.txt"

    def script(self, name, body):
        path = self.tools / name
        path.write_text("#!" + sys.executable + "\n" + body)
        path.chmod(0o755)
        return path

    def prepare(self, brief, cwd=None, **env):
        self.brief.write_text(brief)
        return subprocess.run(
            [sys.executable, str(HELPER), "--prompt-file", str(self.brief)],
            cwd=cwd or self.lane,
            env=dict(self.env, **env),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def refused(self, result, reason):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "", result.stderr)
        self.assertIn(reason, result.stderr)
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertFalse(self.log.exists(), "refused brief reached account provisioning")

    def test_brief_cannot_select_another_lanes_account(self):
        self.refused(
            self.prepare("agent-test-account --lane other", LANE_DIR=str(self.lane)), "differs from launching owner"
        )

    def test_lane_dir_precedes_inherited_owner(self):
        result = self.prepare("agent-test-account", LANE_DIR=str(self.lane), AGENT_TEST_ACCOUNT_OWNER="lane:other")
        self.assertEqual(result.stdout.strip(), "lane:own", result.stderr)
        self.assertTrue((self.lane / ".test-env/prepared-account.json").is_file())

    def test_multiple_owner_flags_in_one_command_are_refused(self):
        for flags in ("--lane own --lane other", "--lane own --agent own", "--lane own --lane own"):
            with self.subTest(flags=flags):
                self.refused(
                    self.prepare("agent-test-account " + flags, LANE_DIR=str(self.lane)), "more than one account owner"
                )

    def test_missing_launch_owner_cannot_be_supplied_by_brief_or_inherited_owner(self):
        self.refused(
            self.prepare("agent-test-account --lane own", cwd=self.root, AGENT_TEST_ACCOUNT_OWNER="lane:own"),
            "no launching owner",
        )

    def launch(self, kind):
        self.script(
            "node",
            """import os, subprocess, sys, time
if '--dry-run' in sys.argv:
    sys.exit(0)
env = dict(os.environ, CODEX_SANDBOX='seatbelt')
if os.environ['CHECK_KIND'] == 'lane' and env.get('LANE_DIR') != os.getcwd():
    print('resolved LANE_DIR was not exported', file=sys.stderr)
    sys.exit(3)
result = subprocess.run(['agent-test-account', '--' + os.environ['CHECK_KIND'], 'own'],
                        env=env, capture_output=True, text=True)
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
time.sleep(10)
sys.exit(result.returncode)
""",
        )
        focus = self.script(
            "focus",
            """import json, sys
print(json.dumps(dict(front='parity', cursor=[0, 0], rows=[])))
""",
        )
        (self.home / ".local/bin/desktop-guard-focus").symlink_to(focus)
        self.brief.write_text("Run agent-test-account --" + kind + " own\n")
        return subprocess.run(
            ["bash", str(HOOKS / "codex-local.sh"), "task", "--prompt-file", str(self.brief)],
            cwd=self.lane if kind == "lane" else self.agent,
            env=dict(self.env, CHECK_KIND=kind),
            capture_output=True,
            text=True,
            timeout=90,
        )

    def test_launcher_exports_resolved_lane_with_lane_dir_unset(self):
        result = self.launch("lane")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "test-account: acct_parity")
        self.assertTrue((self.lane / ".test-env/prepared-account.json").is_file())

    def test_launcher_agent_owner_packet_is_readable_in_sandbox(self):
        result = self.launch("agent")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "test-account: acct_parity")
        self.assertTrue((self.agent / ".test-env/prepared-account.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
