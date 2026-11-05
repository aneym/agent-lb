import errno
import functools
import glob
import json
import os
import re
import stat

VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?")
ROLLBACK = re.compile(r"\.(" + VERSION.pattern + r")-rollback-[0-9a-f]+")
LAUNCHER_ROLLBACK = re.compile(r"\.launcher-rollback-[0-9a-f]+")
BUILD_DIRS = ("versions", ".rollback-runtime")
GONE = {errno.ENOENT, errno.ESRCH}


def roots():
    homes = {os.path.expanduser("~"), "/root", *glob.glob("/home/*"), *glob.glob("/Users/*")}
    return [os.path.join(home, ".t3/runtime") for home in sorted(homes)]


def runtime_of(path):
    for root in roots():
        for form in {root, os.path.realpath(root)}:
            if path.startswith(form + "/"):
                return root, form
    return None


def key(version):
    return tuple(int(part) for part in re.findall(r"\d+", version))


def version_of(parent, name):
    if VERSION.fullmatch(name):
        return name
    match = ROLLBACK.fullmatch(name) if parent == "versions" else None
    return match[1] if match else None


def read_json(path):
    with open(path) as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(path)
    return data


def known_versions(root):
    found = set()
    for parent in BUILD_DIRS:
        folder = os.path.join(root, parent)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            version = version_of(parent, name)
            found |= {version} if version else set()
    return found


def live_links(root):
    home = os.path.dirname(os.path.dirname(root))
    links = [os.path.join(root, "launcher"), os.path.join(home, ".local/bin/t3")]
    listed = os.path.join(root, "launchers")
    if os.path.lexists(listed):
        with open(listed) as handle:
            links += [line.strip() for line in handle if line.strip()]
    for folder in (root, *(os.path.join(root, parent) for parent in BUILD_DIRS)):
        if os.path.isdir(folder):
            links += [
                os.path.join(folder, name) for name in os.listdir(folder) if os.path.islink(os.path.join(folder, name))
            ]
    return {os.path.realpath(link) for link in links}


@functools.lru_cache(maxsize=None)
def kept(root):
    try:
        service = read_json(os.path.join(root, "service-state.json"))
        pin = read_json(os.path.join(root, "launcher-pin.json"))
        active = service["activeVersion"]
        good = pin["lastKnownGood"]
        update = service.get("update")
        if not (isinstance(active, str) and VERSION.fullmatch(active) and isinstance(good, str) and good):
            return None
        versions = {active}
        if update is not None:
            if not isinstance(update, dict) or update.get("status") != "committed":
                return None
            moves = [update.get("fromVersion"), update.get("targetVersion")]
            if not all(isinstance(move, str) and VERSION.fullmatch(move) for move in moves):
                return None
            versions |= set(moves)
        known = known_versions(root)
        older = [version for version in known if key(version) < key(active)]
        versions |= {max(older, key=key)} if older else set()
        versions |= {version for version in known if key(version) >= key(active)}
        return frozenset(versions), frozenset(live_links(root) | {os.path.realpath(good)})
    except (OSError, ValueError, KeyError, TypeError):
        return None


@functools.lru_cache(maxsize=None)
def held():
    if not os.path.isdir("/proc/self/fd"):
        return None
    me, paths, hidden = os.getuid(), set(), []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        base = "/proc/" + pid
        try:
            if os.stat(base).st_uid != me:
                continue
            try:
                links = [os.path.join(base, name) for name in ("cwd", "exe", "root")]
                links += [os.path.join(base, "fd", fd) for fd in os.listdir(base + "/fd")]
                for link in links:
                    try:
                        paths.add(os.readlink(link))
                    except OSError as error:
                        if error.errno not in GONE:
                            raise
                with open(base + "/maps") as handle:
                    paths |= {line[line.index("/") :].rstrip("\n") for line in handle if "/" in line}
            except PermissionError:
                with open(base + "/cmdline", "rb") as handle:
                    hidden.append(handle.read().decode("utf-8", "replace"))
        except OSError as error:
            if error.errno not in GONE:
                return None
    return frozenset(paths), tuple(hidden)


def busy(entry, real):
    found = held()
    if found is None:
        return True
    paths, hidden = found
    inside = real + "/"
    if any(path == real or path.startswith(inside) for path in paths):
        return True
    return any(entry in line or real in line for line in hidden)


def eligible(root, form, path):
    parts = path[len(form) + 1 :].split("/")
    if len(parts) == 2 and parts[0] in BUILD_DIRS:
        version = version_of(parts[0], parts[1])
        if version is None:
            return False
    elif len(parts) == 1 and LAUNCHER_ROLLBACK.fullmatch(parts[0]):
        version = None
    else:
        return False
    entry = os.path.join(root, *parts)
    real = os.path.realpath(entry)
    try:
        info = os.lstat(entry)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode) or real != os.path.join(os.path.realpath(root), *parts):
        return False
    state = kept(root)
    if state is None:
        return False
    versions, live = state
    if version in versions or any(link == real or link.startswith(real + "/") for link in live):
        return False
    return not busy(entry, real)


def covers(path):
    forms = {form for root in roots() for form in (root, os.path.realpath(root))}
    return any(path == form or form.startswith(path.rstrip("/") + "/") for form in forms)


def refused(word, path, raw):
    magic = glob.has_magic(path)
    found = {path: {path, os.path.realpath(path), os.path.realpath(raw or path)}}
    for match in glob.glob(path, include_hidden=True) if magic else []:
        found[match] = {match, os.path.realpath(match)}
    for item, reached in found.items():
        for candidate in reached:
            if covers(candidate):
                return True
            if runtime_of(candidate) is None:
                continue
            place = runtime_of(item)
            if magic or ".." in (raw or word).split("/") or place is None or not eligible(*place, item):
                return True
    return False
