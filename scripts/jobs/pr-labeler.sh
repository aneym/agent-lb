#!/usr/bin/env bash
set -euo pipefail
export GH_APP=on GH_APP_FALLBACK=off
uv run --no-cache --no-project --with pyyaml==6.0.3 python - "$@" <<'PYTHON'
import argparse
import fnmatch
import json
from pathlib import Path
import subprocess

import yaml

parser = argparse.ArgumentParser(description="Apply configured path labels to all open PRs")
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
config = yaml.safe_load(Path(".github/labeler.yml").read_text())
rules = {}
for label, conditions in config.items():
    patterns = []
    for condition in conditions:
        if set(condition) != {"changed-files"}:
            raise ValueError(f"Unsupported label condition for {label}")
        for match in condition["changed-files"]:
            if set(match) != {"any-glob-to-any-file"}:
                raise ValueError(f"Unsupported file matcher for {label}")
            patterns.extend(match["any-glob-to-any-file"])
    rules[label] = patterns


def api(path, data=None):
    command = ["gh", "api", f"repos/aneym/agent-lb/{path}"]
    if data is not None:
        command.extend(["--method", "POST", "--input", "-"])
    response = subprocess.run(command, input=json.dumps(data) if data is not None else None,
                              capture_output=True, text=True, check=True)
    return json.loads(response.stdout)


def pages(path):
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        batch = api(f"{path}{separator}per_page=100&page={page}")
        yield from batch
        if len(batch) < 100:
            return
        page += 1


result = {"dry_run": args.dry_run, "scanned": 0, "labels_added": []}
for pr in pages("pulls?state=open"):
    result["scanned"] += 1
    files = [entry["filename"] for entry in pages(f"pulls/{pr['number']}/files")]
    existing = {label["name"] for label in pr["labels"]}
    labels = [label for label, patterns in rules.items() if label not in existing and
              any(fnmatch.fnmatchcase(file, pattern) for file in files for pattern in patterns)]
    if labels:
        if not args.dry_run:
            api(f"issues/{pr['number']}/labels", {"labels": labels})
        result["labels_added"].append({"pr": pr["number"], "labels": labels})
print(json.dumps(result))
PYTHON
