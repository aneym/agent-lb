"""CLI integration contracts using real tracked repositories, no internal mocks.

These are the primary coverage for the new user commands: token false positives,
classification, added-line isolation, warn-only errors and read-only plans.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2]


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def cli(repo, *args):
    env = dict(os.environ, PYTHONPATH=str(PACKAGE))
    return subprocess.run(
        [sys.executable, "-m", "open_factory", *args], cwd=repo, env=env, capture_output=True, text=True
    )


def setup_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    write(
        repo,
        "glossary/terms.json",
        json.dumps(
            {
                "version": 1,
                "glossary": "test",
                "source": "test",
                "retired": [
                    {
                        "old": "fold",
                        "forms": ["fold", "folds"],
                        "new": "job",
                        "status": "settled",
                        "keep": ["docs/history/**", "glossary/**"],
                    },
                    {
                        "old": "release captain",
                        "forms": ["release captain"],
                        "new": "releaser",
                        "status": "settled",
                        "keep": ["glossary/**"],
                    },
                ],
                "allowlist": [],
            }
        ),
    )
    return repo


def write(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit(repo):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")


def test_scan_classes_tokens_and_read_only_plan(tmp_path):
    repo = setup_repo(tmp_path)
    fixtures = {
        "tokens.py": (
            "folder unfold scaffold\n"
            "fold_pipeline foldPipeline FoldResult fold-pr FOLD_X\n"
            "p6 it2\n"
            "releaseCaptain\n"
        ),
        "docs/history/old.md": "fold\n",
        "quote.md": "> fold\nhttps://example.invalid/fold\n",
        "fold.txt": "nothing\n",
        "db.sql": "CREATE TABLE fold (id int);\n",
        "api.py": 'route = "/fold"\n',
        "cli.py": 'parser.add_argument("--fold")\n',
        "config.json": '{"fold": 1}\n',
        "ui.tsx": "<span>fold</span>\n",
        "prose.md": "fold\n",
        "code.py": "fold = 1\n",
    }
    for path, text in fixtures.items():
        write(repo, path, text)
    commit(repo)
    before = git(repo, "status", "--porcelain")
    second = setup_repo(tmp_path, "second")
    write(second, "second.py", "fold = 2\n")
    commit(second)
    missing = tmp_path / "missing"
    result = cli(
        repo,
        "words",
        "rename",
        "fold",
        "job",
        "--plan",
        "--repo",
        str(repo),
        "--repo",
        str(second),
        "--repo",
        str(missing),
        "--json",
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["repos"][2]["missing"]
    assert data["repos"][1]["counts"] == {"code": 1, "external": 3}
    hits = data["repos"][0]["hits"]
    expected = {
        "docs/history/old.md": "external",
        "quote.md": "external",
        "fold.txt": "file_name",
        "db.sql": "db",
        "api.py": "api",
        "cli.py": "cli",
        "config.json": "config",
        "ui.tsx": "ui",
        "prose.md": "prose",
        "code.py": "code",
    }
    for path, category in expected.items():
        assert any(h["path"] == path and h["class"] == category for h in hits)
    token_hits = [h for h in hits if h["path"] == "tokens.py"]
    assert len(token_hits) == 5
    assert all(h["line_no"] == 2 for h in token_hits)
    assert git(repo, "status", "--porcelain") == before == ""
    assert git(second, "status", "--porcelain") == ""
    filename_hit = next(h for h in hits if h["class"] == "file_name")
    assert (filename_hit["path"], filename_hit["line_no"]) == ("fold.txt", 0)
    assert cli(repo, "words", "rename", "fold", "job").returncode == 2
    check = cli(repo, "words", "check", "--json", "tokens.py")
    assert check.returncode == 0
    assert len(json.loads(check.stdout)) == 6
    status = json.loads(cli(repo, "words", "status", "--json").stdout)
    assert next(t for t in status["terms"] if t["old"] == "release captain")["total"] == 1
    fold_status = next(t for t in status["terms"] if t["old"] == "fold")
    assert fold_status["total"] == 13
    assert fold_status["counts"] == {
        "file_name": 1,
        "db": 1,
        "api": 1,
        "cli": 1,
        "config": 1,
        "ui": 1,
        "prose": 1,
        "code": 6,
    }


def test_diff_defaults_and_bad_terms(tmp_path):
    repo = setup_repo(tmp_path)
    write(repo, "old.txt", "fold\n")
    commit(repo)
    base = git(repo, "rev-parse", "HEAD").strip()
    write(repo, "old.txt", "fold\nSwitch space\n")
    write(
        repo,
        "glossary/rails-terms.json",
        json.dumps(
            {
                "version": 1,
                "glossary": "rails",
                "source": "test",
                "retired": [
                    {
                        "old": "team space",
                        "forms": ["Switch space"],
                        "new": "workspace",
                        "status": "settled",
                        "keep": ["glossary/**"],
                    }
                ],
                "allowlist": [],
            }
        ),
    )
    commit(repo)
    result = cli(repo, "words", "check", "--diff", base, "--json", "old.txt")
    assert result.returncode == 0
    hits = json.loads(result.stdout)
    assert [(h["old"], h["line_no"]) for h in hits] == [("team space", 2)]
    warning = cli(repo, "words", "check", "--diff", base, "old.txt")
    assert 'old.txt:2: "team space" -> say "workspace"' in warning.stdout
    write(repo, "bad.json", '{"version": 2}')
    assert cli(repo, "words", "check", "--terms", "bad.json").returncode == 2


def test_glossary_complete_and_incomplete(tmp_path):
    repo = setup_repo(tmp_path)
    item = {
        "term": "job",
        "definition": "A unit of work.",
        "example": "Run a job.",
        "not": ["seat"],
        "related": ["seat"],
        "code_name": "job",
    }
    write(repo, "CONTEXT.md", "```terms\n" + json.dumps([item]) + "\n```\n")
    assert cli(repo, "glossary", "check").returncode == 0
    del item["not"]
    item["definition"] = "word " * 26
    write(repo, "CONTEXT.md", "```terms\n" + json.dumps([item]) + "\n```\n")
    result = cli(repo, "glossary", "check")
    assert result.returncode == 1
    assert "missing not" in result.stdout and "definition" in result.stdout
    write(
        repo,
        "CONTEXT.md",
        "## Job\nDefinition: A unit of work.\nExample: Run a job.\nNot: seat\nRelated terms: seat\nCode name: job\n",
    )
    assert cli(repo, "glossary", "check").returncode == 0


def test_glossary_rails_bold_format(tmp_path):
    repo = setup_repo(tmp_path)
    write(
        repo,
        "CONTEXT.md",
        "# Rails\n"
        "## Language\n"
        "### Who\n"
        "**Workspace**:\n"
        "A company container.\n"
        "Example: Rails is a workspace.\n"
        "Not: team.\n"
        "See: owner, member\n"
        "Code: tenants\n"
        "\n"
        "**Owner**:\n"
        "A workspace role.\n"
        "Example: An owner manages members.\n"
        "Not: founder.\n"
        "See: workspace\n"
        "Code: owner\n",
    )
    result = cli(repo, "glossary", "check")
    assert result.returncode == 0, result.stdout
    assert "2 glossary entries complete" in result.stdout


def test_identifier_digits_and_allowlist(tmp_path):
    repo = setup_repo(tmp_path)
    terms = json.loads((repo / "glossary/terms.json").read_text())
    terms["retired"] = [
        {"old": word, "forms": [word], "new": "replacement", "status": "settled", "keep": ["glossary/**"]}
        for word in ["p6", "it2", "http", "server"]
    ]
    terms["allowlist"] = [{"path": "keep.py", "word": "p6", "reason": "external contract"}]
    terms["enforce"] = True
    write(repo, "glossary/terms.json", json.dumps(terms))
    write(repo, "code.py", "p6 it2 HTTPServer p60 it20\n")
    write(repo, "keep.py", "p6\n")
    commit(repo)
    result = cli(repo, "words", "check", "--json")
    assert result.returncode == 0
    hits = json.loads(result.stdout)
    assert [(h["form"], h["col"]) for h in hits] == [("p6", 1), ("it2", 4), ("http", 8), ("server", 12)]


def test_multi_word_forms_at_cli_boundary(tmp_path):
    repo = setup_repo(tmp_path)
    write(repo, "prose.md", "release captain\nreleaseCaptain\nrelease-captain\nprerelease captain\n")
    commit(repo)
    result = cli(repo, "words", "check", "--json", "prose.md")
    assert result.returncode == 0, result.stderr
    hits = json.loads(result.stdout)
    assert [(h["old"], h["line_no"], h["col"]) for h in hits] == [
        ("release captain", 1, 1),
        ("release captain", 2, 1),
        ("release captain", 3, 1),
    ]
