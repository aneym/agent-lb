## Implementation

- [x] Define the manual-only consume request and rejection contract.
- [x] Gate the Claude daily-cap bypass after grant selection and active-slot acquisition; persist `manual_override` on the attempt.
- [x] Include the requested override value in API audit details.
- [x] Cover default cap, override, automatic rejection, ineligible grant, and external API semantics.

## Verification

- [x] Run focused integration tests and `ruff check`.
- [x] Validate OpenSpec specs strictly and inspect the final diff.
