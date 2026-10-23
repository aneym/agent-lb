#!/usr/bin/env python3

import argparse
from pathlib import Path


def render(brief="", env=None):
    return (Path(__file__).resolve().parent.parent / "FINISH.md").read_text()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", type=Path)
    args = parser.parse_args()
    brief = args.prompt_file.read_text() if args.prompt_file else ""
    print(render(brief), end="")


if __name__ == "__main__":
    main()
