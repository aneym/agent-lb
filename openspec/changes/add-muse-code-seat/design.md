## Context

See proposal.md for scope and docs/muse-code.md for dated research. The current change implements the offline resolver and provider guard only. The Linux worker has no Muse binary, shell DNS fails, and the authorized Studio open-factory worktree is absent. The local open-factory origin/main snapshot is readable.

## Goals / Non-Goals

Resolve a recorded Muse model and prevent accidental personal sign-in use. Record the remaining integration contracts so a host with the pinned binary can finish them. Default ladder promotion and deployment are outside this brief.

## Decisions

Use a distinct `muse` alias vendor with a `pinned` model and family check. Catalog discovery is deferred because no authenticated catalog is available. Resolution checks the existing blocked and retired model rules before returning the pin.

Extend the existing provider executable map with `muse` and `META_API_KEY`. This follows the provider guard precedent without creating another credential parser. The guard validates credential presence only; it cannot establish subscription eligibility.

Keep the account and receipt adapter pending until `muse exec --json`, auth status and quota output are captured from the pinned native binary. The SDK session protocol is documented, but its notification shape does not establish the headless CLI's JSONL shape. Guessing that shape would corrupt usage or label failed work successful.

Use the open-factory box tools installer for durable installation and seat-run for returned diffs. A copy under factory-runtime would disappear at a runtime replacement. No substitute checkout is writable under this brief.

## Risks / Trade-offs

The model pin does not prove account eligibility or available quota. Completion requires live readback and a real scratch task. Subscription onboarding keys and independently created API keys have different billing; provisioning must preserve that distinction. Contributor mode must be off for repository content under the privacy floor.

## Migration Plan

Complete the unchecked tasks on a host with the authorized open-factory worktree and download access. Keep deployment and push disabled under this brief. After validation, commit each authorized worktree with the requested trailers. Rollback of this partial change removes the Muse alias and provider map entry.
