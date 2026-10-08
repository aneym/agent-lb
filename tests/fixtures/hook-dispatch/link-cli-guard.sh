#!/bin/bash
# Keep Stripe Link CLI secrets out of transcripts (2026-09-25).
# - `link-cli auth status` prints an access token unless output is filtered.
# - `--full-output` prints the whole envelope, token included.
# - `--include card` prints card data unless it goes to --output-file.
CMD=$(jq -r '.tool_input.command // empty')
[ -z "$CMD" ] && exit 0
echo "$CMD" | grep -q 'link-cli' || exit 0
if echo "$CMD" | grep -qE 'link-cli[^;&|]*--full-output'; then
  echo "BLOCKED: link-cli --full-output prints the access token. Use --filter-output <keys>." >&2; exit 2
fi
if echo "$CMD" | grep -qE 'link-cli[^;&|]*auth[[:space:]]+status' && ! echo "$CMD" | grep -qE 'link-cli[^;&|]*--filter-output'; then
  echo "BLOCKED: link-cli auth status prints an access token. Run it with --filter-output authenticated." >&2; exit 2
fi
if echo "$CMD" | grep -qE 'link-cli[^;&|]*--include[[:space:]=]+card' && ! echo "$CMD" | grep -qE 'link-cli[^;&|]*--output-file'; then
  echo "BLOCKED: --include card must write to --output-file, never stdout. Never print card data." >&2; exit 2
fi
exit 0
