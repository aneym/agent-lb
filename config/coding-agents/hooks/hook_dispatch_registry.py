import hashlib
import json
import os

from hook_dispatch_planning import HERE  # type: ignore[missing-import]


def registry_path():
    return os.environ.get("HOOK_DISPATCH_REGISTRY") or os.path.join(HERE, "dispatch", "registry.json")


def registry_candidates(rev=None):
    base = registry_path()
    folder = os.path.dirname(base)
    pinned = bool(os.environ.get("HOOK_DISPATCH_REGISTRY"))
    paths = []
    if rev and not pinned:
        paths.append(os.path.join(folder, "registry.%s.json" % rev))
    paths += [base, base + ".bak"]
    if not rev and not pinned:
        paths.append(os.path.join(folder, "registry.legacy.json"))
    return paths


def load_entry(event, key, rev=None):
    errors = []
    for path in registry_candidates(rev):
        try:
            with open(path, encoding="utf-8") as handle:
                registry = json.load(handle)
        except (OSError, ValueError) as exc:
            errors.append("%s: %s" % (os.path.basename(path), exc.__class__.__name__))
            continue
        if not (isinstance(registry, dict) and isinstance(registry.get("entries"), dict)):
            errors.append("%s: not a registry" % os.path.basename(path))
            continue
        if (registry.get("rev") or None) != rev:
            errors.append(
                "%s: fold %s, not this entry's %s"
                % (os.path.basename(path), registry.get("rev") or "without a rev", rev or "(without a rev)")
            )
            continue
        if rev and entries_rev(registry["entries"]) != rev:
            errors.append("%s: entries do not hash to rev %s" % (os.path.basename(path), rev))
            continue
        try:
            return registry, entry_hooks(registry, event, key), path
        except ValueError as exc:
            errors.append("%s: %s" % (os.path.basename(path), exc))
    raise ValueError("; ".join(errors))


def entries_rev(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def entry_hooks(registry, event, key):
    hooks = (registry["entries"].get(event) or {}) if isinstance(registry["entries"].get(event), dict) else None
    if hooks is None or key not in hooks:
        raise ValueError("no %s '%s' entry" % (event, key))
    hooks = hooks[key]
    if not isinstance(hooks, list) or not hooks:
        raise ValueError("the %s '%s' entry is %s, not a list of hooks" % (event, key, type(hooks).__name__))
    for hook in hooks:
        if not (
            isinstance(hook, dict)
            and hook.get("type", "command") == "command"
            and isinstance(hook.get("command"), str)
            and hook["command"].strip()
        ):
            raise ValueError("the %s '%s' entry holds %r, not a command hook" % (event, key, hook))
    seen, kept = set(), []
    for hook in hooks:
        marker = json.dumps([hook.get("type"), hook.get("command")])
        if marker in seen:
            continue
        seen.add(marker)
        kept.append(hook)
    return kept
