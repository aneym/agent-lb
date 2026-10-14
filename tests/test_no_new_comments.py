import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'bin/no-new-comments'
if not CHECKER.exists():
    CHECKER = ROOT / 'scripts/checks/no-new-comments'


class CommentDiffCheck(unittest.TestCase):
    def check_diff(self, name, content, expected):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            env = dict(os.environ, GIT_AUTHOR_NAME='Checker', GIT_AUTHOR_EMAIL='checker@example.invalid',
                       GIT_COMMITTER_NAME='Checker', GIT_COMMITTER_EMAIL='checker@example.invalid')
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args], env=env, stderr=subprocess.DEVNULL)
            git('init', '-q')
            git('commit', '--allow-empty', '-qm', 'base')
            base = git('rev-parse', 'HEAD').decode().strip()
            (repo / name).write_text(content)
            git('add', name)
            git('commit', '-qm', 'candidate')
            result = subprocess.run(['python3', str(CHECKER), '--repo', directory, base, 'HEAD'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            if expected:
                self.assertIn(name + ':', result.stderr)

    def test_rejected_comments(self):
        for name, content in [('a.py', '# prose\nx = 1\n'), ('a.ts', 'let x = 1; // prose\n'),
                              ('a.tsx', '{/* prose */}\n'), ('a.js', '/* prose */\n'),
                              ('a.mjs', '// prose\n'), ('a.swift', '// prose\n'),
                              ('a.sh', '# prose\n'), ('a.go', '// prose\n'), ('a.rs', '// prose\n'),
                              ('a.sql', '-- prose\n'), ('a.css', '/* prose */\n'),
                              ('a.yaml', 'x: 1 # prose\n'), ('a.toml', '# prose\n'),
                              ('a.html', '<!-- prose -->\n'), ('a.py', '"""Prose docs."""\nx = 1\n')]:
            with self.subTest(name=name, content=content):
                self.check_diff(name, content, 1)

    def test_required_directives(self):
        for name, content in [('a.sh', '#!/bin/sh\n'), ('a.py', '# -*- coding: utf-8 -*-\n'),
                              ('a.py', 'x = 1 # type: ignore\n'), ('a.py', 'x = 1 # noqa\n'),
                              ('a.py', '# pragma: no cover\n'), ('a.ts', '// eslint-disable-next-line\n'),
                              ('a.ts', '// @ts-expect-error\n'), ('a.ts', '// @ts-ignore\n'),
                              ('a.swift', '// swiftlint:disable all\n'), ('a.js', '// @flow\n'),
                              ('a.js', '// prettier-ignore\n'), ('a.js', '// biome-ignore lint/all\n'),
                              ('a.swift', '// swift-tools-version: 5.9\n'), ('a.go', '// Code generated. DO NOT EDIT.\n'),
                              ('a.rs', '// SPDX-License-Identifier: MIT\n')]:
            with self.subTest(name=name, content=content):
                self.check_diff(name, content, 0)

    def test_strings_urls_and_existing_comments(self):
        for name, content in [('a.py', 'x = "# string https://example.invalid"\n'),
                              ('a.ts', 'const x = "https://example.invalid/#part";\n'),
                              ('a.js', 'const x = `// literal`; const y = /https?:\\/\\//;\n'),
                              ('a.yaml', 'x: "# string"\n'), ('a.yaml', 'url: https://example.invalid/#part\n'),
                              ('a.sql', 'select $$--string$$;\n'), ('a.rs', 'let x = r#"// string"#;\n'), ('a.sql', "select '--string';\n"),
                              ('a.rs', 'let x = "// string";\n'), ('a.html', '<a href="https://example.invalid">x</a>\n')]:
            with self.subTest(name=name):
                self.check_diff(name, content, 0)


if __name__ == '__main__':
    unittest.main()
