## 1. Canonical Definition

- [x] 1.1 Add the versioned Opus frontend-designer definition and update the canonical routing policy.
- [x] 1.2 Extend the routing verifier to require the installed Opus designer definition.

## 2. Preservation-Safe Installation

- [x] 2.1 Extend the policy installer to preview, checkpoint, install, idempotently converge, and safely uninstall the managed designer definition.
- [x] 2.2 Add regression tests covering first install, replacement checkpoint, unrelated-agent preservation, preview, idempotence, and customized-file uninstall.

## 3. Verification and Rollout

- [x] 3.1 Run focused installer tests, routing verification, Ruff, and strict OpenSpec validation.
- [x] 3.2 Install the policy locally and verify a live Claude dispatch resolves the designer to Opus without changing planner routing.
