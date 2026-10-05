"""Real uv/file-boundary proof: shared dependencies must not pin the builder's app.

A per-worktree venv or editable app install breaks reuse/import isolation here;
existing API tests do not exercise setup. No production-only test seam is needed.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
def test_worktrees_share_dependencies_but_import_their_own_app(tmp_path):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is required for the real setup integration")
    script = Path(__file__).resolve().parents[2] / "scripts/worktree-setup.sh"
    env = dict(os.environ, HOME=str(tmp_path / "home"), UV_PYTHON=sys.executable)
    targets = []
    for name in ("builder", "probe"):
        root = tmp_path / name
        (root / "scripts").mkdir(parents=True)
        (root / "app").mkdir()
        (root / "app/__init__.py").write_text(f"WORKTREE = {name!r}\n")
        (root / "pyproject.toml").write_text(
            '[project]\nname = "setup-probe"\nversion = "0.0.0"\nrequires-python = ">=3.13"\n'
        )
        shutil.copyfile(script, root / "scripts/worktree-setup.sh")
        subprocess.run([uv, "lock", "--offline"], cwd=root, env=env, check=True, timeout=30)
        subprocess.run(["sh", "scripts/worktree-setup.sh"], cwd=root, env=env, check=True, timeout=270)
        local = root / ".venv"
        assert local.is_symlink(), "setup must share rather than create a local environment"
        targets.append(local.resolve())
        result = subprocess.run(
            [str(local / "bin/python"), "-c", "import app; print(app.WORKTREE); print(app.__file__)"],
            cwd=root, env=env, check=True, capture_output=True, text=True, timeout=10,
        )
        assert result.stdout.splitlines() == [name, str(root / "app/__init__.py")]
    assert targets[0] == targets[1]
