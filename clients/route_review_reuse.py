from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from route_run_checkout import git_value  # pyrefly: ignore [missing-import]
from route_run_jobs import run_path  # pyrefly: ignore [missing-import]


def review_key(args, row: dict, cwd: Path, brief: str) -> dict | None:
    path = getattr(args, "review_key", None)
    if not path or row.get("class") != "verify":
        return None
    try:
        spec = json.loads(Path(path).read_text())
        if not isinstance(spec, dict) or set(spec) != {"base", "scope", "lens"}:
            return None
        if not all(isinstance(spec[k], str) and spec[k].strip() for k in spec):
            return None
        if git_value(cwd, "status", "--porcelain", "--untracked-files=all"):
            return None
        base = git_value(cwd, "rev-parse", "--verify", spec["base"] + "^{commit}")
        head = git_value(cwd, "rev-parse", "HEAD")
        diff = git_value(cwd, "diff", "--binary", base, head, "--", spec["scope"])
        return {
            "v": 1,
            "repository": git_value(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"),
            "cwd": str(cwd),
            "head": head,
            "base": base,
            "scope": spec["scope"],
            "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
            "lens": spec["lens"],
            "brief_sha256": hashlib.sha256(brief.encode()).hexdigest(),
            "vendor": row["vendor"],
            "model": row["model"],
            "effort": row["effort"],
            "provider": row["target"]["providerInstanceId"],
            "permissions": {"write": args.write},
            "author_vendor": args.author_vendor,
        }
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def structured_pass(row: dict) -> bool:
    try:
        verdict = json.loads(row["result"])
        if not isinstance(verdict, dict) or verdict.get("verdict") != "PASS":
            return False
        if row.get("status") != "ok" or row.get("exit_code") != 0 or row.get("reused_from"):
            return False
        key = row.get("review_key")
        if not isinstance(key, dict) or any(key.get(k) != row.get(k) for k in ("vendor", "model", "effort")):
            return False
        if row.get("execution_sha") != key.get("head"):
            return False
        events = [json.loads(line) for line in run_path(row["decision_id"], ".log").read_text().splitlines()]
        provider = row["target"]["providerInstanceId"]
        if key.get("provider") != provider:
            return False
        if provider == "codex":
            start = max((i for i, e in enumerate(events) if e.get("type") == "turn.started"), default=-1)
            if start < 0:
                return False
            messages = [
                e["item"].get("text")
                for e in events[start:]
                if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"
            ]
            return bool(messages and messages[-1] == row["result"] and events[-1].get("type") == "turn.completed")
        return bool(
            provider == "claudeAgent"
            and events[-1].get("type") == "result"
            and events[-1].get("subtype") == "success"
            and events[-1].get("is_error") is False
            and events[-1].get("result") == row["result"]
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return False


def reusable_receipt(key: dict | None) -> dict | None:
    if key is None:
        return None
    for path in sorted(run_path("dcn_index", ".json").parent.glob("dcn_*.json"), reverse=True):
        try:
            receipt = json.loads(path.read_text())
            if json.dumps(receipt.get("review_key"), sort_keys=True) == json.dumps(
                key, sort_keys=True
            ) and structured_pass(receipt):
                return receipt
        except (OSError, ValueError, AttributeError):
            continue
    return None
