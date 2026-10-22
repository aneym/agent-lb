import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def run_parity(source: Path, home: Path, registry: dict[str, Any], overlays: dict[Path, Any]) -> tuple[bool, str]:
    try:
        wake = subprocess.run(
            [
                sys.executable,
                str(source / "hooks" / "seat-run-wake-parity.py"),
                "--dispatcher",
                str(source / "hooks/hook-dispatch.py"),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"seat-run wake fixture did not run: {exc.__class__.__name__}"
    if wake.returncode != 0:
        return False, f"seat-run wake fixture failed: {wake.stderr.strip()[-400:]}"
    report = home / ".claude/hooks/dispatch/parity-last.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hook-dispatch-stage-") as stage:
        staged = Path(stage) / "registry.json"
        staged.write_text(json.dumps(registry, indent=2) + "\n")
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(source / "hooks" / "hook-dispatch-parity.py"),
                    "--home",
                    str(home),
                    "--registry",
                    str(staged),
                    "--dispatcher",
                    str(source / "hooks/hook-dispatch.py"),
                    "--out",
                    str(report),
                    "--workdir",
                    str(Path(tempfile.gettempdir()) / "hook-dispatch-parity"),
                    *[
                        arg
                        for target, template in overlays.items()
                        for arg in ("--overlay", f"{target.relative_to(home)}={template}")
                    ],
                    "--lock-held",
                    "--no-process-count",
                ],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"parity fixture did not run: {exc.__class__.__name__}"
    summary = (result.stdout.strip().splitlines() or [""])[-1]
    if result.returncode != 0:
        return False, f"{summary or result.stderr.strip()[-400:]} (exit {result.returncode}; report {report})"
    return True, f"{summary} (report {report})"
