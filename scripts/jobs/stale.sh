#!/usr/bin/env bash
set -euo pipefail
export GH_APP=on GH_APP_FALLBACK=off
python3 - "$@" <<'PYTHON'
import argparse
import datetime as dt
import json
import subprocess
from urllib.parse import urlencode

parser = argparse.ArgumentParser(description="Mark and close inactive issues and PRs")
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
repo = "repos/aneym/agent-lb"
now = dt.datetime.now(dt.timezone.utc)
operations = 0
result = {"staled-issues-prs": [], "closed-issues-prs": [], "unstaled": [], "dry_run": args.dry_run}
messages = {'stale-issue-message': 'This issue has been automatically marked as **stale** because it has been open for 7 days with no reporter activity since `needs-info` was applied.\n\nIt will be closed in 7 more days unless additional information is provided.\n\nIf this is still relevant, please:\n- Reply with the requested details, or\n- Remove the `stale` label to keep it open\n\nThanks for the report 🙏\n', 'close-issue-message': 'Closing as **insufficient information** — no response from reporter within the 14-day `needs-info` window.\n\nPlease feel free to reopen with the requested details. 🙏\n', 'stale-pr-message': 'This pull request has been automatically marked as **stale** because it has had no activity for 7 days.\n\nIt will be closed in 23 more days unless there is new activity.\n\nIf this is still relevant, please:\n- Rebase or push an update if the branch drifted\n- Address pending review feedback if there is any\n- Leave a short comment confirming it is still being worked on\n\nThanks for the contribution 🙏\n', 'close-pr-message': 'Closing this pull request because it has had no activity for about 30 days after the stale warning.\n\nPlease feel free to reopen it, or open a fresh PR rebased on current `main`, if the change is still relevant. 🙏\n'}


class BudgetExhausted(Exception):
    pass


def api(path, method="GET", data=None):
    global operations
    if operations >= 100:
        raise BudgetExhausted
    operations += 1
    if method != "GET" and args.dry_run:
        print(json.dumps({"method": method, "path": path, "data": data}))
        return None
    command = ["gh", "api", f"{repo}/{path}", "--method", method]
    if data is not None:
        command.extend(["--input", "-"])
    response = subprocess.run(command, input=json.dumps(data) if data is not None else None,
                              capture_output=True, text=True, check=True)
    return json.loads(response.stdout) if response.stdout.strip() else None


def pages(path):
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        batch = api(f"{path}{separator}per_page=100&page={page}")
        yield from batch
        if len(batch) < 100:
            return
        page += 1


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def process(issue):
    is_pr = "pull_request" in issue
    kind = "pr" if is_pr else "issue"
    labels = {label["name"].strip().lower() for label in issue["labels"]}
    exempt = {"pinned", "security", "in-progress"}
    if is_pr:
        exempt.add("autorelease: pending")
    if issue["locked"] or labels & exempt or (not is_pr and "needs-info" not in labels):
        return
    path = f"issues/{issue['number']}"
    updated = timestamp(issue["updated_at"])
    if "stale" not in labels:
        if (now - updated).total_seconds() <= 7 * 86400:
            return
        if operations + 2 > 100:
            raise BudgetExhausted
        api(f"{path}/comments", "POST", {"body": messages[f"stale-{kind}-message"]})
        api(f"{path}/labels", "POST", {"labels": ["stale"]})
        result["staled-issues-prs"].append(issue["number"])
        return
    events = list(pages(f"{path}/events"))
    marked = next((timestamp(event["created_at"]) for event in reversed(events)
                   if event["event"] == "labeled" and
                   event.get("label", {}).get("name", "").strip().lower() == "stale"), updated)
    comments = list(pages(f"{path}/comments?{urlencode({'since': marked.isoformat()})}"))
    human_comments = any(comment.get("user", {}).get("type") == "User" and
                         (comment.get("body") or "").lower() != messages[f"stale-{kind}-message"].lower()
                         for comment in comments)
    changed = (updated - marked).total_seconds() > 15
    recent_events = [event for event in events if timestamp(event["created_at"]) >= marked]
    if changed and recent_events and all(event["event"] == "labeled" and
                                        event.get("label", {}).get("name", "").strip().lower() == "stale"
                                        for event in recent_events):
        changed = False
    if changed or human_comments:
        api(f"{path}/labels/stale", "DELETE")
        result["unstaled"].append(issue["number"])
    elif (now - updated).total_seconds() > (23 if is_pr else 7) * 86400:
        if operations + 2 > 100:
            raise BudgetExhausted
        api(f"{path}/comments", "POST", {"body": messages[f"close-{kind}-message"]})
        api(path, "PATCH", {"state": "closed", "state_reason": "not_planned"})
        result["closed-issues-prs"].append(issue["number"])


try:
    for issue in pages("issues?state=open&sort=created&direction=asc"):
        process(issue)
except BudgetExhausted:
    result["budget_exhausted"] = True
result["operations"] = operations
print(json.dumps(result))
PYTHON
