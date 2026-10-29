import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
CHECKPOINT = re.compile(r"^\d{8}T\d{12}Z$")
LINK_KEY = ".agents/policy/coding-agents"


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fsync_dir(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable(path, value, temp=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp or path.with_name(path.name + ".tmp")
    payload = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "wb") as out:
        out.write(payload)
        out.flush()
        os.fsync(out.fileno())
    os.replace(str(tmp), str(path))
    fsync_dir(path.parent)


def delete_marker(path):
    path.unlink()
    fsync_dir(path.parent)


def checkpoints(path):
    return (
        sorted(p.name for p in path.iterdir() if p.is_dir() and CHECKPOINT.fullmatch(p.name)) if path.exists() else []
    )


def applied_commit(path, warnings):
    try:
        obj = json.loads(path.read_text())
        if isinstance(obj, dict) and isinstance(obj.get("commit"), str) and SHA.fullmatch(obj["commit"]):
            return obj["commit"]
        if path.exists():
            warnings.append("bad APPLIED")
    except FileNotFoundError:
        pass
    except (OSError, ValueError, UnicodeError):
        warnings.append("bad APPLIED")
    return None


def restore_link(home, target):
    q = home / LINK_KEY
    try:
        if not (q.is_symlink() and os.readlink(str(q)) == target):
            if q.is_symlink() or q.is_file():
                q.unlink()
            elif q.is_dir():
                shutil.rmtree(str(q))
            q.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, str(q))
    except Exception as exc:
        raise RuntimeError("restore %s: %s" % (q, exc))


def restore_file(ckpt, home, key, value):
    relative = Path(key)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("invalid manifest path: %s" % key)
    dest = home / relative
    try:
        if value == "copied":
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=str(dest.parent), delete=False) as tmp:
                temp_name = tmp.name
            try:
                shutil.copy2(str(ckpt / relative), temp_name)
                os.replace(temp_name, str(dest))
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        elif value == "absent":
            if dest.is_symlink() or dest.is_file():
                dest.unlink()
            elif dest.exists():
                raise IsADirectoryError(str(dest))
        else:
            raise ValueError("invalid manifest entry: %s" % value)
    except Exception as exc:
        raise RuntimeError("restore %s: %s" % (dest, exc))


def restore(marker, checkpoint_root, home):
    names = sorted(set(checkpoints(checkpoint_root)) - set(marker["installer_checkpoints_before"]))
    if not names:
        return "nothing to restore"
    ckpt = max((checkpoint_root / n for n in names), key=lambda p: (p.stat().st_mtime_ns, p.name))
    manifest_path = ckpt / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, UnicodeError):
        return "checkpoint without manifest: nothing to restore"
    if not isinstance(manifest, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in manifest.items()
    ):
        raise RuntimeError("invalid manifest: %s" % manifest_path)
    if LINK_KEY in manifest:
        value = manifest[LINK_KEY]
        if not value.startswith("symlink -> "):
            raise RuntimeError("invalid manifest entry: %s" % LINK_KEY)
        restore_link(home, value[len("symlink -> ") :])
    for key, value in manifest.items():
        if key == LINK_KEY or (LINK_KEY in manifest and key.startswith(LINK_KEY + "/")):
            continue
        restore_file(ckpt, home, key, value)
    for name in names:
        older = checkpoint_root / name
        if older != ckpt and older.stat().st_mtime_ns <= ckpt.stat().st_mtime_ns:
            shutil.rmtree(older)
    fsync_dir(checkpoint_root)
    return str(ckpt)
