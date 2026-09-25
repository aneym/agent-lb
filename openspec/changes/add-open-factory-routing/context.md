# Context
The decision shape follows keel's "host owns the move" (`crates/engine/src/jev_routing.rs`):
at most 16 candidates, `escalate` reserved as abstain, a pick below confidence 0.35 or fit
0.8 is an abstain, and any failed re-check keeps the host's default. The decider policy
(capability blurbs, thresholds, timeouts) lives in `clients/open-factory/open_factory/decider.json`
so a change to it is a versioned policy change that the eval replays before adoption.

Example: `open-factory route --class explore "Find every caller of pools_from_accounts"`
returned `Explore` on `sonnet` (Jev confidence 0.91, fit 0.81, 476 ms, $0.00006), re-checked
against a fresh menu and accepted. The same call for `implement` with Opus behind weekly pace
got an abstain (`low_fit`) and fell back to `route pick implement` (codex-sol).
