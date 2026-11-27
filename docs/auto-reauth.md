# Automatic Claude sign-in

The `agent-lb-reauth-auto.timer` user timer on `ax42` checks enrolled Anthropic accounts every ten minutes. It uses only the account's dedicated, registered Chrome directory under `~/.agent-lb-primary/browser-profiles`, with a private Linux display. It never opens a desk machine's browser.

If sign-in needs a person, the Needs You card links to `/reauth/<accountId>` on the owner's HTTPS dashboard. Open the link to start a fresh flow. Use the isolated Chrome pane, or copy the bound link into a private window and paste the full `code#state` back into the dashboard. OpenAI uses device codes and does not start Chrome. OAuth flows are bound to the account and expire within 60 minutes.

The owner checks the new credentials before it withdraws the corresponding Needs You card. The `agent-lb-reauth-needs-you.path` unit notices successful checks, and its timer retries interruptions. The separate weekly `agent-lb-reauth-keepalive.timer` checks enrolled browser sessions without clicking or entering credentials.

Run `scripts/install-reauth-auto.sh` on `ax42` to install the user units. State and receipts live under `~/.agent-lb-primary/state`. Review a tick with `agent-lb reauth-auto run --dry-run --no-keepalive`; a dry run does not click, change tokens, or file a card.
