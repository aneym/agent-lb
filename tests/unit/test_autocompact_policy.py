import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class AutocompactInstall(unittest.TestCase):
    def test_settings_only_install_preserves_other_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            repo = fixture / 'repo'
            shutil.copytree(ROOT / 'config/coding-agents', repo / 'config/coding-agents')
            (repo / 'clients').mkdir()
            shutil.copy2(ROOT / 'clients/route', repo / 'clients/route')
            for args in (['init', '-b', 'main'], ['add', '.'],
                         ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'],
                         ['remote', 'add', 'origin', str(repo)]):
                subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
            home = fixture / 'home'
            settings = home / '.claude/settings.json'
            settings.parent.mkdir(parents=True)
            original = '{\n "permissions" : {"allow": ["fixture"]},\n "hooks": {},\n "env": {"KEEP":"yes"},\n "autoCompactWindow" : 1, "number": 1.00\n}\n'
            settings.write_text(original)
            table_path = repo / 'config/coding-agents/routing-table.json'
            table = json.loads(table_path.read_text())
            self.assertEqual(table['autocompact_window'], {'tab': 300000, 'lane': 300000, 'box': 300000})
            window = 412000
            table['autocompact_window']['tab'] = window
            table_path.write_text(json.dumps(table))
            result = subprocess.run([shutil.which('python3.12') or sys.executable, str(repo / 'config/coding-agents/install-policy.py'),
                                     '--home', str(home), '--autocompact-only', '--force',
                                     '--reason', 'fixture'], capture_output=True, text=True,
                                    env=os.environ | {'HOME': str(home), 'AGENT_LB_URL': 'http://127.0.0.1:1'}, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(settings.read_text(), original.replace('"autoCompactWindow" : 1,',
                                                                    f'"autoCompactWindow" : {window},'))
            backups = list((home / '.agent-lb/config-checkpoints/coding-agents').glob('*/.claude/settings.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)
            print(f'fixture autoCompactWindow={window}; other settings bytes unchanged')
