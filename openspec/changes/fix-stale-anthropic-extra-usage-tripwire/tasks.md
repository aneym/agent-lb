## 1. Implementation
- [x] 1.1 Retain extra-usage tripwires until a strictly newer primary snapshot confirms recovery, for normal routing and transient recovery.
- [x] 1.2 Preserve fresh credit-billing tripwires when primary usage is missing, older, or recorded at the same time.

## 2. Validation
- [ ] 2.1 Run the supplied acceptance check successfully (exit 127: supplied Mac script absent on this box).
- [x] 2.2 Pass focused Anthropic proxy coverage: 67 integration tests and 16 API tests passed, including credit-billing response rotation. Forensics output was redirected to a disposable directory through the existing registration API because the default home directory is read-only. No tests were modified.
- [x] 2.3 Ruff and git diff checks passed.
- [x] 2.4 Strict validation of this OpenSpec change passed.

No test files are admitted by this brief; existing HTTP regression coverage exercises the fresh-tripwire failure. The acceptance runner supplies additional stale-tripwire regression coverage externally. Acceptance validation remains incomplete; this change is unarchived.
