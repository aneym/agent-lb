# Tasks

- [x] Require resolved assigned accounts for new member keys and reject clearing existing member key assignments before any write.
- [x] Accept assigned accounts in the Team API, return a dedicated error for missing scope, and audit only the account count.
- [x] Map the dedicated error in the API key update endpoint without changing other errors.
- [x] Update existing member-key fixtures and add integration coverage for scope creation, rejection, and non-member clearing.
- [x] Run scoped tests and strict OpenSpec validation; changed-file Ruff checks pass. Full-tree Ruff checks expose pre-existing lint and formatting issues outside this change.
