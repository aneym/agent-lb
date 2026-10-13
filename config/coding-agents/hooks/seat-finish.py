#!/usr/bin/env python3
"""Render the caller-owned lane boundary for local seat contracts."""
import argparse
import os
from pathlib import Path
import re


def own_lane(brief, env=None):
    env = os.environ if env is None else env
    root = (Path.home() / '.agent-rails/lanes').resolve()
    value = env.get('LANE_DIR', '')
    if not value:
        match = re.search(r'(?im)^\s*Lane(?:\s*:\s*|\s+)([A-Za-z0-9][A-Za-z0-9_.-]*)(?=[\s,.;]|$)', brief)
        value = str(root / match.group(1)) if match else ''
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    return path if path.parent == root else None


def allowed_lane_path(path, lane):
    return lane is not None and Path(path).expanduser().resolve().is_relative_to(lane)


def render(brief='', env=None):
    template = (Path(__file__).resolve().parent.parent / 'FINISH.md').read_text()
    lane = own_lane(brief, env)
    boundary = (f'Calling lane directory: `{lane}`. Brief-listed evidence, state, lane-local helpers and runners inside this directory may be edited. '
                if lane else 'Calling lane directory: unresolved. No lane-directory edits are allowed. ')
    return template + '\n' + boundary + 'Other lanes, ~/.agent-rails/factory state, scoping state and Alex\'s files remain forbidden.\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prompt-file', type=Path)
    args = parser.parse_args()
    brief = args.prompt_file.read_text() if args.prompt_file else ''
    print(render(brief), end='')


if __name__ == '__main__':
    main()
