# Stand-in widening

## Why
Widen the canary safely without changing the vendor of review, verification or audit lanes.

## What Changes
- Exclude review lanes from automatic launcher tags and server-side swaps, including ccgpt launchers.
- Show resolved stand-in intent and lane on stderr banners; keep dry-run tags off stdout.
- Drop internal intent/lane headers before forwarding upstream.
- Support stable half-stage selection while retaining canaries.
- Add a forced-429 swap-and-return check using the isolated test harness, never a live LB.

## Impact
Affected specifications: anthropic-messages-compat.
Affected code: session launcher, proxy eligibility and header filtering, test tooling.
No rollout activation, live restart, credentials, or system-block changes.
