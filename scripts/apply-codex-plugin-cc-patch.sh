#!/bin/sh



set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
plugin_dir=${CODEX_PLUGIN_CC_DIR:-"$HOME/.agent-lb/plugins/codex-plugin-cc"}
if [ ! -d "$plugin_dir/plugins/codex/scripts" ]; then
    echo "ERROR: plugin scripts not found in $plugin_dir" >&2
    exit 1
fi
for patch_file in "$repo_dir"/patches/codex-plugin-cc/*.patch; do


if git -C "$plugin_dir" apply --reverse --check "$patch_file" 2>/dev/null; then
    echo "Already patched: $plugin_dir ($(basename "$patch_file"))"
    continue
fi
if python3 - "$plugin_dir" "$patch_file" <<'PYTHON'
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1]).resolve()
lines = pathlib.Path(sys.argv[2]).read_text().splitlines(keepends=True)
source = None
checked = 0
i = 0
while i < len(lines):
    line = lines[i]
    if line.startswith("+++ b/"):
        target = (root / line[6:].strip()).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            sys.exit(1)
        source = target.read_text()
    elif line.startswith("@@ "):
        match = re.match(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
        if not match or source is None:
            sys.exit(1)
        old_left = int(match[1] or 1)
        new_left = int(match[2] or 1)
        content = []
        added = False
        while old_left or new_left:
            i += 1
            if i >= len(lines):
                sys.exit(1)
            body = lines[i]
            if body.startswith(" "):
                old_left -= 1
                new_left -= 1
                content.append(body[1:])
            elif body.startswith("+"):
                new_left -= 1
                content.append(body[1:])
                added = True
            else:
                sys.exit(1)
            if old_left < 0 or new_left < 0:
                sys.exit(1)
        if not added or "".join(content) not in source:
            sys.exit(1)
        checked += 1
    i += 1
sys.exit(0 if checked else 1)
PYTHON
then
    echo "Already patched (exact added hunks): $plugin_dir ($(basename "$patch_file"))"
    continue
fi
if [ "$(basename "$patch_file")" = repo-write-guard.patch ] &&
   ! git -C "$plugin_dir" apply --check "$patch_file" 2>/dev/null; then
    python3 - "$plugin_dir" "$patch_file" <<'PYTHON'
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
patch = pathlib.Path(sys.argv[2])
source = root / "plugins/codex/scripts/codex-companion.mjs"
original = source.read_text()
start = original.find("function enforceRepoWriteRule(")
end = original.find("\nfunction sleep(", start)
if start < 0 or end < 0 or "never codex-companion --write" not in original[start:end]:
    sys.exit("ERROR: unrecognized companion guard; refusing migration")
lines = patch.read_text().splitlines()
first = next(i for i, line in enumerate(lines) if line.startswith("+function enforceRepoWriteRule("))
last = next(i for i in range(first + 1, len(lines)) if lines[i] in ("+}", " }"))
function = "\n".join(line[1:] for line in lines[first:last + 1]) + "\n"
updated = original[:start] + function + original[end:]
updated = updated.replace('import { spawn } from "node:child_process";', 'import { spawn, spawnSync } from "node:child_process";', 1)
source.write_text(updated)
result = subprocess.run(["git", "-C", str(root), "apply", "--reverse", "--check", str(patch)])
if result.returncode:
    source.write_text(original)
    sys.exit("ERROR: companion guard migration failed validation; restored original")
print("Migrated companion cwd guard: " + str(source))
PYTHON
    continue
fi
if ! git -C "$plugin_dir" apply --check "$patch_file"; then
    echo "ERROR: codex-plugin-cc patch is incompatible in $plugin_dir; file/hunk diagnostics above. No files changed. Rebase the repo-owned patch on the updated plugin." >&2
    exit 1
fi
if ! git -C "$plugin_dir" apply "$patch_file"; then
    echo "ERROR: failed to apply codex-plugin-cc patch in $plugin_dir; see file/hunk diagnostics above." >&2
    exit 1
fi
echo "Patched: $plugin_dir ($(basename "$patch_file"))"
done
