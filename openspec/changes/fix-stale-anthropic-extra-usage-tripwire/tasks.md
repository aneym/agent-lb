## 1. Implementation
- [x] 1.1 Restrict effective extra-usage markers to current primary exhaustion for normal routing and transient recovery.

## 2. Validation
- [ ] 2.1 Run the supplied acceptance check successfully (attempt returned 127: supplied script absent on this box).
- [ ] 2.2 Pass focused Anthropic proxy coverage (66 passed, one legacy expectation failed: tripwire blocks without a primary-exhaustion snapshot). Ruff, service import, and diff checks passed.
- [x] 2.3 Validate the OpenSpec change strictly.

Repository-wide strict spec validation reports existing long-requirement warnings: 17 passed, 20 failed. Acceptance and integration validation remain incomplete; this change is unarchived.
