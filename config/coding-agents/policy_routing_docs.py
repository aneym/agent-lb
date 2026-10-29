from __future__ import annotations

import hashlib
import json
from pathlib import Path

from policy_documents import read_text  # type: ignore[missing-import]
from policy_layout import CORE_SOURCE, MODELS_FULL, MODELS_MARKER, POLICY_DIR  # type: ignore[missing-import]


def routing_changes(source: Path, home: Path, uninstall: bool) -> dict[Path, str | None]:
    changes: dict[Path, str | None] = {}
    models_path = home / ".claude/rules/models.md"
    models_text = models_path.read_bytes().decode("utf-8") if models_path.exists() else ""
    full_path = home / MODELS_FULL
    full_disk = full_path.read_bytes().decode("utf-8") if full_path.exists() else ""
    if uninstall:
        if models_text.startswith(MODELS_MARKER):
            changes[models_path] = None
        if full_disk.startswith(MODELS_MARKER):
            changes[full_path] = None
    else:
        section_paths = sorted((source / "routing").glob("*.md"))
        if not section_paths:
            raise SystemExit("error: missing canonical routing sections")
        for section in section_paths:
            target = home / ".claude/docs/models" / section.name
            desired = section.read_text()
            if read_text(target) != desired:
                changes[target] = desired
        core = (source / CORE_SOURCE).read_text()
        desired_models = MODELS_MARKER + " from routing/core.md; generated, do not edit. -->\n\n" + core
        if models_text and not models_text.startswith(MODELS_MARKER):
            backup = (
                home
                / ".claude/ledgers"
                / ("models-handkept-" + hashlib.sha256(models_text.encode()).hexdigest()[:12] + ".md")
            )
            if not backup.exists():
                changes[backup] = models_text
        if desired_models != models_text:
            changes[models_path] = desired_models
        if full_disk.startswith(MODELS_MARKER):
            changes[full_path] = None
        old_policy = home / POLICY_DIR / "ROUTING.md"
        pointer = "# Routing reference\n\nRead `~/.claude/docs/models/README.md` and its named sections. This compatibility pointer contains no duplicate policy.\n"
        if not old_policy.exists() and not old_policy.is_symlink():
            changes[old_policy] = pointer
        elif old_policy.is_file() and not old_policy.is_symlink():
            old_text = old_policy.read_text()
            legacy = source / "routing-legacy-sha256.json"
            known = json.loads(legacy.read_text()) if legacy.is_file() else []
            if old_text == pointer:
                pass
            elif hashlib.sha256(old_text.encode()).hexdigest() in known:
                changes[old_policy] = pointer
            else:
                print(f"PRESERVE {old_policy}: modified or unrecognized legacy routing document")
    return changes
