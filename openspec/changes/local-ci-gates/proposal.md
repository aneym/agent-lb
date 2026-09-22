# Move merge checks to local CI

Alex requested local CI on Studio, following Agent Rails' exact-commit receipts,
instead of GitHub-hosted merge gates. The existing hosted workflow has no runs
in this fork. Local Makefile parity currently stops at the first error and
provides no durable exact-SHA result.

Run the complete gate in an isolated worktree with disposable PostgreSQL and
kind resources. Retain per-command logs and a same-machine, exact-SHA receipt.
Missing tools and failed legs stay red. Remove hosted merge workflows and cloud
review-label dependencies; keep release/publication workflows and manual diff
review. No test selection or reduced baseline checks are introduced.

Existing application failures are reported, not hidden or waived by the runner.
The already-approved reset-display merge is independent of the new CI receipt.
