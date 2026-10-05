"""Warn about retired words without changing repositories."""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from ..words.classify import CLASSES
from ..words.scan import scan, tokens
from ..words.terms import load_terms

NAME = "words"
HELP = "Check retired words, plan renames and count remaining uses"


def build_parser(subparsers):
    parser = subparsers.add_parser(NAME, help=HELP)
    commands = parser.add_subparsers(dest="words_command", required=True)
    for name in ("check", "rename", "status"):
        command = commands.add_parser(name)
        command.add_argument("--terms", action="append")
        command.add_argument("--repo", action="append")
        command.add_argument("--json", action="store_true")
        if name == "check":
            command.add_argument("--diff")
            command.add_argument("paths", nargs="*")
        else:
            command.add_argument("--repos-file")
        if name == "rename":
            command.add_argument("old")
            command.add_argument("new")
            command.add_argument("--plan", action="store_true")
        command.set_defaults(func=run)


def contracts(repo, explicit):
    paths = (
        explicit
        if explicit is not None
        else [
            str(p)
            for p in (Path(repo) / "glossary/terms.json", Path(repo) / "glossary/rails-terms.json")
            if p.is_file()
        ]
    )
    terms, allowlist = [], []
    for path in paths:
        data = load_terms(path)
        for term in data["retired"]:
            if term not in terms:
                terms.append(term)
        allowlist.extend(data["allowlist"])
    return terms, allowlist


def run(args):
    if args.words_command == "rename" and not args.plan:
        print("only --plan is built; renames are separate pieces", file=sys.stderr)
        return 2
    try:
        repos = args.repo or [str(Path.cwd())]
        if getattr(args, "repos_file", None):
            extra = json.loads(Path(args.repos_file).read_text())
            if not isinstance(extra, list) or any(not isinstance(p, str) for p in extra):
                raise ValueError("repos-file must be a JSON list of local paths")
            repos += extra
        if args.words_command == "check":
            repo = repos[0]
            terms, allowlist = contracts(repo, args.terms)
            hits = [h for h in scan(repo, terms, allowlist, args.paths, args.diff) if h["class"] != "external"]
            if args.json:
                print(json.dumps(hits))
            else:
                for h in hits:
                    print(f'{h["path"]}:{h["line_no"]}: "{h["old"]}" -> say "{h["new"]}"')
            return 0
        reports = []
        totals = {}
        for repo in dict.fromkeys(repos):
            if not Path(repo).is_dir():
                reports.append({"path": repo, "missing": True, "counts": {}, "hits": []})
                continue
            terms, allowlist = contracts(repo, args.terms)
            if args.words_command == "rename":
                query = [
                    t
                    for t in terms
                    if args.old.lower() == t["old"].lower()
                    or any([x[0] for x in tokens(args.old)] == [x[0] for x in tokens(f)] for f in t["forms"])
                ]
                terms = query or [{"old": args.old, "new": args.new, "forms": [args.old], "keep": []}]
            hits = scan(repo, terms, allowlist, filenames=True)
            if args.words_command == "rename":
                for h in hits:
                    h["new"] = args.new
            counts = dict(Counter(h["class"] for h in hits))
            reports.append({"path": repo, "missing": False, "counts": counts, "hits": hits})
            for term in terms:
                counter = totals.setdefault(term["old"], Counter())
                counter.update(h["class"] for h in hits if h["old"] == term["old"] and h["class"] != "external")
        if args.words_command == "status":
            result = {
                "terms": [
                    {"old": old, "counts": dict(counts), "total": sum(counts.values())}
                    for old, counts in totals.items()
                ],
                "repos": reports,
            }
            if args.json:
                print(json.dumps(result))
            else:
                for term in result["terms"]:
                    print(f"{term['old']}: {term['total']} {json.dumps(term['counts'], sort_keys=True)}")
                for repo in reports:
                    if repo["missing"]:
                        print(f"{repo['path']}: missing")
        else:
            result = {"old": args.old, "new": args.new, "repos": reports, "total": sum(len(r["hits"]) for r in reports)}
            if args.json:
                print(json.dumps(result))
            else:
                print("repo\t" + "\t".join(CLASSES))
                for repo in reports:
                    print(
                        repo["path"]
                        + (
                            "\tmissing"
                            if repo["missing"]
                            else "\t" + "\t".join(str(repo["counts"].get(c, 0)) for c in CLASSES)
                        )
                    )
                    for cls in CLASSES:
                        for h in [h for h in repo["hits"] if h["class"] == cls][:20]:
                            print(f"  {cls} {h['path']}:{h['line_no']}: {h['text']}")
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"words: unable to scan: {exc}", file=sys.stderr)
        return 0
