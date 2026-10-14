

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

AGENTS = Path(__file__).resolve().parents[2] / "config" / "coding-agents" / "agents"

SHELLS = ["/bin/bash", "bash", "zsh"]


def seats_naming_verify_slot() -> list[Path]:
    seats = sorted(p for p in AGENTS.glob("*.md") if "verify-slot" in p.read_text())
    assert seats, "no seat names verify-slot; drop this test with the load cap"
    return seats


def launch_lines(seat: Path) -> tuple[str, str]:

    blocks = re.findall(r"```sh\n(.*?)```", seat.read_text(), re.S)
    lines = [ln for b in blocks for ln in b.splitlines()]
    vs = [ln for ln in lines if ln.startswith("vs=(")]
    use = [m.group(1) for ln in lines if (m := re.search(r"(\S*\$\{vs\[@\]\S*) node ", ln))]
    assert len(vs) == 1 and len(use) == 1, f"{seat.name}: need one vs= line and one launch using it"
    return vs[0], use[0]


@pytest.mark.parametrize("seat", seats_naming_verify_slot(), ids=lambda p: p.stem)
def test_no_placeholder_prefix(seat: Path) -> None:

    assert not re.search(r"\[(?!\s)[^\]\n]*(verify-slot|prefix)[^\]\n]*\]", seat.read_text()), (
        f"{seat.name} has a bracketed verify-slot placeholder; give the exact vs= argv line"
    )


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("installed", [True, False], ids=["installed", "missing"])
@pytest.mark.parametrize("seat", seats_naming_verify_slot(), ids=lambda p: p.stem)
def test_vs_line_yields_separate_argv(seat: Path, installed: bool, shell: str, tmp_path: Path) -> None:
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} not installed")
    vs_line, expansion = launch_lines(seat)
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    if installed:
        slot = home / ".local" / "bin" / "verify-slot"
        slot.write_text("#!/bin/sh\nexit 0\n")
        slot.chmod(0o755)

    script = f'{vs_line}\nfor a in {expansion}; do printf "%s\\n" "$a"; done\n'
    out = subprocess.run(
        [exe, "-u", "-c", script], env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    if installed:
        assert out == [str(slot), seat.stem, "--"]
        assert Path(out[0]).is_file()
    else:
        assert out == []
