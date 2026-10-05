"""Validated, mergeable retired-word contracts."""

import json
from pathlib import Path


def load_terms(path):
    try:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict) or set(data) - {
            "version",
            "glossary",
            "source",
            "retired",
            "allowlist",
            "enforce",
        }:
            raise ValueError("unexpected terms fields")
        if type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("version must be 1")
        for key in ("glossary", "source"):
            if not isinstance(data.get(key), str):
                raise ValueError(f"{key} must be a string")
        if not isinstance(data.get("retired"), list) or not isinstance(data.get("allowlist"), list):
            raise ValueError("retired and allowlist must be arrays")
        for term in data["retired"]:
            if not isinstance(term, dict) or set(term) != {"old", "forms", "new", "status", "keep"}:
                raise ValueError("retired entries require old, forms, new, status, keep")
            if any(not isinstance(term[k], str) or not term[k].strip() for k in ("old", "new")):
                raise ValueError("old and new must be nonempty strings")
            if term["status"] not in ("settled", "proposed"):
                raise ValueError("status must be settled or proposed")
            for key in ("forms", "keep"):
                if not isinstance(term[key], list) or any(not isinstance(x, str) or not x.strip() for x in term[key]):
                    raise ValueError(f"{key} must be an array of nonempty strings")
            if not term["forms"]:
                raise ValueError("forms must not be empty")
        for entry in data["allowlist"]:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"path", "word", "reason"}
                or any(not isinstance(v, str) for v in entry.values())
            ):
                raise ValueError("allowlist entries require string path, word, reason")
        return data
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
