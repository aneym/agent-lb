## Context

The runtime version endpoint is dashboard-auth protected and returns the
running version, latest known GitHub release version, and a release URL for
operators. The frontend OpenSpec already expects the update icon to link to
`https://github.com/aneym/agent-lb/releases/latest`, but the backend still uses
the old upstream repository for both API lookup and response defaults.

## Goals / Non-Goals

**Goals:**

- Make the backend lookup and returned release URL match the public
  `aneym/agent-lb` release surface.
- Keep the existing cache, failure behavior, version comparison, and response
  schema shape unchanged.

**Non-Goals:**

- No release publishing, GitHub metadata mutation, or package upload.
- No new configuration surface for choosing arbitrary release repositories.

## Decisions

- Keep the release repository hard-coded because the public package and
  dashboard footer are release-channel metadata, not operator-specific runtime
  configuration.
- Update tests to assert the GitHub API URL as well as the returned
  `releaseUrl`, so future forks or upstream references do not drift silently.

## Risks / Trade-offs

- [Risk] If the public repository has no stable latest release, GitHub's
  `/releases/latest` endpoint can fail. The existing runtime service already
  degrades failed lookups to `updateAvailable: false`, so no new failure mode is
  introduced.
