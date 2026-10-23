## Implementation

- [x] Add `compaction` to the shared public output-item passthrough allowlist.
- [x] Verify streaming events, terminal backfill, and collected responses preserve checkpoints.
- [ ] Run the supplied acceptance check and strict OpenSpec validation.

## Validation evidence

- An in-memory stream exercise passed for added/done events, populated and backfilled terminal output, and non-streaming collection, preserving opaque content and extra fields.
- The existing response-contract suite returned 32 passed and one failed. The failing test requires compaction items to be stripped, which conflicts with this change. It remains unchanged outside the allowed file scope.
- Ruff passed with its cache disabled because the shared cache directory is read-only. `git diff --check` passed.
- Strict validation of this change passed. Repository-wide strict spec validation returned 17 passed and 20 failed on existing specifications.
- The supplied acceptance command returned 127: its `/Users/aneyman/.../accept.sh` script is absent on this box. Acceptance remains unverified.
