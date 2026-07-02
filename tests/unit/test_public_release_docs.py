from __future__ import annotations

import json
import stat
import subprocess
import tomllib
from pathlib import Path

import yaml

from scripts.release_versions import ReleaseVersion, parse_version, read_pyproject_version

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_REPO_DESCRIPTION = (
    "ChatGPT and Claude account load balancer & proxy with usage tracking, "
    "dashboard, and OpenAI/Anthropic-compatible endpoints"
)
PUBLIC_REPO_HOMEPAGE = "https://github.com/aneym/agent-lb"
PUBLIC_REPO_TOPICS = (
    "python",
    "oauth",
    "sqlalchemy",
    "dashboard",
    "load-balancer",
    "openai",
    "anthropic",
    "claude",
    "rate-limit",
    "api-proxy",
    "codex",
    "fastapi",
    "usage-tracking",
    "chatgpt",
    "opencode",
    "openclaw",
)
PUBLIC_REPO_RESOURCES = (
    f"Homepage {PUBLIC_REPO_HOMEPAGE}",
    "Repository https://github.com/aneym/agent-lb",
    "Issues https://github.com/aneym/agent-lb/issues",
    "Releases https://github.com/aneym/agent-lb/releases",
    "Discussions https://github.com/aneym/agent-lb/discussions",
    "Security https://github.com/aneym/agent-lb/security/advisories/new",
)
PR_HEAD_SNAPSHOT_AT = "2026-06-14T07:20:58Z"
PR_HEAD_PROOF_AT = "2026-06-14T08:17:46Z"
PREFLIGHT_AT = "2026-06-14T08:38:25Z"
PREFLIGHT_PUBLIC_SNAPSHOT_AT = "2026-06-14T08:38:26Z"
PREFLIGHT_LOCAL_ARTIFACT_PROOF_AT = "2026-06-14T08:38:29Z"
STANDALONE_PUBLIC_SNAPSHOT_AT = "2026-06-14T08:50:46Z"
LIVE_PUBLIC_SNAPSHOT_AT = PREFLIGHT_PUBLIC_SNAPSHOT_AT
PUBLISH_READINESS_AT = "2026-06-14T08:50:46Z"


def test_install_service_plist_uses_menubar_service_label() -> None:
    result = subprocess.run(
        ["bash", "scripts/install-service.sh", "--print"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "<string>com.aneyman.agent-lb</string>" in result.stdout
    assert "com.agent-lb" not in result.stdout


def test_readme_jsonc_config_examples_parse() -> None:
    readme = (ROOT / "README.md").read_text()

    for block in _fenced_blocks(readme, "jsonc"):
        json.loads(_jsonc_to_json(block))


def test_public_markdown_code_fences_are_balanced() -> None:
    public_markdown_paths = (
        ROOT / "README.md",
        ROOT / "GETTING-STARTED.md",
        ROOT / "AGENTS.md",
        ROOT / ".github/CONTRIBUTING.md",
        ROOT / ".github/PULL_REQUEST_TEMPLATE.md",
        ROOT / ".github/SECURITY.md",
        ROOT / "deploy/helm/agent-lb/README.md",
        ROOT / "GOAL.md",
        ROOT / "HANDOFF.md",
    )
    offenders: list[str] = []

    for path in public_markdown_paths:
        in_fence = False
        opened_at = 0
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            marker = line.strip()
            if not marker.startswith("```"):
                continue

            if in_fence:
                if marker != "```":
                    offenders.append(f"{path.relative_to(ROOT)}:{line_number} has an info string on a closing fence")
                in_fence = False
                opened_at = 0
                continue

            in_fence = True
            opened_at = line_number

        if in_fence:
            offenders.append(f"{path.relative_to(ROOT)}:{opened_at} opens an unclosed code fence")

    assert offenders == []


def test_readme_public_screenshot_assets_exist() -> None:
    readme = (ROOT / "README.md").read_text()
    screenshot_paths = {
        "docs/screenshots/accounts-dark.jpg",
        "docs/screenshots/accounts.jpg",
        "docs/screenshots/dashboard-dark.jpg",
        "docs/screenshots/dashboard.jpg",
        "docs/screenshots/login.jpg",
        "docs/screenshots/settings-dark.jpg",
        "docs/screenshots/settings.jpg",
    }

    for screenshot_path in screenshot_paths:
        assert f"({screenshot_path})" in readme

        data = (ROOT / screenshot_path).read_bytes()
        assert len(data) > 50_000
        assert data.startswith(b"\xff\xd8")
        assert data.endswith(b"\xff\xd9")
        assert _jpeg_dimensions(data) == (2880, 1800)


def test_public_screenshot_directory_contains_only_documented_assets() -> None:
    expected_paths = {
        "docs/screenshots/accounts-dark.jpg",
        "docs/screenshots/accounts.jpg",
        "docs/screenshots/dashboard-dark.jpg",
        "docs/screenshots/dashboard.jpg",
        "docs/screenshots/login.jpg",
        "docs/screenshots/settings-dark.jpg",
        "docs/screenshots/settings.jpg",
    }
    actual_paths = {
        path.relative_to(ROOT).as_posix() for path in (ROOT / "docs/screenshots").iterdir() if path.is_file()
    }

    assert actual_paths == expected_paths


def test_public_screenshot_harness_uses_owned_preview_server() -> None:
    config = (ROOT / "frontend/screenshots/playwright.config.ts").read_text()
    capture = (ROOT / "frontend/screenshots/capture.spec.ts").read_text()

    assert "const SCREENSHOT_PORT = 4174;" in config
    assert "const SCREENSHOT_BASE_URL = `http://127.0.0.1:${SCREENSHOT_PORT}`;" in config
    assert "baseURL: SCREENSHOT_BASE_URL" in config
    assert "bun run preview --host 127.0.0.1 --port ${SCREENSHOT_PORT}" in config
    assert "url: SCREENSHOT_BASE_URL" in config
    assert "reuseExistingServer: false" in config
    assert "http://localhost:4173" not in config
    assert "http://localhost:4173" not in capture
    assert 'await page.goto(opts.route, { waitUntil: "networkidle" });' in capture


def test_readme_github_metadata_header_matches_public_release_target() -> None:
    metadata = _readme_metadata_header((ROOT / "README.md").read_text())

    assert metadata["about"] == PUBLIC_REPO_DESCRIPTION
    assert metadata["topics"] == list(PUBLIC_REPO_TOPICS)
    assert metadata["resources"] == list(PUBLIC_REPO_RESOURCES)
    assert "Codex/ChatGPT" not in metadata["about"]


def test_handoff_approval_packet_matches_readme_github_metadata_target() -> None:
    handoff = (ROOT / "HANDOFF.md").read_text()

    assert f"Description:\n{PUBLIC_REPO_DESCRIPTION}" in handoff
    assert f"Homepage:\n{PUBLIC_REPO_HOMEPAGE}" in handoff
    assert f"Topics:\n{' '.join(PUBLIC_REPO_TOPICS)}" in handoff
    assert f"Resources:\n{'\n'.join(PUBLIC_REPO_RESOURCES)}" in handoff
    assert f'--description "{PUBLIC_REPO_DESCRIPTION}"' in handoff
    assert f'--homepage "{PUBLIC_REPO_HOMEPAGE}"' in handoff
    assert "gh api \\" in handoff
    assert "--method PUT" in handoff
    assert "/repos/aneym/agent-lb/topics" in handoff
    assert "--input - <<'JSON'" in handoff
    assert "--add-topic" not in handoff
    assert _handoff_topic_payload(handoff) == {"names": list(PUBLIC_REPO_TOPICS)}


def test_handoff_commit_pr_readiness_preflight_stays_read_only() -> None:
    handoff = (ROOT / "HANDOFF.md").read_text()
    preflight = _handoff_commit_pr_readiness_preflight(handoff)

    assert "Run this before asking for or performing any commit, push, tag, PR" in handoff
    assert "./scripts/public-release-preflight.sh <approved-release-tag>" in handoff
    assert "git status --short" in preflight
    assert "git diff --stat" in preflight
    assert "git rev-parse HEAD" in preflight
    assert "git rev-parse '<approved-release-tag>^{}'" in preflight
    assert "gh pr list --repo aneym/agent-lb --state open" in preflight
    assert "gh run list --repo aneym/agent-lb --branch main --limit 10" in preflight
    assert "uv lock --locked" in preflight
    assert "bash -n scripts/public-release-preflight.sh" in preflight
    assert "scripts/public-release-drift-scan.sh" in preflight
    assert "scripts/public-release-live-snapshot.sh" in preflight
    assert "scripts/public-release-publish-readiness.sh" in preflight
    assert "scripts/public-release-postpublish-proof.sh" in preflight
    assert "scripts/public-release-pr-head-proof.sh" in preflight
    assert "scripts/public-release-local-artifact-proof.sh" in preflight
    assert "scripts/public-release-runtime-proof.sh" in preflight
    assert "scripts/validate-active-openspec-changes.sh" in preflight
    assert "./scripts/public-release-drift-scan.sh" in preflight
    assert "./scripts/public-release-live-snapshot.sh <approved-release-tag>" in preflight
    assert "./scripts/public-release-local-artifact-proof.sh <approved-release-tag>" in preflight
    assert (
        "uv run pytest -q tests/unit/test_release_versions.py "
        "tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py" in preflight
    )
    assert "uv run pytest -q tests/unit/test_guard_beta_release.py" in preflight
    assert (
        "uv run python -m scripts.verify_release_version --tag <approved-release-tag> --require-channel beta"
        in preflight
    )
    assert "./scripts/validate-active-openspec-changes.sh" in preflight
    assert "npx --yes @fission-ai/openspec@latest validate --specs" in preflight
    assert "uvx ruff format --check tests/unit/test_public_release_docs.py" in preflight
    assert "uvx ruff check tests/unit/test_public_release_docs.py" in preflight
    assert "git diff --check" in preflight
    for mutating_command in (
        "git commit",
        "git push",
        "gh pr create",
        "gh repo edit",
        "gh release edit",
        "gh workflow run",
        "gh api",
    ):
        assert mutating_command not in preflight


def test_handoff_publish_readiness_guard_blocks_dirty_tree_and_tag_drift() -> None:
    goal = (ROOT / "GOAL.md").read_text()
    handoff = (ROOT / "HANDOFF.md").read_text()
    normalized_goal = " ".join(goal.split())
    normalized = " ".join(handoff.split())

    assert "./scripts/public-release-publish-readiness.sh <approved-release-tag>" in handoff
    assert "publish-readiness guard" in normalized
    assert PUBLISH_READINESS_AT in normalized
    assert f"publishReadinessAt={PUBLISH_READINESS_AT}" in normalized
    assert "release tag does not point at `HEAD`" in handoff
    assert "current_branch=main" in handoff
    assert "main_sha=b00efd4fce34f42edb455a78b9cf34df8600e337" in handoff
    assert "local `main`" in handoff
    assert "channel=beta" in normalized
    assert "pypi_version=1.20.0b3" in normalized
    assert "working tree is dirty" in normalized
    assert "dirty_count=167" in handoff
    assert "167 dirty/untracked paths" in handoff
    assert "167 dirty/untracked paths" in normalized_goal
    assert "open_pr_count" in handoff
    assert "current_head_main_run_count" in handoff
    assert "missing/non-green" in normalized
    assert "current-head `main` workflow evidence" in handoff
    assert "164 dirty/untracked paths" not in handoff
    assert "164 dirty/untracked paths" not in normalized_goal
    assert "159 dirty/untracked paths" not in handoff
    assert "159 dirty/untracked paths" not in normalized_goal
    assert "expected non-zero" in normalized
    assert "No GitHub mutations were made." in handoff


def test_public_release_preflight_script_stays_read_only() -> None:
    script_path = ROOT / "scripts/public-release-preflight.sh"
    script = script_path.read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert script_path.stat().st_mode & stat.S_IXUSR
    assert "set -euo pipefail" in script
    assert "usage: $0 <approved-release-tag> [release-channel]" in script
    assert 'PREFLIGHT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in script
    assert 'echo "preflightAt=${PREFLIGHT_AT}"' in script
    assert "git status --short" in script
    assert "git diff --stat" in script
    assert "git rev-parse HEAD" in script
    assert 'git rev-parse "${RELEASE_TAG}^{}"' in script
    assert "gh pr list --repo aneym/agent-lb --state open" in script
    assert "gh run list --repo aneym/agent-lb --branch main --limit 10" in script
    assert "uv lock --locked" in script
    assert "bash -n scripts/public-release-preflight.sh" in script
    assert "scripts/public-release-drift-scan.sh" in script
    assert "scripts/public-release-live-snapshot.sh" in script
    assert "scripts/public-release-publish-readiness.sh" in script
    assert "scripts/public-release-postpublish-proof.sh" in script
    assert "scripts/public-release-pr-head-proof.sh" in script
    assert "scripts/public-release-local-artifact-proof.sh" in script
    assert "scripts/public-release-runtime-proof.sh" in script
    assert "scripts/validate-active-openspec-changes.sh" in script
    assert "./scripts/public-release-drift-scan.sh" in script
    assert './scripts/public-release-live-snapshot.sh "${RELEASE_TAG}"' in script
    assert './scripts/public-release-local-artifact-proof.sh "${RELEASE_TAG}"' in script
    assert "tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py" in script
    assert "uv run pytest -q tests/unit/test_guard_beta_release.py" in script
    assert 'uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}"' in script
    assert "./scripts/validate-active-openspec-changes.sh" in script
    assert "npx --yes @fission-ai/openspec@latest validate --specs" in script
    assert "uvx ruff format --check tests/unit/test_public_release_docs.py" in script
    assert "uvx ruff check tests/unit/test_public_release_docs.py" in script
    assert "git diff --check" in script
    assert 'echo "preflight complete at ${PREFLIGHT_AT}"' in script
    for mutating_command in (
        "git commit",
        "git push",
        "gh pr create",
        "gh repo edit",
        "gh release edit",
        "gh workflow run",
        "gh api",
    ):
        assert mutating_command not in script


def test_public_release_runtime_proof_script_stays_read_only() -> None:
    script_path = ROOT / "scripts/public-release-runtime-proof.sh"
    script = script_path.read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert script_path.stat().st_mode & stat.S_IXUSR
    assert "set -euo pipefail" in script
    assert "usage: $0 <approved-release-tag> [release-channel] [base-url]" in script
    assert 'RUNTIME_PROOF_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in script
    assert 'echo "runtimeProofAt=${RUNTIME_PROOF_AT}"' in script
    assert 'uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}"' in script
    assert 'EXPECTED_RELEASE_URL="https://github.com/aneym/agent-lb/releases/latest"' in script
    assert 'BASE_URL="${3:-http://127.0.0.1:2455}"' in script
    assert 'curl -fsS "${BASE_URL}/health" | jq -e' in script
    assert '.status == "ok"' in script
    assert 'curl -fsS "${BASE_URL}/api/runtime/version"' in script
    assert ".currentVersion == $expected_version" in script
    assert ".updateAvailable == false" in script
    assert ".releaseUrl == $expected_release_url" in script
    assert '.checkedAt | type == "string" and length > 0' in script
    assert 'echo "runtime release proof passed at ${RUNTIME_PROOF_AT}"' in script
    for mutating_command in (
        "launchctl",
        "scripts/install-service.sh",
        "git commit",
        "git push",
        "gh pr create",
        "gh repo edit",
        "gh release edit",
        "gh workflow run",
        "gh api",
    ):
        assert mutating_command not in script


def test_public_release_local_artifact_proof_script_stays_read_only() -> None:
    script_path = ROOT / "scripts/public-release-local-artifact-proof.sh"
    script = script_path.read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert script_path.stat().st_mode & stat.S_IXUSR
    assert "set -euo pipefail" in script
    assert "usage: $0 <approved-release-tag> [release-channel]" in script
    assert 'LOCAL_ARTIFACT_PROOF_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in script
    assert 'echo "localArtifactProofAt=${LOCAL_ARTIFACT_PROOF_AT}"' in script
    assert 'uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}"' in script
    assert 'wheel="dist/agent_lb-${pypi_version}-py3-none-any.whl"' in script
    assert 'sdist="dist/agent_lb-${pypi_version}.tar.gz"' in script
    assert "tar -tzf" in script
    assert "README.md" in script
    assert "pyproject\\.toml" in script
    assert "shasum -a 256 README.md" in script
    assert "sdist README.md does not match repository README.md" in script
    assert "dev-only top-level paths" in script
    assert "unzip -p" in script
    assert 'release_version="$(printf' in script
    assert 'rg --fixed-strings "Name: agent-lb"' in script
    assert 'rg --fixed-strings "Version: ${pypi_version}"' in script
    assert 'rg --fixed-strings "Maintainer: Alex Neyman"' in script
    assert 'rg --fixed-strings "ghcr.io/aneym/agent-lb:${release_version}"' in script
    assert "uvx --from twine==6.2.0 twine check" in script
    assert 'echo "local release artifact proof passed at ${LOCAL_ARTIFACT_PROOF_AT}"' in script
    for mutating_command in (
        "git commit",
        "git push",
        "gh pr create",
        "gh repo edit",
        "gh release edit",
        "gh workflow run",
        "gh api",
    ):
        assert mutating_command not in script


def test_public_release_drift_scan_script_stays_read_only() -> None:
    script_path = ROOT / "scripts/public-release-drift-scan.sh"
    script = script_path.read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert script_path.stat().st_mode & stat.S_IXUSR
    assert "set -euo pipefail" in script
    assert "scan_fixed" in script
    assert "scan_regex" in script
    assert "ghcr.io/aneym/agent-lb:latest" in script
    assert "https://github.com/Soju06/agent-lb/releases/latest" in script
    assert "apis-assigned-accounts|codex-session-retag-" in script
    assert "Codex/ChatGPT multiple account load balancer" in script
    assert "README.md" in script
    assert ".agents/skills/agent-lb-account-operator" in script
    assert "openspec/specs" in script
    assert "public release drift scan passed" in script
    for mutating_command in (
        "git commit",
        "git push",
        "gh pr create",
        "gh repo edit",
        "gh release edit",
        "gh workflow run",
        "gh api",
    ):
        assert mutating_command not in script


def test_public_release_pr_head_proof_script_fails_closed_and_stays_read_only() -> None:
    script_path = ROOT / "scripts/public-release-pr-head-proof.sh"
    script = script_path.read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert script_path.stat().st_mode & stat.S_IXUSR
    assert "set -euo pipefail" in script
    assert "usage: $0 <pr-number> [repo]" in script
    assert 'REPO="${2:-aneym/agent-lb}"' in script
    assert 'PR_HEAD_PROOF_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in script
    assert 'echo "prHeadProofAt=${PR_HEAD_PROOF_AT}"' in script
    assert "gh pr view" in script
    assert (
        "--json number,state,isDraft,baseRefName,headRefName,headRefOid,"
        "headRepositoryOwner,mergeable,mergeStateStatus,reviewDecision,labels,url" in script
    )
    assert 'pr_head_sha="$(printf' in script
    assert '.headRefOid // ""' in script
    assert "pull request headRefOid was missing" in script
    assert 'pr_head_short="${pr_head_sha:0:12}"' in script
    assert 'echo "pr_head_sha=${pr_head_sha}"' in script
    assert 'echo "pr_head_short=${pr_head_short}"' in script
    assert '.state == "OPEN"' in script
    assert ".isDraft == false" in script
    assert '.baseRefName == "main"' in script
    assert ".headRepositoryOwner.login == $owner" in script
    assert '.mergeable == "MERGEABLE"' in script
    assert '.mergeStateStatus == "CLEAN"' in script
    assert "🤖 codex: ok" in script
    assert "🤖 codex: needs work" in script
    assert "gh pr checks" in script
    assert "--required" in script
    assert "CI Required" in script
    assert "jq -e" in script
    assert ".github/scripts/sync_codex_ok_labels.py" in script
    assert "--no-trigger-missing-codex" in script
    assert "--no-approve-workflow-runs" in script
    assert 'require_codex_fragment "head=${pr_head_short}"' in script
    assert "checks=success" in script
    assert "merge=CLEAN" in script
    assert "review=clean" in script
    assert "ok=True->True/keep" in script
    assert "needs_work=False->False/keep" in script
    assert 'echo "PR head proof passed for ${REPO}#${PR_NUMBER} at ${PR_HEAD_PROOF_AT}"' in script
    for mutating_command in (
        "git commit",
        "git push",
        "gh pr create",
        "gh repo edit",
        "gh release edit",
        "gh workflow run",
        "gh api",
    ):
        assert mutating_command not in script


def test_public_release_live_snapshot_script_stays_read_only() -> None:
    script_path = ROOT / "scripts/public-release-live-snapshot.sh"
    script = script_path.read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert script_path.stat().st_mode & stat.S_IXUSR
    assert "set -uo pipefail" in script
    assert "usage: $0 <approved-release-tag> [release-channel]" in script
    assert 'SNAPSHOT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in script
    assert "SNAPSHOT_OPTIONAL_FAILURES=0" in script
    assert "SNAPSHOT_OPTIONAL_FAILURE_NAMES=()" in script
    assert "SNAPSHOT_BLOCKING_NAMES=()" in script
    assert 'echo "snapshotAt=${SNAPSHOT_AT}"' in script
    assert script.count("SNAPSHOT_OPTIONAL_FAILURES=$((SNAPSHOT_OPTIONAL_FAILURES + 1))") == 1
    assert 'SNAPSHOT_OPTIONAL_FAILURE_NAMES+=("${check_name}")' in script
    assert 'SNAPSHOT_BLOCKING_NAMES+=("$1")' in script
    assert "run_capture_optional release-view gh release view" in script
    assert "run_capture_optional repo-metadata gh repo view" in script
    assert 'EXPECTED_DESCRIPTION="ChatGPT and Claude account load balancer & proxy with usage tracking' in script
    assert 'EXPECTED_HOMEPAGE="https://github.com/aneym/agent-lb"' in script
    assert "EXPECTED_TOPICS_JSON=" in script
    assert "EXPECTED_PYPI_PROJECT_URLS_JSON=" in script
    assert '"Repository":"https://github.com/aneym/agent-lb"' in script
    assert 'uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}"' in script
    assert 'is_prerelease="$(printf' in script
    assert 'wheel_asset="agent_lb-${pypi_version}-py3-none-any.whl"' in script
    assert 'sdist_asset="agent_lb-${pypi_version}.tar.gz"' in script
    assert 'release_url="https://github.com/aneym/agent-lb/releases/tag/${RELEASE_TAG}"' in script
    assert 'release_name="Release ${RELEASE_TAG}"' in script
    assert "--arg expected_tag" in script
    assert ".tagName == $expected_tag" in script
    assert "--arg expected_name" in script
    assert ".name == $expected_name" in script
    assert "--arg expected_url" in script
    assert ".url == $expected_url" in script
    assert '(.publishedAt // "") != ""' in script
    assert "--argjson expected_prerelease" in script
    assert ".isPrerelease == $expected_prerelease" in script
    assert ".isDraft == false" in script
    assert '--arg wheel_asset "${wheel_asset}"' in script
    assert '--arg sdist_asset "${sdist_asset}"' in script
    assert "index($wheel_asset) != null and index($sdist_asset) != null" in script
    assert "gh pr list --repo aneym/agent-lb --state open" in script
    assert "gh run list --repo aneym/agent-lb --branch main --limit 10" in script
    assert "gh release list --repo aneym/agent-lb --limit 10" in script
    assert 'gh release view "${RELEASE_TAG}" --repo aneym/agent-lb' in script
    assert "assets,body" in script
    assert "gh repo view aneym/agent-lb" in script
    assert "curl -fsS https://pypi.org/pypi/agent-lb/json" in script
    assert "jq -e --arg pypi_version '${pypi_version}'" in script
    assert ".info.version == \\$pypi_version" in script
    assert ".info.summary == \\$expected_description" in script
    assert "(.info.project_urls // {}) == \\$expected_project_urls" in script
    assert ".releases[\\$pypi_version][]?.filename" in script
    assert "python3 -m pip index versions agent-lb" in script
    assert 'docker manifest inspect "ghcr.io/aneym/agent-lb:${release_version}"' in script
    assert 'docker manifest inspect "ghcr.io/aneym/agent-lb:${IMAGE_ALIAS}"' in script
    assert 'docker manifest inspect "ghcr.io/aneym/charts/agent-lb:${release_version}"' in script
    assert 'echo "snapshotOptionalFailures=${SNAPSHOT_OPTIONAL_FAILURES}"' in script
    assert 'echo "snapshotOptionalFailureNames=[]"' in script
    assert 'echo "snapshotOptionalFailureNames=${optional_failure_names}"' in script
    assert 'echo "snapshotBlockingNames=[]"' in script
    assert 'echo "snapshotBlockingNames=${blocking_names}"' in script
    assert "repo-description" in script
    assert "repo-homepage" in script
    assert "repo-topics" in script
    assert "repo-visibility" in script
    assert "repo-private" in script
    assert "repo-archived" in script
    assert "repo-default-branch" in script
    assert '.visibility == "PUBLIC"' in script
    assert ".isPrivate == false" in script
    assert ".isArchived == false" in script
    assert '.defaultBranchRef.name == "main"' in script
    assert "release-tag" in script
    assert "release-name" in script
    assert "release-url" in script
    assert "release-published" in script
    assert "release-prerelease" in script
    assert "release-draft" in script
    assert "release-assets" in script
    assert "release-body" in script
    assert "pypi-json" in script
    assert "pip-index" in script
    assert "ghcr-image-tag" in script
    assert "ghcr-image-alias" in script
    assert "ghcr-helm-chart" in script
    assert 'echo "snapshot complete at ${SNAPSHOT_AT}"' in script
    for mutating_command in (
        "git commit",
        "git push",
        "gh pr create",
        "gh repo edit",
        "gh release edit",
        "gh workflow run",
        "gh api",
    ):
        assert mutating_command not in script


def test_public_release_publish_readiness_script_fails_closed_before_publication() -> None:
    script_path = ROOT / "scripts/public-release-publish-readiness.sh"
    script = script_path.read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert script_path.stat().st_mode & stat.S_IXUSR
    assert "set -euo pipefail" in script
    assert "usage: $0 <approved-release-tag> [release-channel]" in script
    assert 'PUBLISH_READINESS_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in script
    assert 'echo "publishReadinessAt=${PUBLISH_READINESS_AT}"' in script
    assert 'head_sha="$(git rev-parse HEAD)"' in script
    assert 'tag_sha="$(git rev-parse "${RELEASE_TAG}^{}")"' in script
    assert 'current_branch="$(git branch --show-current)"' in script
    assert 'main_sha="$(git rev-parse main)"' in script
    assert 'echo "current_branch=${current_branch}"' in script
    assert 'echo "main_sha=${main_sha}"' in script
    assert 'if [ "${current_branch}" != "main" ]; then' in script
    assert "publish readiness must run from local main" in script
    assert "${current_branch:-detached}" in script
    assert 'if [ "${head_sha}" != "${main_sha}" ]; then' in script
    assert "local main does not point at HEAD" in script
    assert script.index('if [ "${head_sha}" != "${main_sha}" ]; then') < script.index(
        'if [ "${head_sha}" != "${tag_sha}" ]; then'
    )
    assert 'if [ "${head_sha}" != "${tag_sha}" ]; then' in script
    assert "does not point at HEAD" in script
    assert script.index('uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}"') < script.index(
        'dirty_paths="$(git status --porcelain)"'
    )
    assert 'dirty_paths="$(git status --porcelain)"' in script
    assert 'if [ -n "${dirty_paths}" ]; then' in script
    assert "dirty_count=\"$(printf '%s\\n' \"${dirty_paths}\" | wc -l | tr -d ' ')\"" in script
    assert 'echo "dirty_count=${dirty_count}" >&2' in script
    assert "working tree is dirty" in script
    assert 'uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}"' in script
    assert "gh pr list --repo aneym/agent-lb --state open" in script
    assert 'open_prs_json="$(gh pr list --repo aneym/agent-lb --state open' in script
    assert 'open_pr_count="$(printf' in script
    assert 'echo "open_pr_count=${open_pr_count}"' in script
    assert "gh run list --repo aneym/agent-lb --branch main --limit 10" in script
    assert 'main_runs_json="$(gh run list --repo aneym/agent-lb --branch main --limit 10' in script
    assert 'current_head_main_run_count="$(' in script
    assert "[.[] | select(.headSha == $head_sha)] | length" in script
    assert 'echo "current_head_main_run_count=${current_head_main_run_count}"' in script
    assert 'if [ "${current_head_main_run_count}" -eq 0 ]; then' in script
    assert "no current-head main workflow runs found" in script
    assert "non_green_current_head_runs" in script
    assert "select(.headSha == $head_sha)" in script
    assert '.status != "completed"' in script
    assert '$conclusion != "success" and $conclusion != "skipped" and $conclusion != "neutral"' in script
    assert "current-head main workflow runs are not publish-ready" in script
    assert 'echo "publish readiness passed at ${PUBLISH_READINESS_AT}"' in script
    for mutating_command in (
        "git commit",
        "git push",
        "gh pr create",
        "gh repo edit",
        "gh release edit",
        "gh workflow run",
        "gh api",
    ):
        assert mutating_command not in script


def test_public_release_postpublish_proof_script_fails_closed_for_public_artifacts() -> None:
    script_path = ROOT / "scripts/public-release-postpublish-proof.sh"
    script = script_path.read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert script_path.stat().st_mode & stat.S_IXUSR
    assert "set -euo pipefail" in script
    assert "usage: $0 <approved-release-tag> [release-channel]" in script
    assert 'POSTPUBLISH_PROOF_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in script
    assert 'echo "postpublishProofAt=${POSTPUBLISH_PROOF_AT}"' in script
    assert 'uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}"' in script
    assert "curl -fsS https://pypi.org/pypi/agent-lb/json" in script
    assert 'pypi_json="$(curl -fsS https://pypi.org/pypi/agent-lb/json)"' in script
    assert 'echo "expectedPypiVersion=${pypi_version}"' in script
    assert 'echo "expectedPypiWheelAsset=${wheel_asset}"' in script
    assert 'echo "expectedPypiSdistAsset=${sdist_asset}"' in script
    assert 'echo "expectedPypiSummary=${EXPECTED_DESCRIPTION}"' in script
    assert 'echo "expectedPypiProjectUrls=${EXPECTED_PYPI_PROJECT_URLS_JSON}"' in script
    assert "printf '%s\\n' \"${pypi_json}\"" in script
    assert "jq -e" in script
    assert '--arg pypi_version "${pypi_version}"' in script
    assert ".info.version == $pypi_version" in script
    assert ".info.summary == $expected_description" in script
    assert "(.info.project_urls // {}) == $expected_project_urls" in script
    assert ".releases[$pypi_version][]?.filename" in script
    assert 'python3 -m pip index versions agent-lb | rg --fixed-strings "${pypi_version}"' in script
    assert 'docker manifest inspect "ghcr.io/aneym/agent-lb:${release_version}"' in script
    assert 'docker manifest inspect "ghcr.io/aneym/agent-lb:${IMAGE_ALIAS}"' in script
    assert 'docker manifest inspect "ghcr.io/aneym/charts/agent-lb:${release_version}"' in script
    assert "EXPECTED_DESCRIPTION=" in script
    assert "ChatGPT and Claude account load balancer & proxy" in script
    assert 'EXPECTED_HOMEPAGE="https://github.com/aneym/agent-lb"' in script
    assert "EXPECTED_PYPI_PROJECT_URLS_JSON=" in script
    assert '"Issues":"https://github.com/aneym/agent-lb/issues"' in script
    assert '"openclaw"' in script
    assert "gh repo view aneym/agent-lb" in script
    assert "description,homepageUrl,repositoryTopics,isArchived,isPrivate,visibility,url,defaultBranchRef" in script
    assert ".description == $expected_description" in script
    assert ".homepageUrl == $expected_homepage" in script
    assert '.visibility == "PUBLIC"' in script
    assert '.defaultBranchRef.name == "main"' in script
    assert "([.repositoryTopics[]? | topic_name] | sort) == ($expected_topics | sort)" in script
    assert 'gh release view "${RELEASE_TAG}" --repo aneym/agent-lb' in script
    assert "tagName,name,assets,isPrerelease,isDraft,publishedAt,url,body" in script
    assert ".tagName == $expected_tag" in script
    assert 'release_url="https://github.com/aneym/agent-lb/releases/tag/${RELEASE_TAG}"' in script
    assert 'release_name="Release ${RELEASE_TAG}"' in script
    assert '--arg expected_name "${release_name}"' in script
    assert '--arg expected_url "${release_url}"' in script
    assert ".name == $expected_name" in script
    assert ".url == $expected_url" in script
    assert '(.publishedAt // "") != ""' in script
    assert 'wheel_asset="agent_lb-${pypi_version}-py3-none-any.whl"' in script
    assert 'sdist_asset="agent_lb-${pypi_version}.tar.gz"' in script
    assert '--arg wheel_asset "${wheel_asset}"' in script
    assert '--arg sdist_asset "${sdist_asset}"' in script
    assert "index($wheel_asset) != null and index($sdist_asset) != null" in script
    assert "(.assets | length) > 0" not in script
    assert 'contains("Beta prerelease for Agent LB public-release readiness.")' in script
    assert 'contains("duplicate column name")' in script
    assert 'contains("tests/integration/test_migrations.py currently fails")' in script
    assert '--argjson expected_prerelease "${is_prerelease}"' in script
    assert 'echo "post-publish proof complete at ${POSTPUBLISH_PROOF_AT}"' in script
    for mutating_command in (
        "git commit",
        "git push",
        "gh pr create",
        "gh repo edit",
        "gh release edit",
        "gh workflow run",
    ):
        assert mutating_command not in script


def test_handoff_pull_request_draft_is_paste_ready_without_mutations() -> None:
    release = _current_release()
    handoff = (ROOT / "HANDOFF.md").read_text()
    draft = _handoff_pull_request_draft(handoff)
    normalized_draft = " ".join(draft.split())

    assert draft.startswith("Title:\nchore(release): prepare public beta release readiness")
    assert "Related issue / discussion:" in draft
    assert "Fill this in" not in draft
    assert "No tracked issue identified in the local readiness pass" in draft
    assert "ChatGPT + Claude account pooling" in draft
    assert "trusted-proxy API-key bypasses" in draft
    assert "existing\n  prerelease reruns still dispatch PyPI, Docker, Helm" in draft
    assert "Publication is approval-gated and not run from this PR draft" in draft
    assert LIVE_PUBLIC_SNAPSHOT_AT in draft
    assert "release title, public URL, published timestamp" in draft
    assert "./scripts/public-release-live-snapshot.sh" in draft
    assert f"`uv run python -m scripts.verify_release_version --tag {release.tag} --require-channel beta`" in draft
    assert f"`pypi_version={release.pypi_version}`" in draft
    assert "PyPI `agent-lb` still returns 404" in normalized_draft
    assert "GHCR image/chart manifests" in normalized_draft
    assert "GitHub prerelease has no assets" in normalized_draft
    assert "snapshotOptionalFailures=5" in draft
    assert "snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart" in draft
    assert (
        "snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,"
        "pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart" in draft
    )
    assert "Use the local artifact proof to verify the selected wheel/sdist" in normalized_draft
    assert "first use the publish-readiness guard" in normalized_draft
    assert "Once this PR exists, run the PR-head proof helper" in normalized_draft
    assert "run the runtime proof to close the daemon release-link blocker" in normalized_draft
    assert "then use the post-publish proof script in this handoff" in normalized_draft.lower()
    assert "./scripts/public-release-local-artifact-proof.sh <approved-release-tag>" in draft
    assert "2880x1800 JPEG" in draft
    assert "./scripts/validate-active-openspec-changes.sh" in draft
    assert "./scripts/public-release-pr-head-proof.sh <pr-number>" in draft
    assert "./scripts/public-release-runtime-proof.sh <approved-release-tag>" in draft

    for screenshot_path in (
        "docs/screenshots/dashboard.jpg",
        "docs/screenshots/dashboard-dark.jpg",
        "docs/screenshots/accounts.jpg",
        "docs/screenshots/accounts-dark.jpg",
        "docs/screenshots/settings.jpg",
        "docs/screenshots/settings-dark.jpg",
        "docs/screenshots/login.jpg",
    ):
        assert screenshot_path in draft

    for change in (
        "hide-canceled-subscription-accounts",
        "fix-runtime-release-repository",
        "harden-trusted-proxy-api-key-auth",
        "fix-anthropic-quota-selection-diagnostics",
        "fix-menubar-limit-status-sync",
        "require-beta-candidate-validation",
    ):
        assert f"- `{change}`" in draft

    for mutating_command in (
        "git commit",
        "git push",
        "gh pr create",
        "gh repo edit",
        "gh release edit",
        "gh workflow run",
        "gh api",
    ):
        assert mutating_command not in draft


def test_handoff_replacement_prerelease_notes_match_current_release_evidence() -> None:
    handoff = (ROOT / "HANDOFF.md").read_text()
    notes = _handoff_replacement_prerelease_notes(handoff)

    assert notes.startswith("Beta prerelease for Agent LB public-release readiness.")
    assert "pricing/warmup beta body" not in notes
    assert "duplicate column name" not in notes
    assert "subscription_status" not in notes
    assert "tests/integration/test_migrations.py currently fails" not in notes
    assert "GitHub/PyPI/GHCR publication is approval-gated" in notes
    assert "3675 passed, 43 skipped, 4 warnings in 213.60s" in notes
    assert "exactly one live upstream/account smoke checklist choice" in notes
    assert "./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight" in notes
    assert PREFLIGHT_AT in notes
    assert PREFLIGHT_PUBLIC_SNAPSHOT_AT in notes
    assert f"snapshotAt={PREFLIGHT_PUBLIC_SNAPSHOT_AT}" in notes
    assert LIVE_PUBLIC_SNAPSHOT_AT in notes
    assert f"snapshotAt={LIVE_PUBLIC_SNAPSHOT_AT}" in notes
    assert STANDALONE_PUBLIC_SNAPSHOT_AT in notes
    assert f"snapshotAt={STANDALONE_PUBLIC_SNAPSHOT_AT}" in notes
    assert f"localArtifactProofAt={PREFLIGHT_LOCAL_ARTIFACT_PROOF_AT}" in notes
    assert "agent_lb-1.20.0b3-py3-none-any.whl" in notes
    assert "agent_lb-1.20.0b3.tar.gz" in notes
    assert "bash -n scripts/public-release-drift-scan.sh scripts/public-release-preflight.sh" in notes
    assert "passed read-only public release drift scan" in notes
    assert "./scripts/public-release-local-artifact-proof.sh v1.20.0-beta.3" in notes
    assert "uv run pytest -q tests/unit/test_guard_beta_release.py` -> `10 passed`" in notes
    assert "uv run pytest -q tests/unit/test_public_release_docs.py` -> `79 passed`" in notes
    assert "uv run pytest -q tests/unit/test_public_release_docs.py" in notes
    assert (
        "uv run pytest -q tests/unit/test_release_versions.py "
        "tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py` -> `109 passed`" in notes
    )
    assert "./scripts/public-release-runtime-proof.sh v1.20.0-beta.3" in notes
    assert "expected non-zero before approved restart/reinstall" in notes
    assert "runtimeProofAt=2026-06-14T07:19:03Z" in notes
    assert "healthy daemon still serves the old upstream release URL" in notes
    assert "./scripts/validate-active-openspec-changes.sh` -> `validated 54 active changes`" in notes
    assert "2026-06-14T04:51:13Z" not in notes
    assert "uv run pytest -q tests/unit/test_public_release_docs.py` -> `77 passed`" not in notes
    assert (
        "uv run pytest -q tests/unit/test_release_versions.py "
        "tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py` -> `107 passed`" not in notes
    )
    assert "tests/unit/test_k8s_version_policy.py` -> `79 passed`" not in notes
    assert "86 passed" not in notes
    assert "99 passed" not in notes
    assert "10 passed in " not in notes
    assert "43 passed" not in notes
    assert "44 passed" not in notes
    assert "45 passed in " not in notes
    assert "npx --yes @fission-ai/openspec@latest validate --specs" in notes
    assert "gh release edit v1.20.0-beta.3" in handoff
    assert "--notes-file /tmp/agent-lb-v1.20.0-beta.3-notes.md" in handoff
    assert "--prerelease" in handoff


def test_handoff_post_publish_proof_commands_fail_closed_for_public_artifacts() -> None:
    release = _current_release()
    handoff = (ROOT / "HANDOFF.md").read_text()
    proof = _handoff_post_publish_proof_commands(handoff)

    assert "./scripts/public-release-postpublish-proof.sh <approved-release-tag>" in handoff
    assert f'expected_pypi_version="{release.pypi_version}"' in proof
    assert f'expected_pypi_wheel_asset="agent_lb-{release.pypi_version}-py3-none-any.whl"' in proof
    assert f'expected_pypi_sdist_asset="agent_lb-{release.pypi_version}.tar.gz"' in proof
    assert 'echo "expectedPypiVersion=${expected_pypi_version}"' in proof
    assert 'echo "expectedPypiWheelAsset=${expected_pypi_wheel_asset}"' in proof
    assert 'echo "expectedPypiSdistAsset=${expected_pypi_sdist_asset}"' in proof
    assert 'pypi_json="$(curl -fsS https://pypi.org/pypi/agent-lb/json)"' in proof
    assert "printf '%s\\n' \"${pypi_json}\"" in proof
    assert '--arg pypi_version "${expected_pypi_version}"' in proof
    assert '--arg wheel_asset "${expected_pypi_wheel_asset}"' in proof
    assert '--arg sdist_asset "${expected_pypi_sdist_asset}"' in proof
    assert ".info.version == $pypi_version" in proof
    assert ".releases[$pypi_version][]?.filename" in proof
    assert f"python3 -m pip index versions agent-lb | rg --fixed-strings '{release.pypi_version}'" in proof
    assert f"docker manifest inspect ghcr.io/aneym/agent-lb:{release.version}" in proof
    assert "docker manifest inspect ghcr.io/aneym/agent-lb:beta" in proof
    assert f"docker manifest inspect ghcr.io/aneym/charts/agent-lb:{release.version}" in proof
    assert (
        "gh repo view aneym/agent-lb --json "
        "description,homepageUrl,repositoryTopics,isArchived,isPrivate,visibility,url,defaultBranchRef"
    ) in proof
    assert PUBLIC_REPO_DESCRIPTION in proof
    assert PUBLIC_REPO_HOMEPAGE in proof
    assert f"gh release view v{release.version} --repo aneym/agent-lb" in proof
    assert "tagName,name,assets,isPrerelease,isDraft,publishedAt,url,body" in proof
    assert f'expected_release_url="https://github.com/aneym/agent-lb/releases/tag/v{release.version}"' in proof
    assert f'expected_release_name="Release v{release.version}"' in proof
    assert '--arg expected_name "${expected_release_name}"' in proof
    assert '--arg expected_url "${expected_release_url}"' in proof
    assert ".name == $expected_name" in proof
    assert ".url == $expected_url" in proof
    assert '(.publishedAt // "") != ""' in proof
    assert "Beta prerelease for Agent LB public-release readiness." in proof
    assert "duplicate column name" in proof
    assert "tests/integration/test_migrations.py currently fails" in proof
    assert ".isPrerelease == $expected_prerelease" in proof
    assert f'expected_wheel_asset="agent_lb-{release.pypi_version}-py3-none-any.whl"' in proof
    assert f'expected_sdist_asset="agent_lb-{release.pypi_version}.tar.gz"' in proof
    assert '--arg wheel_asset "${expected_wheel_asset}"' in proof
    assert '--arg sdist_asset "${expected_sdist_asset}"' in proof
    assert "index($wheel_asset) != null and index($sdist_asset) != null" in proof
    assert "(.assets | length) > 0" not in proof
    assert f"docker manifest inspect ghcr.io/aneym/agent-lb:{release.version}\n" in proof
    assert "curl -fsS https://pypi.org/pypi/agent-lb/json | jq -r '.info.version'\n" not in proof
    assert "python3 -m pip index versions agent-lb\n" not in proof


def test_goal_and_handoff_live_snapshot_record_public_artifact_blockers() -> None:
    goal = (ROOT / "GOAL.md").read_text()
    handoff = (ROOT / "HANDOFF.md").read_text()

    for text in (goal, handoff):
        normalized = " ".join(text.split())

        assert LIVE_PUBLIC_SNAPSHOT_AT in text
        assert "./scripts/public-release-live-snapshot.sh v1.20.0-beta.3" in text
        assert "snapshotOptionalFailures=5" in text
        assert (
            "snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart" in text
        )
        assert (
            "snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,"
            "pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart" in text
        )
        assert "Codex/ChatGPT multiple account load balancer" in text
        assert "release assets remain `[]`" in normalized or "with no assets" in normalized
        assert "Hosted repo homepage is empty" in text
        assert "Recent branch workflow runs returned `[]`" in text
        assert "PyPI JSON for `agent-lb` returned 404" in normalized
        assert "python3 -m pip index versions agent-lb" in normalized
        assert "found no matching distribution" in normalized
        assert "denied/not visible" in normalized
        assert "./scripts/public-release-pr-head-proof.sh <pr-number>" in text
        assert "No GitHub mutations were made." in text

    normalized_goal = " ".join(goal.split())
    assert f"Latest read-only refresh inside the full preflight on {PREFLIGHT_PUBLIC_SNAPSHOT_AT}" in goal
    assert f"Latest standalone read-only refresh on {STANDALONE_PUBLIC_SNAPSHOT_AT}" in goal
    assert "release title, public release URL, published timestamp" in normalized_goal
    assert "Latest read-only refresh on 2026-06-14T07:11:40Z" not in goal
    assert (
        "Current local evidence from this release-readiness pass, refreshed through the "
        "2026-06-14T08:38:25Z full read-only public-release preflight" in normalized_goal
    )


def test_goal_and_handoff_record_current_pr_head_boundary() -> None:
    goal = (ROOT / "GOAL.md").read_text()
    handoff = (ROOT / "HANDOFF.md").read_text()

    for text in (goal, handoff):
        normalized = " ".join(text.split())

        assert PR_HEAD_SNAPSHOT_AT in text
        assert PR_HEAD_PROOF_AT in text
        assert "gh pr list --repo aneym/agent-lb --state open" in normalized
        assert "gh run list --repo aneym/agent-lb --branch main --limit 10" in normalized
        assert "167" in normalized
        assert "PR-head proof" in normalized
        assert "CI/Codex-review" in normalized
        assert "pr_head_sha" in text
        assert "pr_head_short" in text


def test_goal_and_handoff_record_runtime_daemon_restart_boundary() -> None:
    goal = (ROOT / "GOAL.md").read_text()
    handoff = (ROOT / "HANDOFF.md").read_text()

    for text in (goal, handoff):
        normalized = " ".join(text.split()).lower()

        assert "http://127.0.0.1:2455/health" in text
        assert "http://127.0.0.1:2455/api/runtime/version" in text
        assert '{"status":"ok"}' in text
        assert "`currentVersion` `1.20.0-beta.3`" in text
        assert "https://github.com/Soju06/agent-lb/releases/latest" in text
        assert "https://github.com/aneym/agent-lb/releases/latest" in text
        assert "./scripts/public-release-runtime-proof.sh v1.20.0-beta.3" in text
        assert "2026-06-14T04:35:55.088884Z" in text
        assert "2026-06-14T04:36:45Z" in text
        assert "runtime proof" in normalized
        assert "expected non-zero" in normalized or "fails closed" in normalized
        assert "no restart was performed" in normalized
        assert "approval-gated" in normalized
        assert "pre-candidate runtime" in normalized


def test_handoff_known_remaining_risk_stays_explicitly_incomplete() -> None:
    handoff = (ROOT / "HANDOFF.md").read_text()
    risk = handoff[handoff.index("## Known Remaining Risk") :]
    normalized = " ".join(risk.split())

    assert "Local tests, lint, frontend build, menubar tests, screenshots, active OpenSpec" in normalized
    assert "remaining unchecked OpenSpec tasks are PR/CI/Codex-review gates" in normalized
    assert "need a committed PR head" in normalized
    assert "The release is still not complete" in normalized
    assert "release-ready tree is not committed and tagged" in normalized
    assert "live GitHub metadata is stale" in normalized
    assert "public package/container assets are not visible" in normalized
    assert "healthy live launchd daemon still serves a pre-candidate runtime release URL" in normalized
    assert "./scripts/public-release-runtime-proof.sh v1.20.0-beta.3" in risk
    assert "publishing or release mutation still requires explicit user approval" in normalized


def test_pull_request_template_names_both_provider_protocol_families() -> None:
    template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text()

    assert "provider-faithful" in template
    assert "OpenAI-compatible SDK routes" in template
    assert "Claude Code / Anthropic request shape" in template
    assert "Messages SSE framing" in template
    assert "Anthropic/Claude request/response shape" in template
    assert "codex-faithful" not in template


def test_pull_request_template_requires_visual_proof_for_ui_changes() -> None:
    template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text()

    assert "## Screenshots / output" in template
    assert "Required for dashboard, UI, or public screenshot changes" in template
    assert "before/after\nscreenshots or explain why screenshots are not applicable" in template
    assert "Dashboard/UI-visible changes include screenshots or a clear not-applicable reason" in template
    assert "## Screenshots / output (optional)" not in template


def test_pr_and_contributor_docs_require_release_artifact_evidence() -> None:
    template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text()
    contributing = (ROOT / ".github/CONTRIBUTING.md").read_text()

    for text in (template, contributing):
        assert "candidate SHA/tag" in text
        assert "PyPI" in text
        assert "GHCR" in text
        assert "Helm" in text
        assert "release" in text
        assert "approval-gated" in text

    assert "artifact proof commands" in template
    assert "If publication is approval-gated and not run from this PR" in template
    assert "./scripts/public-release-live-snapshot.sh <approved-release-tag>" in template
    assert "./scripts/public-release-local-artifact-proof.sh <approved-release-tag>" in template
    assert "./scripts/public-release-pr-head-proof.sh <pr-number>" in template
    assert "./scripts/public-release-runtime-proof.sh <approved-release-tag>" in template
    assert "./scripts/public-release-postpublish-proof.sh <approved-release-tag>" in template
    assert "./scripts/public-release-publish-readiness.sh <approved-release-tag>" in template
    assert "Release/package/publication changes include candidate SHA/tag" in template
    assert "local artifact proof" in template
    assert "PR-head proof" in template
    assert "runtime proof" in template
    assert "GitHub release asset check" in contributing
    assert "local wheel/sdist proof" in contributing
    assert "leave the\n   local artifact proof, live blocker snapshot, PR-head proof" in contributing
    assert "./scripts/public-release-live-snapshot.sh <approved-release-tag>" in contributing
    assert "./scripts/public-release-local-artifact-proof.sh <approved-release-tag>" in contributing
    assert "./scripts/public-release-pr-head-proof.sh <pr-number>" in contributing
    assert "./scripts/public-release-runtime-proof.sh <approved-release-tag>" in contributing
    assert "./scripts/public-release-postpublish-proof.sh <approved-release-tag>" in contributing
    assert "./scripts/public-release-publish-readiness.sh <approved-release-tag>" in contributing


def test_pr_and_contributor_docs_require_public_client_onboarding_sync() -> None:
    template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text()
    contributing = (ROOT / ".github/CONTRIBUTING.md").read_text()

    for text in (template, contributing):
        normalized = " ".join(text.split())

        assert "Public client/onboarding" in text
        assert "AGENTS.md" in text
        assert "README.md" in text
        assert "GETTING-STARTED.md" in text
        assert ".agents/skills/get-started/SKILL.md" in text
        assert ".agents/skills/skill-rules.json" in text
        assert "tests/unit/test_public_release_docs.py" in text
        assert "explain why a surface is not affected" in normalized

    assert "client setup, SDK, launcher, OAuth, or endpoint wiring guidance" in contributing
    assert "keep `AGENTS.md`, `README.md`, `GETTING-STARTED.md`" in template


def test_pr_and_contributor_docs_require_public_intake_form_sync() -> None:
    template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text()
    contributing = (ROOT / ".github/CONTRIBUTING.md").read_text()

    for text in (template, contributing):
        normalized = " ".join(text.split())
        lowered = normalized.lower()

        assert "support-intake" in lowered
        assert "release-version" in lowered
        assert "account-plan" in lowered
        assert ".github/ISSUE_TEMPLATE/bug_report.yml" in text
        assert ".github/ISSUE_TEMPLATE/account_quota.yml" in text
        assert ".github/ISSUE_TEMPLATE/feature_request.yml" in text
        assert ".github/DISCUSSION_TEMPLATE/q-and-a.yml" in text
        assert "tests/unit/test_public_release_docs.py" in text
        assert "explain why a surface is not affected" in normalized

    assert "Public support intake stays in sync" in contributing
    assert "Public client, release-version, account-plan, or support-intake changes" in template


def test_pr_and_contributor_docs_require_security_policy_sync() -> None:
    template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text()
    contributing = (ROOT / ".github/CONTRIBUTING.md").read_text()

    for text in (template, contributing):
        normalized = " ".join(text.split())
        lowered = normalized.lower()

        assert "security" in lowered
        assert "support-window" in lowered or "supported versions" in lowered
        assert ".github/SECURITY.md" in text
        assert "README.md" in text
        assert "deploy/helm/agent-lb/README.md" in text
        assert "tests/unit/test_public_release_docs.py" in text
        assert "explain why a surface is not affected" in normalized

    assert "Security support policy stays in sync" in contributing
    assert "Security/support-window changes keep" in template


def test_pr_and_contributor_docs_require_account_operator_sync() -> None:
    template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text()
    contributing = (ROOT / ".github/CONTRIBUTING.md").read_text()

    for text in (template, contributing):
        normalized = " ".join(text.split())
        lowered = normalized.lower()

        assert "account admin" in lowered
        assert "browser-profile" in lowered
        assert "billing" in lowered
        assert "subscription-ledger" in lowered
        assert "pause/reactivate" in lowered
        assert "removal" in lowered
        assert "verification" in lowered
        assert "AGENTS.md" in text
        assert "README.md" in text
        assert "GETTING-STARTED.md" in text
        assert ".agents/skills/agent-lb-account-operator/SKILL.md" in text
        assert ".agents/skills/agent-lb-account-operator/account-profiles.example.json" in text
        assert ".agents/skills/skill-rules.json" in text
        assert "tests/unit/test_public_release_docs.py" in text
        assert "explain why a surface is not affected" in normalized

    assert "Account operator guidance stays in sync" in contributing
    assert (
        "Account admin, browser-profile, billing, subscription-ledger, "
        "pause/reactivate, removal, or verification guidance changes"
    ) in template


def test_contributing_docs_explain_beta_release_candidate_flow() -> None:
    contributing = (ROOT / ".github/CONTRIBUTING.md").read_text()

    assert "### Beta release candidates" in contributing
    assert "`Sync Beta Release PR` opens or updates a `release/beta-*` PR" in contributing
    assert "validation checklist tied to a\n   specific candidate SHA" in contributing
    assert "backend, frontend, package, container," in contributing
    assert "and exactly one live upstream/account smoke option before merge" in contributing
    assert "triggers `Publish Beta Release`" in contributing
    assert "refuses to publish if the checklist evidence is missing or stale" in contributing
    assert "GitHub prerelease plus PyPI, Docker, and Helm\n   artifacts" in contributing
    assert "Do not manually dispatch beta publishing for a dirty local tree" in contributing


def test_public_release_package_metadata_names_fork_maintainer() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = pyproject["project"]

    assert {"name": "Soju06", "email": "qlskssk@gmail.com"} in project["authors"]
    assert {"name": "Alex Neyman"} in project["maintainers"]


def test_public_release_package_metadata_points_to_public_fork() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = pyproject["project"]

    assert project["description"] == PUBLIC_REPO_DESCRIPTION
    assert "Development Status :: 4 - Beta" in project["classifiers"]
    assert "Development Status :: 3 - Alpha" not in project["classifiers"]
    assert project["urls"] == {
        "Homepage": "https://github.com/aneym/agent-lb",
        "Repository": "https://github.com/aneym/agent-lb",
        "Issues": "https://github.com/aneym/agent-lb/issues",
        "Releases": "https://github.com/aneym/agent-lb/releases",
    }


def test_public_release_package_keywords_name_public_surfaces() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    keywords = set(pyproject["project"]["keywords"])

    assert {"openai", "chatgpt", "anthropic", "claude"} <= keywords
    assert {"codex", "opencode", "openclaw"} <= keywords


def test_beta_readme_install_examples_use_published_prerelease_artifacts() -> None:
    release = _current_release()
    if not release.is_prerelease:
        return

    readme = (ROOT / "README.md").read_text()

    assert f'uvx --from "agent-lb=={release.pypi_version}" agent-lb' in readme
    assert f"ghcr.io/aneym/agent-lb:{release.version}" in readme
    assert "ghcr.io/aneym/agent-lb:latest" not in readme


def test_beta_runtime_portability_context_uses_prerelease_docker_image() -> None:
    release = _current_release()
    if not release.is_prerelease:
        return

    context = (ROOT / "openspec/specs/runtime-portability/context.md").read_text()

    assert f"ghcr.io/aneym/agent-lb:{release.version}" in context
    assert "ghcr.io/aneym/agent-lb:latest" not in context


def test_readme_source_checkout_distinguishes_api_from_unbuilt_dashboard() -> None:
    readme = (ROOT / "README.md").read_text()
    source_section = _readme_section(readme, "### Source checkout", "### Published beta artifacts")
    published_section = _readme_section(readme, "### Published beta artifacts", "## Remote Setup")

    assert "does not include built\ndashboard assets" in source_section
    assert "GETTING-STARTED.md#4-connect-accounts--one-at-a-time" in source_section
    assert "scripts/anthropic-auth.sh start" in source_section
    assert "scripts/openai-auth.sh start" in source_section
    assert "cd frontend && bun install && bun run build" in source_section
    assert "Open [localhost:2455]" not in source_section
    assert "Open [localhost:2455](http://localhost:2455)" in published_section


def test_beta_helm_oci_examples_pin_prerelease_version() -> None:
    release = _current_release()
    if not release.is_prerelease:
        return

    for path in (ROOT / "README.md", ROOT / "deploy/helm/agent-lb/README.md"):
        blocks = _helm_oci_command_blocks(path.read_text())
        assert blocks
        for block in blocks:
            assert f"--version {release.version}" in block, block
            assert "--devel" in block, block


def test_beta_helm_docs_distinguish_oci_artifacts_from_source_install() -> None:
    release = _current_release()
    if not release.is_prerelease:
        return

    readme_kubernetes = _readme_section(
        (ROOT / "README.md").read_text(),
        "## Kubernetes",
        "\n\nFast Mode",
    )
    helm_readme = (ROOT / "deploy/helm/agent-lb/README.md").read_text()

    for text in (readme_kubernetes, helm_readme):
        normalized = " ".join(text.split())

        assert "approval-gated" in normalized
        assert "publish" in normalized
        assert "chart artifact" in normalized
        assert "helm dependency build deploy/helm/agent-lb/" in text

    assert "local chart source" in readme_kubernetes
    assert "From source" in helm_readme
    assert "helm upgrade --install agent-lb deploy/helm/agent-lb/" in helm_readme


def test_release_workflow_prerelease_notes_use_prerelease_install_commands() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()

    assert 'echo "uvx --from \\"agent-lb==${PYPI_VERSION}\\" agent-lb"' in workflow
    assert 'echo "docker pull ghcr.io/${GITHUB_REPOSITORY}:${TAG_VERSION}"' in workflow
    assert (
        'echo "helm install agent-lb oci://ghcr.io/aneym/charts/agent-lb --version ${TAG_VERSION} --devel"'
    ) in workflow


def test_release_workflow_uploads_only_expected_wheel_and_sdist() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()

    assert "Verify dist artifact names" in workflow
    assert '["python", "-m", "scripts.verify_release_version", "--tag", os.environ["RELEASE_TAG"]]' in workflow
    assert 'f"agent_lb-{pypi_version}-py3-none-any.whl"' in workflow
    assert 'f"agent_lb-{pypi_version}.tar.gz"' in workflow
    assert 'actual = {path.name for path in Path("dist").iterdir() if path.is_file()}' in workflow
    assert "if actual != expected:" in workflow
    assert "unexpected dist artifacts" in workflow
    assert "dist artifacts verified:" in workflow
    assert workflow.index("Verify dist artifact names") < workflow.index("Upload dist artifacts")
    assert workflow.index("Verify dist artifact names") < workflow.index('files: "dist/*"')


def test_publish_beta_release_notes_use_prerelease_install_commands() -> None:
    workflow = (ROOT / ".github/workflows/publish-beta-release.yml").read_text()

    assert 'uvx --from "agent-lb==${PYPI_VERSION}" agent-lb' in workflow
    assert "docker pull ghcr.io/${GITHUB_REPOSITORY}:${VERSION}" in workflow
    assert (
        "helm install agent-lb oci://ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/charts/agent-lb --version ${VERSION} --devel"
    ) in workflow
    assert 'gh release edit "${TAG}"' in workflow
    assert 'gh release create "${TAG}"' in workflow
    assert "--prerelease" in workflow


def test_publish_beta_release_dispatches_artifacts_for_existing_prerelease() -> None:
    workflow = (ROOT / ".github/workflows/publish-beta-release.yml").read_text()

    assert "actions: write" in workflow
    existing_release_branch = workflow[workflow.index('if gh release view "${TAG}"') : workflow.index("else")]
    assert 'gh release edit "${TAG}"' in existing_release_branch
    assert "gh workflow run release.yml" in existing_release_branch
    assert '--repo "${GITHUB_REPOSITORY}"' in existing_release_branch
    assert '--ref "${TAG}"' in existing_release_branch
    assert '-f "tag=${TAG}"' in existing_release_branch


def test_release_workflow_stable_notes_use_latest_safe_install_commands() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()

    assert "type=raw,value=latest,enable=${{ needs.release-metadata.outputs.is_prerelease == 'false' }}" in workflow
    assert "echo 'uvx agent-lb'" in workflow
    assert ('echo "helm install agent-lb oci://ghcr.io/aneym/charts/agent-lb --version ${TAG_VERSION}"') in workflow


def test_release_workflow_prerelease_docker_metadata_publishes_channel_alias() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()

    assert (
        "type=raw,value=${{ needs.release-metadata.outputs.channel }},"
        "enable=${{ needs.release-metadata.outputs.is_prerelease == 'true' }}"
    ) in workflow


def test_release_workflow_concurrency_is_scoped_to_release_tag() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())

    assert (
        workflow["concurrency"]["group"]
        == "${{ github.workflow }}-${{ github.event.release.tag_name || github.event.inputs.tag || github.ref }}"
    )


def test_helm_chart_metadata_describes_public_fork() -> None:
    chart = yaml.safe_load((ROOT / "deploy/helm/agent-lb/Chart.yaml").read_text())

    assert "ChatGPT and Claude account load balancer" in chart["description"]
    assert {"openai", "anthropic", "claude", "chatgpt"} <= set(chart["keywords"])
    assert {"codex", "opencode", "openclaw"} <= set(chart["keywords"])
    assert {"name": "Alex Neyman"} in chart["maintainers"]


def test_all_contributors_config_targets_public_fork() -> None:
    config = json.loads((ROOT / ".all-contributorsrc").read_text())

    assert config["projectOwner"] == "aneym"
    assert config["projectName"] == "agent-lb"


def test_github_automation_defaults_to_public_fork() -> None:
    check_all_contributors = (ROOT / ".github/scripts/check_all_contributors.py").read_text()
    sync_codex_ok_labels = (ROOT / ".github/scripts/sync_codex_ok_labels.py").read_text()

    assert 'default=os.environ.get("GITHUB_REPOSITORY", "aneym/agent-lb")' in check_all_contributors
    assert '"aneym/agent-lb": AGENT_LB_REQUIRED_CHECKS' in sync_codex_ok_labels
    assert "Soju06/agent-lb" not in check_all_contributors
    assert "Soju06/agent-lb" not in sync_codex_ok_labels


def test_release_codeowners_include_public_fork_owner() -> None:
    codeowners = (ROOT / ".github/CODEOWNERS").read_text().splitlines()

    for release_path in (
        "/.github/release-please-config.json",
        "/.github/release-please-manifest.json",
        "/.github/workflows/release-please.yml",
        "/CHANGELOG.md",
        "/app/__init__.py",
        "/uv.lock",
    ):
        line = _codeowners_line_for_path(codeowners, release_path)
        assert line is not None
        assert "@aneym" in line.split()


def test_agent_git_workflow_convention_matches_fork_branch_policy() -> None:
    agents = (ROOT / "AGENTS.md").read_text()
    workflow = (ROOT / ".agents/conventions/git-workflow.md").read_text()

    assert "fork (`aneym/agent-lb`) stays on `main`" in agents
    assert "upstream (`Soju06/codex-lb`) contributions" in agents
    assert "upstream `Soju06/codex-lb`" in workflow
    assert "upstream `aneym/agent-lb`" not in workflow


def test_agent_onboarding_docs_pin_public_service_contract() -> None:
    for path in (
        ROOT / "GETTING-STARTED.md",
        ROOT / ".agents/skills/get-started/SKILL.md",
    ):
        text = path.read_text()

        assert "com.aneyman.agent-lb" in text
        assert "com.agent-lb" not in text
        assert "127.0.0.1:2455" in text


def test_agents_md_routes_account_operations_to_account_operator_skill() -> None:
    agents = (ROOT / "AGENTS.md").read_text()

    assert "## Account Operations" in agents
    assert "`agent-lb-account-operator` skill" in agents
    assert ".agent-lb/account-profiles.json" in agents
    assert "quota reset checks" in agents
    assert "stuck or\nrate-limited account triage" in agents
    assert "pause/reactivate\nrouting" in agents
    assert "dedicated browser-profile work" in agents


def test_agent_onboarding_docs_name_public_client_surfaces() -> None:
    getting_started = (ROOT / "GETTING-STARTED.md").read_text()
    readme = (ROOT / "README.md").read_text()

    assert "Codex CLI, Claude Code, OpenCode, OpenClaw, and SDKs" in readme
    assert "Anything OpenAI-compatible (OpenCode, OpenClaw, SDKs)" in getting_started
    assert "Anthropic-compatible SDKs" in getting_started
    assert "Vercel AI SDK" in getting_started
    assert "ANTHROPIC_BASE_URL=http://127.0.0.1:2455" in getting_started
    assert "http://127.0.0.1:2455` (root, not `/v1`)" in getting_started
    assert 'base_url = "http://127.0.0.1:2455/backend-api/codex"' in getting_started
    assert "http://127.0.0.1:2455/v1" in getting_started


def test_agent_onboarding_docs_include_discovered_model_v1_smoke() -> None:
    getting_started = (ROOT / "GETTING-STARTED.md").read_text()

    assert 'MODEL_ID="$(curl -fsS http://127.0.0.1:2455/v1/models' in getting_started
    assert "uv run python -c 'import json, sys; print(json.load(sys.stdin)" in getting_started
    assert "http://127.0.0.1:2455/v1/chat/completions" in getting_started
    assert '\\"model\\":\\"${MODEL_ID}\\"' in getting_started


def test_agent_onboarding_docs_keep_claude_subscription_billing_guardrail() -> None:
    for path in (
        ROOT / "GETTING-STARTED.md",
        ROOT / ".agents/skills/get-started/SKILL.md",
    ):
        text = path.read_text()

        assert "ANTHROPIC_AUTH_TOKEN" in text
        assert "ANTHROPIC_API_KEY" in text
        assert "Never set" in text


def test_readme_agent_prompt_keeps_onboarding_guardrails() -> None:
    readme = (ROOT / "README.md").read_text()
    # Collapse hard wraps: the pins protect phrases, not line-break positions.
    prompt = " ".join(_fenced_blocks(readme, "text")[0].split())

    assert "read GETTING-STARTED.md" in prompt
    assert "Claude/ChatGPT accounts ONE AT A TIME" in prompt
    assert "ANTHROPIC_AUTH_TOKEN" in prompt
    assert "ANTHROPIC_API_KEY" in prompt
    assert "subscription billing stays intact" in prompt
    assert "show exact dotfile edits and ask before applying them" in prompt


def test_readme_routes_post_setup_account_ops_to_account_operator() -> None:
    readme = (ROOT / "README.md").read_text()

    assert "post-setup account-specific work" in readme
    assert "quota reset checks" in readme
    assert "stuck or rate-limited\naccount triage" in readme
    assert "pause/reactivate routing" in readme
    assert "browser-profile work" in readme
    assert '**"account operator"**' in readme
    assert "`agent-lb-account-operator` skill" in readme
    assert ".agent-lb/account-profiles.json" in readme


def test_readme_client_setup_table_has_unique_public_clients() -> None:
    readme = (ROOT / "README.md").read_text()
    table = _readme_client_setup_table(readme)
    clients = [_markdown_table_cell(row, 2).strip("*") for row in table]

    assert clients == [
        "Codex CLI",
        "Claude Code",
        "Anthropic Python SDK",
        "OpenCode",
        "OpenClaw",
        "Vercel AI SDK",
        "OpenAI Python SDK",
    ]
    assert len(clients) == len(set(clients))


def test_readme_client_setup_intro_names_provider_surfaces() -> None:
    section = _readme_client_setup_section((ROOT / "README.md").read_text())

    assert "OpenAI-compatible clients use `/v1`" in section
    assert "Codex uses `/backend-api/codex`" in section
    assert "Claude Code uses the Anthropic-compatible base URL" in section
    assert "Anthropic-compatible SDKs use the same root Messages API surface" in section
    assert "Point any OpenAI-compatible client at agent-lb" not in section


def test_sdk_app_wiring_guidance_stays_server_side_and_local() -> None:
    readme_section = _readme_client_setup_section((ROOT / "README.md").read_text())
    getting_started = (ROOT / "GETTING-STARTED.md").read_text()
    skill = (ROOT / ".agents/skills/get-started/SKILL.md").read_text()

    for text in (readme_section, getting_started, skill):
        normalized = " ".join(text.split())
        lowered = normalized.lower()

        assert "Vercel AI SDK" in text
        assert "server-side" in lowered
        assert "browser-direct" in lowered
        assert "deployed" in lowered
        assert "local subscription accounts" in normalized


def test_readme_vercel_ai_sdk_example_uses_server_side_base_url_override() -> None:
    readme = (ROOT / "README.md").read_text()
    section = _readme_section(readme, "<b>Vercel AI SDK</b>", "</details>")
    normalized = " ".join(section.split())

    assert "pnpm add ai @ai-sdk/openai" in section
    assert 'import { createOpenAI } from "@ai-sdk/openai";' in section
    assert 'import { generateText } from "ai";' in section
    assert 'baseURL: process.env.AGENT_LB_BASE_URL ?? "http://127.0.0.1:2455/v1"' in section
    assert 'apiKey: process.env.AGENT_LB_API_KEY ?? "sk-local"' in section
    assert 'model: agentLB.responses("gpt-5.3-codex")' in section
    assert "server-side code" in section
    assert "route handler or server action" in section
    assert "Deployed apps need `AGENT_LB_BASE_URL`" in section
    assert "Vercel AI Gateway" in section
    assert "local subscription accounts" in normalized


def test_readme_anthropic_python_sdk_example_uses_bearer_auth_and_root_base_url() -> None:
    readme = (ROOT / "README.md").read_text()
    section = _readme_section(readme, "<b>Anthropic Python SDK</b>", "</details>")
    normalized = " ".join(section.split())
    lowered = normalized.lower()

    assert "pip install anthropic" in section
    assert "from anthropic import Anthropic" in section
    assert 'base_url=os.environ.get("AGENT_LB_ANTHROPIC_BASE_URL", "http://127.0.0.1:2455")' in section
    assert 'auth_token=os.environ.get("AGENT_LB_API_KEY", "sk-local")' in section
    assert "client.messages.create(" in section
    assert 'model="claude-sonnet-4-20250514"' in section
    assert "Authorization: Bearer" in section
    assert "ANTHROPIC_AUTH_TOKEN" in section
    assert "ANTHROPIC_API_KEY" in section
    assert "AGENT_LB_API_KEY" in section
    assert "server-side code or a trusted local script" in section
    assert "browser-direct code" in lowered
    assert "local subscription accounts" in normalized


def test_agent_skills_pin_public_onboarding_and_account_operator_contracts() -> None:
    getting_started = (ROOT / "GETTING-STARTED.md").read_text()
    skill = (ROOT / ".agents/skills/get-started/SKILL.md").read_text()
    account_operator = (ROOT / ".agents/skills/agent-lb-account-operator/SKILL.md").read_text()
    account_profiles_example = json.loads(
        (ROOT / ".agents/skills/agent-lb-account-operator/account-profiles.example.json").read_text()
    )

    # Collapse hard wraps: the pins protect phrases, not line-break positions.
    skill_text = " ".join(skill.split())
    assert "Follow `GETTING-STARTED.md` at the repo root, top to bottom" in skill_text
    assert "single source of truth" in skill_text
    assert "Claude Code, Codex, OpenCode, OpenClaw, Vercel AI SDK, Anthropic-compatible SDK, or SDK" in skill_text
    assert "discover a model from `/v1/models` first" in skill_text
    assert "Anthropic-compatible SDKs use the root Messages API base URL" in skill_text

    assert "`agent-lb-account-operator` skill" in getting_started
    assert ".agent-lb/account-profiles.json" in getting_started
    assert "quota reset checks" in getting_started
    assert "stuck or\nrate-limited account triage" in getting_started
    assert "pause/reactivate routing" in getting_started
    assert "browser-profile work" in getting_started
    assert "dedicated browser profile" in getting_started
    assert "do not store secrets" in getting_started

    assert ".agent-lb/account-profiles.json" in account_operator
    assert "OpenAI/ChatGPT or Anthropic/Claude" in account_operator
    assert "dedicated Chrome user-data directories" in account_operator
    assert "quota reset checks" in account_operator
    assert "stuck/rate-limited account triage" in account_operator
    assert "subscription/account status checks" in account_operator
    assert "routing imbalance reports" in account_operator
    assert "routing imbalance diagnostics" in account_operator
    assert "Do not store passwords, recovery codes, access tokens, refresh tokens" in account_operator
    assert "explicit user confirmation in the current turn" in account_operator
    assert "Distinguish vendor subscription state from agent-lb routing state" in account_operator

    providers = {account["provider"] for account in account_profiles_example["accounts"]}
    assert providers == {"openai", "anthropic"}
    for account in account_profiles_example["accounts"]:
        assert account["userDataDir"].startswith(".agent-lb/chrome-profiles/")
        assert account["email"] is None
        assert account["accountId"] is None
        assert "passwords, tokens, card numbers, and recovery codes" in account["notes"]


def test_agent_skill_activation_matches_public_onboarding_and_account_prompts() -> None:
    get_started_prompts = (
        "connect my Anthropic account to agent-lb",
        "set up Claude Code and Codex CLI with agent-lb",
        "wire OpenCode to agent-lb",
        "wire OpenClaw to agent-lb",
        "wire Vercel AI SDK to agent-lb",
        "use an OpenAI-compatible SDK with agent-lb",
        "wire Anthropic Python SDK to agent-lb",
        "use an Anthropic-compatible SDK with agent-lb",
        "run the OpenAI OAuth login for this load balancer",
    )
    account_operator_prompts = (
        "pause this Claude account in agent-lb",
        "cancel the ChatGPT subscription and update the ledger",
        "open the browser profile for my Anthropic account",
        "remove a deactivated OpenAI account",
        "verify the subscription ledger for this account",
    )

    for prompt in get_started_prompts:
        result = subprocess.run(
            [str(ROOT / ".agents/hooks/skill_activation.py")],
            input=json.dumps({"session_id": "test", "prompt": prompt}),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        assert "-> get-started" in result.stdout

    for prompt in account_operator_prompts:
        result = subprocess.run(
            [str(ROOT / ".agents/hooks/skill_activation.py")],
            input=json.dumps({"session_id": "test", "prompt": prompt}),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        assert "-> agent-lb-account-operator" in result.stdout


def test_account_operator_activation_covers_public_support_prompts() -> None:
    prompts = (
        "why is this account stuck rate limited in the dashboard",
        "check the quota reset time for my Claude account",
        "same account always picked by agent-lb",
        "reactivate routing for this ChatGPT account",
        "what is the subscription status for my OpenAI account",
        "pause routing for a disabled Anthropic account",
    )

    for prompt in prompts:
        result = subprocess.run(
            [str(ROOT / ".agents/hooks/skill_activation.py")],
            input=json.dumps({"session_id": "test", "prompt": prompt}),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        assert "-> agent-lb-account-operator" in result.stdout


def test_github_intake_forms_default_to_current_release_version() -> None:
    release = _current_release()

    for path in (
        ROOT / ".github/ISSUE_TEMPLATE/bug_report.yml",
        ROOT / ".github/ISSUE_TEMPLATE/account_quota.yml",
        ROOT / ".github/DISCUSSION_TEMPLATE/q-and-a.yml",
    ):
        form = yaml.safe_load(path.read_text())
        field = _github_form_field(form, "version")

        assert field["attributes"]["placeholder"] == release.version


def test_public_intake_templates_route_security_reports_to_private_advisories() -> None:
    advisory_url = "https://github.com/aneym/agent-lb/security/advisories/new"

    for path in (
        ROOT / ".github/ISSUE_TEMPLATE/bug_report.yml",
        ROOT / ".github/ISSUE_TEMPLATE/account_quota.yml",
        ROOT / ".github/ISSUE_TEMPLATE/feature_request.yml",
        ROOT / ".github/DISCUSSION_TEMPLATE/q-and-a.yml",
    ):
        text = path.read_text()
        normalized = " ".join(text.split()).lower()

        assert advisory_url in text
        assert "security vulnerabilities" in normalized
        assert "private advisory" in normalized
        assert "do not" in normalized
        assert "public" in normalized or "post them here" in normalized


def test_public_issue_chooser_routes_questions_security_and_docs() -> None:
    config = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE/config.yml").read_text())

    assert config["blank_issues_enabled"] is False

    links = {link["url"]: link for link in config["contact_links"]}
    assert set(links) == {
        "https://github.com/aneym/agent-lb/discussions",
        "https://github.com/aneym/agent-lb/security/advisories/new",
        "https://github.com/aneym/agent-lb#readme",
    }

    security_link = links["https://github.com/aneym/agent-lb/security/advisories/new"]
    normalized_security_about = " ".join(security_link["about"].split()).lower()
    assert "security vulnerability" in security_link["name"].lower()
    assert "privately" in normalized_security_about
    assert "do not open a public issue" in normalized_security_about

    discussion_link = links["https://github.com/aneym/agent-lb/discussions"]
    normalized_discussion_about = " ".join(discussion_link["about"].split()).lower()
    assert "questions" in discussion_link["name"].lower()
    assert "how do i" in normalized_discussion_about


def test_account_quota_form_uses_timeless_reset_timestamp_placeholder() -> None:
    form = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE/account_quota.yml").read_text())
    last_quota_reset = _github_form_field(form, "last_quota_reset")

    assert last_quota_reset["attributes"]["placeholder"] == "YYYY-MM-DD HH:MM UTC"


def test_security_policy_supported_versions_track_current_release_train() -> None:
    release = _current_release()
    current_minor = _minor_series(release.base)
    previous_minor = _previous_minor_series(release.base)
    security = (ROOT / ".github/SECURITY.md").read_text()

    assert f"`agent-lb {release.version}`" in security
    assert f"| {current_minor}.x beta" in security
    assert f"| {previous_minor}.x" in security
    assert f"| < {previous_minor}" in security
    assert "Published artifacts once available" in security
    assert "ghcr.io/aneym/agent-lb" in security
    assert "oci://ghcr.io/aneym/charts/agent-lb" in security
    assert "`agent-lb` on PyPI" in security


def test_github_intake_forms_are_chatgpt_and_claude_ready() -> None:
    bug_report = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE/bug_report.yml").read_text())
    bug_client_options = _github_form_options(_github_form_field(bug_report, "client"))
    assert "Claude Code" in bug_client_options
    assert "OpenCode" in bug_client_options
    assert "OpenClaw" in bug_client_options
    assert "OpenAI-compatible SDK" in bug_client_options
    assert "Anthropic-compatible SDK" in bug_client_options
    assert "ОpenCode" not in bug_client_options

    feature_request = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE/feature_request.yml").read_text())
    feature_scope_options = _github_form_options(_github_form_field(feature_request, "scope"))
    assert "Anthropic-compatible API surface" in feature_scope_options
    assert "Client launchers / integrations" in feature_scope_options

    for path in (
        ROOT / ".github/ISSUE_TEMPLATE/bug_report.yml",
        ROOT / ".github/ISSUE_TEMPLATE/account_quota.yml",
    ):
        form = yaml.safe_load(path.read_text())
        account_plan = _github_form_field(form, "account_plan")
        options = _github_form_options(account_plan)

        assert "Provider" in account_plan["attributes"]["label"] or "provider" in account_plan["attributes"]["label"]
        assert "ChatGPT Plus" in options
        assert "Claude account" in options
        assert "OpenAI API-key only (no ChatGPT account)" in options
        assert "Anthropic API-key only (no Claude account)" in options


def test_release_critical_make_targets_use_repo_python() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "PYTHON ?= .venv/bin/python" in makefile
    assert "architecture-check:\n\t$(PYTHON) scripts/check_proxy_architecture.py" in makefile
    assert "\t$(PYTHON) scripts/verify-wheel-assets.py" in makefile
    assert "\n\tpython scripts/check_proxy_architecture.py" not in makefile
    assert "\n\tpython scripts/verify-wheel-assets.py" not in makefile


def test_active_openspec_verification_notes_do_not_defer_available_cli() -> None:
    active_verification_files = [
        path
        for path in (ROOT / "openspec/changes").rglob("*")
        if path.name in {"tasks.md", "verify-report.md"} and "archive" not in path.relative_to(ROOT).parts
    ]
    forbidden_phrases = (
        "OpenSpec CLI is unavailable",
        "CLI unavailable",
        "could not run because `openspec` is not installed",
        "deferred to an environment",
        "cannot determine an executable",
    )
    offenders: list[str] = []

    for path in active_verification_files:
        text = path.read_text()
        for phrase in forbidden_phrases:
            if phrase in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {phrase!r}")

    assert offenders == []


def test_active_openspec_validation_script_uses_strict_cli() -> None:
    script = (ROOT / "scripts/validate-active-openspec-changes.sh").read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in script
    assert 'CHANGES_DIR="openspec/changes"' in script
    assert 'find "${CHANGES_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name archive | sort' in script
    assert 'npx --yes @fission-ai/openspec@latest validate "${change}" --strict' in script
    assert 'echo "validated ${total} active changes"' in script


def test_active_release_management_openspec_pins_public_release_proof_identity_contracts() -> None:
    spec = (ROOT / "openspec/changes/require-beta-candidate-validation/specs/release-management/spec.md").read_text()
    normalized = " ".join(spec.split())

    expected_contracts = (
        "MUST verify the normalized PyPI version, public package summary, "
        "project URLs, and exact wheel/sdist filenames through PyPI JSON",
        "MUST print the current branch and local `main` SHA evidence",
        "fails closed before publication when the checkout is not local `main`, local `main` does not point at `HEAD`",
        "PyPI version/summary/project-URL/file visibility",
        "MUST print the pull request `headRefOid` as full and 12-character short SHA evidence",
        "MUST verify current-head Codex classifier output proves the same short head SHA",
        "has Codex classifier output for a different head SHA",
        "#### Scenario: publish readiness blocks non-main checkout drift",
        "The public release handoff SHALL keep the remaining-risk and completion boundary explicit",
        "MUST NOT describe a release as complete while any commit/PR, PR-head CI/Codex-review",
        "#### Scenario: handoff remains incomplete while approval-gated blockers remain",
    )

    for contract in expected_contracts:
        assert contract in normalized


def test_active_openspec_unchecked_tasks_are_pr_head_gates() -> None:
    active_task_files = sorted(
        path for path in (ROOT / "openspec/changes").glob("*/tasks.md") if "archive" not in path.parts
    )
    unchecked_tasks: list[str] = []
    missing_boundaries: list[str] = []

    for path in active_task_files:
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            if not line.startswith("- [ ] "):
                continue

            task = f"{path.relative_to(ROOT)}: {line}"
            unchecked_tasks.append(task)
            evidence_window = "\n".join(lines[index : index + 8])
            normalized_window = " ".join(evidence_window.split())
            if "Confirm GitHub CI and Codex review on the PR head" not in line:
                missing_boundaries.append(f"{task} is not a PR-head gate")
            if "Pending until" not in normalized_window or "PR exists" not in normalized_window:
                missing_boundaries.append(f"{task} does not explain the PR-head evidence boundary")
            if "gh pr list --repo aneym/agent-lb --state open" not in normalized_window:
                missing_boundaries.append(f"{task} does not record live PR evidence")
            if "gh run list --repo aneym/agent-lb --branch main --limit 10" not in normalized_window:
                missing_boundaries.append(f"{task} does not record live CI-run evidence")
            if PR_HEAD_SNAPSHOT_AT not in normalized_window:
                missing_boundaries.append(f"{task} does not use the latest PR-head snapshot timestamp")
            if "./scripts/public-release-pr-head-proof.sh <pr-number>" not in normalized_window:
                missing_boundaries.append(f"{task} does not name the PR-head proof command")

    assert unchecked_tasks == [
        "openspec/changes/create-pytest-required-check-placeholders/tasks.md: "
        "- [ ] 2.4 Confirm GitHub CI and Codex review on the PR head.",
        "openspec/changes/require-beta-candidate-validation/tasks.md: "
        "- [ ] 4.7 Confirm GitHub CI and Codex review on the PR head.",
    ]
    assert missing_boundaries == []


def test_active_openspec_changes_have_spec_deltas() -> None:
    active_change_dirs = [
        path for path in sorted((ROOT / "openspec/changes").iterdir()) if path.is_dir() and path.name != "archive"
    ]
    delta_headers = (
        "## ADDED Requirements",
        "## MODIFIED Requirements",
        "## REMOVED Requirements",
        "## RENAMED Requirements",
    )
    missing_deltas: list[str] = []

    for change_dir in active_change_dirs:
        spec_files = sorted((change_dir / "specs").glob("*/spec.md"))
        if not spec_files:
            missing_deltas.append(f"{change_dir.relative_to(ROOT)} has no spec delta")
            continue

        if not any(any(header in spec_file.read_text() for header in delta_headers) for spec_file in spec_files):
            missing_deltas.append(f"{change_dir.relative_to(ROOT)} has spec files without delta headers")

    assert missing_deltas == []


def _current_release() -> ReleaseVersion:
    return parse_version(read_pyproject_version(ROOT))


def _minor_series(version: str) -> str:
    major, minor, _patch = version.split(".", 2)
    return f"{major}.{minor}"


def _previous_minor_series(version: str) -> str:
    major, minor, _patch = version.split(".", 2)
    minor_number = int(minor)
    if minor_number <= 0:
        raise ValueError(f"release has no previous minor series: {version}")
    return f"{major}.{minor_number - 1}"


def _github_form_field(form: dict[str, object], field_id: str) -> dict[str, object]:
    for field in form["body"]:
        if field.get("id") == field_id:
            return field
    raise AssertionError(f"could not find GitHub form field {field_id!r}")


def _github_form_options(field: dict[str, object]) -> list[str]:
    return list(field["attributes"]["options"])


def _handoff_topic_payload(handoff: str) -> dict[str, list[str]]:
    marker = "--input - <<'JSON'\n"
    start = handoff.index(marker) + len(marker)
    end = handoff.index("\nJSON", start)
    return json.loads(handoff[start:end])


def _handoff_replacement_prerelease_notes(handoff: str) -> str:
    marker = "Replacement prerelease notes"
    fence_start = handoff.index("```markdown\n", handoff.index(marker))
    start = fence_start + len("```markdown\n")
    end = handoff.index("\n```", start)
    return handoff[start:end]


def _handoff_commit_pr_readiness_preflight(handoff: str) -> str:
    marker = "Commit / PR readiness preflight:"
    fence_start = handoff.index("```bash\n", handoff.index(marker))
    start = fence_start + len("```bash\n")
    end = handoff.index("\n```", start)
    return handoff[start:end]


def _handoff_pull_request_draft(handoff: str) -> str:
    marker = "Pull request draft after approval:"
    fence_start = handoff.index("```markdown\n", handoff.index(marker))
    start = fence_start + len("```markdown\n")
    end = handoff.index("\n```", start)
    return handoff[start:end]


def _handoff_post_publish_proof_commands(handoff: str) -> str:
    marker = "Expected post-publish proof:"
    fence_start = handoff.index("```bash\n", handoff.index(marker))
    start = fence_start + len("```bash\n")
    end = handoff.index("\n```", start)
    return handoff[start:end]


def _codeowners_line_for_path(lines: list[str], path: str) -> str | None:
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.split()[0] == path:
            return stripped
    return None


def _helm_oci_command_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    needles = (
        "helm install agent-lb oci://ghcr.io/aneym/charts/agent-lb",
        "helm upgrade agent-lb oci://ghcr.io/aneym/charts/agent-lb",
    )
    for needle in needles:
        start = 0
        while True:
            command_start = text.find(needle, start)
            if command_start == -1:
                break
            fence_end = text.find("\n```", command_start)
            if fence_end == -1:
                fence_end = len(text)
            blocks.append(text[command_start:fence_end])
            start = command_start + len(needle)
    return blocks


def _fenced_blocks(text: str, language: str) -> list[str]:
    blocks: list[str] = []
    fence = f"```{language}\n"
    start = 0
    while True:
        fence_start = text.find(fence, start)
        if fence_start == -1:
            return blocks
        body_start = fence_start + len(fence)
        fence_end = text.find("\n```", body_start)
        if fence_end == -1:
            return blocks
        blocks.append(text[body_start:fence_end])
        start = fence_end + 4


def _readme_client_setup_table(text: str) -> list[str]:
    table_start = text.index("| Logo")
    table_end = text.index("\n\n<details>", table_start)
    return [line for line in text[table_start:table_end].splitlines() if line.startswith("| <img")]


def _readme_client_setup_section(text: str) -> str:
    section_start = text.index("## Client Setup")
    section_end = text.index("\n\n<details>", section_start)
    return text[section_start:section_end]


def _readme_section(text: str, heading: str, next_heading: str) -> str:
    section_start = text.index(heading)
    section_end = text.index(next_heading, section_start)
    return text[section_start:section_end]


def _markdown_table_cell(row: str, index: int) -> str:
    return row.split("|")[index].strip()


def _readme_metadata_header(text: str) -> dict[str, str | list[str]]:
    if not text.startswith("<!--\n"):
        raise AssertionError("README must start with the GitHub metadata comment")

    comment_end = text.find("\n-->")
    if comment_end == -1:
        raise AssertionError("README GitHub metadata comment is not closed")

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text[len("<!--\n") : comment_end].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in {"About", "Topics", "Resources"}:
            current = stripped.lower()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(stripped)

    return {
        "about": " ".join(sections.get("about", [])),
        "topics": " ".join(sections.get("topics", [])).split(),
        "resources": sections.get("resources", []),
    }


def _jsonc_to_json(text: str) -> str:
    return _strip_trailing_commas(_strip_jsonc_line_comments(text))


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\xff\xd8"):
        raise AssertionError("not a JPEG file")

    start_of_frame_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    index = 2
    while index < len(data):
        if data[index] != 0xFF:
            raise AssertionError("malformed JPEG marker stream")
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break

        marker = data[index]
        index += 1
        if marker == 0xD9:
            break
        if marker == 0xDA:
            break
        if index + 2 > len(data):
            break

        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            raise AssertionError("malformed JPEG segment")
        if marker in start_of_frame_markers:
            if segment_length < 7:
                raise AssertionError("malformed JPEG start-of-frame segment")
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += segment_length

    raise AssertionError("could not find JPEG dimensions")


def _strip_jsonc_line_comments(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            while index < len(text) and text[index] != "\n":
                index += 1
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _strip_trailing_commas(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == ",":
            cursor = index + 1
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor < len(text) and text[cursor] in "}]":
                index += 1
                continue
        result.append(char)
        index += 1
    return "".join(result)
