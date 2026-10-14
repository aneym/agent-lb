#!/usr/bin/env python3


from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def image_tag(task: Path) -> str:
    dockerfile = (task / "environment" / "Dockerfile").read_text()
    base = re.match(r"FROM (\S+)", dockerfile).group(1)
    base_id = docker("image", "inspect", "--format", "{{.Id}}", base).stdout.strip()
    if not base_id:
        raise SystemExit(f"{task.name}: base image {base} is missing")
    digest = hashlib.sha256(f"{dockerfile}\n{base_id}".encode()).hexdigest()[:12]
    return f"of-work/task:{task.name}-{digest}"


def prebuild(task: Path) -> str:
    tag = image_tag(task)
    if docker("image", "inspect", tag).returncode:
        built = docker("build", "-q", "-t", tag, str(task / "environment"))
        if built.returncode:
            return f"FAILED {task.name}: {built.stderr.strip()[-300:]}"
    toml = task / "task.toml"
    text = re.sub(r"^docker_image = .*\n", "", toml.read_text(), flags=re.M)
    toml.write_text(text.replace("[environment]\n", f'[environment]\ndocker_image = "{tag}"\n', 1))
    return tag


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tasks", type=Path)
    parser.add_argument("-j", type=int, default=4, help="parallel builds")
    args = parser.parse_args()
    tasks = sorted(p for p in args.tasks.iterdir() if (p / "task.toml").is_file())
    with ThreadPoolExecutor(args.j) as pool:
        results = list(pool.map(prebuild, tasks))
    failed = [r for r in results if r.startswith("FAILED")]
    print(f"{len(results) - len(failed)} of {len(results)} tasks use a prebuilt image")
    print(*failed, sep="\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
