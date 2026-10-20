import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, TypedDict


class ArtifactFile(TypedDict):
    path: str
    sha256: str
    bytes: int
    mode: int


class Specification(TypedDict):
    origin: str
    source: str
    tag: str
    paths: list[str]
    files: list[str]
    archive_sha256: str


ROOT = Path(__file__).resolve().parents[2]


def git(repo: Path, *args: str, output: BinaryIO | None = None) -> bytes:
    return (
        subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
            check=True,
            stdout=output or subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        or b""
    )


def safe_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or ".git" in path.parts
        or "\x00" in value
        or "\\" in value
        or str(path) != value
    ):
        raise ValueError("Unsafe artifact path: " + value)
    return path


def specification(name: str) -> tuple[Specification, dict[str, ArtifactFile]]:
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
        raise ValueError("Invalid artifact name")
    folder = ROOT / "artifact-manifests"
    spec = json.loads((folder / (name + ".json")).read_text())
    files = {}
    for chunk in spec["files"]:
        safe_path(chunk)
        for row in json.loads((folder / chunk).read_text()):
            safe_path(row["path"])
            if row["path"] in files:
                raise ValueError("Duplicate artifact file")
            files[row["path"]] = row
    for value in spec["paths"]:
        safe_path(value)
    if not files or any(not any(p == root or p.startswith(root + "/") for root in spec["paths"]) for p in files):
        raise ValueError("Artifact files outside declared paths")
    return spec, files


def checked_files(directory: Path, files: dict[str, ArtifactFile]) -> None:
    observed = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("Artifact symlink")
        if path.is_file():
            rel = path.relative_to(directory).as_posix()
            row = files.get(rel)
            if (
                row is None
                or path.stat().st_size != row["bytes"]
                or path.stat().st_mode & 0o777 != row["mode"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]
            ):
                raise ValueError("Artifact file mismatch: " + rel)
            observed.add(rel)
    if observed != files.keys():
        raise ValueError("Artifact file set mismatch")


def unpack(archive: Path, destination: Path, files: dict[str, ArtifactFile]) -> None:
    observed = set()
    with tarfile.open(archive, "r:*") as stream:
        for member in stream:
            safe_path(member.name.rstrip("/"))
            if member.isdir():
                if not any(p.startswith(member.name.rstrip("/") + "/") for p in files):
                    raise ValueError("Unexpected artifact directory")
                continue
            if not member.isfile() or member.name not in files or member.name in observed:
                raise ValueError("Unexpected artifact member: " + member.name)
            row = files[member.name]
            if member.size != row["bytes"]:
                raise ValueError("Artifact size mismatch")
            entry = stream.extractfile(member)
            if entry is None:
                raise ValueError("Unreadable artifact member")
            content = entry.read()
            if hashlib.sha256(content).hexdigest() != row["sha256"]:
                raise ValueError("Artifact checksum mismatch: " + member.name)
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(row["mode"])
            observed.add(member.name)
    if observed != files.keys():
        raise ValueError("Artifact missing expected members")


def source_archive(spec: Specification, output: Path) -> None:
    try:
        git(ROOT, "rev-parse", "--verify", spec["source"] + "^{commit}")
        repo = ROOT
        with output.open("wb") as handle:
            git(
                repo,
                "archive",
                "--format=tar",
                spec["source"],
                "--",
                *spec["paths"],
                output=handle,
            )
    except subprocess.CalledProcessError:
        with tempfile.TemporaryDirectory(prefix="artifact-origin-") as directory:
            git(Path(directory), "init", "-q")
            git(
                Path(directory),
                "fetch",
                "--filter=blob:none",
                "--depth=1",
                spec["origin"],
                spec["tag"],
            )
            source = git(Path(directory), "rev-parse", "FETCH_HEAD").decode().strip()
            if source != spec["source"]:
                raise ValueError("Artifact origin tag changed")
            with output.open("wb") as handle:
                git(
                    Path(directory),
                    "archive",
                    "--format=tar",
                    source,
                    "--",
                    *spec["paths"],
                    output=handle,
                )


def restore(name: str, destination: Path) -> Path:
    spec, files = specification(name)
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        checked_files(destination, files)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".artifact-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        staged = temporary / "tree"
        staged.mkdir()
        store = Path(os.environ.get("STACK_ARTIFACT_STORE", "/Volumes/StudioExt/artifacts/stack-lean"))
        archive = store / (spec["archive_sha256"] + ".tar.gz")
        if archive.is_file():
            if hashlib.sha256(archive.read_bytes()).hexdigest() != spec["archive_sha256"]:
                raise ValueError("Stored artifact checksum mismatch")
        else:
            archive = temporary / "source.tar"
            source_archive(spec, archive)
        unpack(archive, staged, files)
        checked_files(staged, files)
        try:
            staged.rename(destination)
        except FileExistsError:
            checked_files(destination, files)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore SHA-verified archived repository files.")
    parser.add_argument("name")
    parser.add_argument("--dest", type=Path, required=True)
    args = parser.parse_args()
    print(restore(args.name, args.dest))
