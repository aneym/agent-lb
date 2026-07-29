# remove-codex-5h-window

## Why

OpenAI removed the Codex 5-hour ("primary") rate-limit window in late July
2026; codex accounts now report only the weekly window (upstream sends the
weekly limit in the primary slot, which the existing weekly-only
normalization already remaps to secondary). The application still presented
5-hour UI and API artifacts for codex: `/api/usage/summary?provider=openai`
returned a degenerate primary snapshot (0% remaining, 0 capacity,
windowMinutes 300), openai accounts with no usage defaulted to a full 100%
primary gauge, and the macOS menubar and dashboard rendered a dead "5-HOUR
LIMIT" card/donut for the Codex scope.

## What Changes

- `/api/usage/summary` reports `primaryWindow: null` when every account in
  scope is an openai (codex) account, instead of an empty 0% snapshot.
  Mixed and Anthropic scopes keep the 5-hour aggregate (Claude still has a
  5h window).
- Account summaries never default openai accounts to a 100% primary gauge;
  with no primary usage data the primary fields stay null (anthropic already
  behaved this way; the 100% default now applies only to other providers).
- macOS menubar: the Codex provider scope renders only the WEEKLY LIMIT pool
  card; codex account rows drop the 5H cell and drive the leading ring from
  the weekly window. Claude and All scopes are unchanged.
- Dashboard: the 5-Hour Credits donut is hidden when the provider filter is
  Codex. Per-account cards already collapse to weekly-only via the existing
  weekly-only path.

## Impact

- Menubar/dashboard consumers of `/api/usage/summary` must tolerate a null
  `primaryWindow` (the menubar model was already optional; the dashboard
  reads its own overview endpoint, which is unchanged).
- No DB or migration changes: ingestion still stores whatever windows
  upstream reports; this change only stops synthesizing/displaying a codex
  5h window that no longer exists.
- Routing/balancer behavior is unchanged (primary fields were already
  optional; codex selection now keys off the weekly window only, which live
  data already reflected).
