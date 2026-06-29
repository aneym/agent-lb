<!--
Thanks for contributing to agent-lb! 🙏
Fill in the sections below. Delete sections that don't apply.
-->

## Summary

<!-- One or two sentences: what does this PR change and why? -->

## Type of change

<!-- Check the box that matches your PR. Commit titles must follow Conventional Commits. -->

- [ ] `fix:` — bug fix (no behavior change beyond the bug)
- [ ] `feat:` — new user-facing feature or capability
- [ ] `refactor:` — internal refactor (no behavior change, no API change)
- [ ] `docs:` — documentation only
- [ ] `chore:` / `ci:` / `build:` — tooling, CI, packaging
- [ ] `test:` — test-only change
- [ ] **Breaking change** (also append `!` after the type, e.g. `feat!:` or include `BREAKING CHANGE:` footer)

Linked issue: <!-- e.g. Closes #123, Fixes #456 -->

## OpenSpec

<!--
agent-lb is OpenSpec-first. If this PR changes observable behavior, requirements,
contracts, or schema, it needs an OpenSpec change under openspec/changes/<change>/.

If this PR touches an upstream-mimicking code path (Codex CLI / ChatGPT request
shape, OpenAI-compatible SDK routes, Claude Code / Anthropic request shape,
image pipeline, response.create framing, Messages SSE framing, OAuth flows,
etc.), it must stay **provider-faithful** — i.e. preserve the exact wire format
the real upstream emits for that provider. Call out anything that intentionally
diverges, and link the spec section that records the divergence.
-->

- [ ] This PR includes / updates an OpenSpec change
- [ ] Not applicable — bug fix that matches the existing spec
- [ ] Not applicable — docs / CI / chore only
- [ ] This PR touches a provider-faithful path (OpenAI/Codex or
      Anthropic/Claude request/response shape, SSE framing, OAuth flow) and
      preserves upstream-equivalent behavior

Change directory: <!-- openspec/changes/<change>/ -->

## Changes

<!-- Bulleted list of the substantive changes. Group by area if multiple. -->

-
-

## Test plan

<!--
How did you verify this works? Paste the commands you ran and the relevant outputs.
Required: unit tests for new logic, integration tests for new endpoints.
For release, package, Helm, Docker, PyPI/GHCR, or public metadata changes:
include the candidate SHA/tag plus local and public artifact proof commands.
If publication is approval-gated and not run from this PR, name the local
artifact proof, live blocker snapshot, PR-head proof command, runtime proof
command, pre-publish readiness command, and post-approval proof command that
must pass, such as
`./scripts/public-release-local-artifact-proof.sh <approved-release-tag>`,
`./scripts/public-release-live-snapshot.sh <approved-release-tag>`,
`./scripts/public-release-pr-head-proof.sh <pr-number>`,
`./scripts/public-release-runtime-proof.sh <approved-release-tag>`,
`./scripts/public-release-publish-readiness.sh <approved-release-tag>`, and
`./scripts/public-release-postpublish-proof.sh <approved-release-tag>`.
-->

```
# uv run pytest tests/unit/test_<area>.py -q
# uv run pytest tests/integration/test_<area>.py -q
```

## Screenshots / output

<!--
Required for dashboard, UI, or public screenshot changes: include before/after
screenshots or explain why screenshots are not applicable.

For proxy/API behavior: include an example request + response, stream excerpt,
or relevant log excerpt.
-->

## Checklist

- [ ] Title is in Conventional Commits format (`<type>(<scope>)?: <subject>`).
- [ ] Linked the related issue / discussion above.
- [ ] Added or updated tests covering the change.
- [ ] Ran `uv run pre-commit run local-ci --hook-stage manual --all-files` or the relevant `make <target>` subset locally.
- [ ] If touching specs: `openspec validate --specs` passes and `/opsx:verify` is clean.
- [ ] Dashboard/UI-visible changes include screenshots or a clear not-applicable reason.
- [ ] Release/package/publication changes include candidate SHA/tag and PyPI/GHCR/Helm/release-asset proof, or explicitly say publication is approval-gated and name the local artifact proof, live blocker snapshot, PR-head proof, runtime proof, pre-publish readiness, plus post-approval proof commands.
- [ ] Public client/onboarding changes keep `AGENTS.md`, `README.md`, `GETTING-STARTED.md`, `.agents/skills/get-started/SKILL.md`, `.agents/skills/skill-rules.json`, and `tests/unit/test_public_release_docs.py` in sync, or explain why a surface is not affected.
- [ ] Public client, release-version, account-plan, or support-intake changes keep `.github/ISSUE_TEMPLATE/bug_report.yml`, `.github/ISSUE_TEMPLATE/account_quota.yml`, `.github/ISSUE_TEMPLATE/feature_request.yml`, `.github/DISCUSSION_TEMPLATE/q-and-a.yml`, and `tests/unit/test_public_release_docs.py` in sync, or explain why a surface is not affected.
- [ ] Security/support-window changes keep `.github/SECURITY.md`, `README.md`, `deploy/helm/agent-lb/README.md`, and `tests/unit/test_public_release_docs.py` in sync, or explain why a surface is not affected.
- [ ] Account admin, browser-profile, billing, subscription-ledger, pause/reactivate, removal, or verification guidance changes keep `AGENTS.md`, `README.md`, `GETTING-STARTED.md`, `.agents/skills/agent-lb-account-operator/SKILL.md`, `.agents/skills/agent-lb-account-operator/account-profiles.example.json`, `.agents/skills/skill-rules.json`, and `tests/unit/test_public_release_docs.py` in sync, or explain why a surface is not affected.
- [ ] CHANGELOG is **not** edited by hand (release-please handles it).
