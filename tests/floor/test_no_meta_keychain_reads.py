from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATTERN = re.compile(r"find-generic-password|SecItemCopyMatching")


def test_no_tracked_code_reads_meta_keychain_items() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "app", "clients", "scripts", "bin", "config"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    offenders = []
    for name in tracked:
        try:
            text = (ROOT / name).read_text(errors="ignore")
        except OSError:
            continue
        if "ai.meta" in text and PATTERN.search(text):
            offenders.append(name)
    assert offenders == []
