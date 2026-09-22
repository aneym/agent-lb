from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_hosted_merge_ci_and_windows_workflows_are_removed() -> None:
    assert not (ROOT / ".github/workflows/ci.yml").exists()
    assert not (ROOT / ".github/workflows/windows-startup.yml").exists()


def test_local_ci_docs_require_the_full_exact_sha_gate() -> None:
    docs = (ROOT / "docs/local-ci.md").read_text(encoding="utf-8")

    assert "python3 scripts/local_ci.py run <PR|sha|main>" in docs
    assert "python3 scripts/local_ci.py status <40sha>" in docs
    assert "~/agent-lb-ci/receipts/<sha>/<runid>" in docs
    assert "Missing or failed tools make the run red; they are never skipped." in docs
