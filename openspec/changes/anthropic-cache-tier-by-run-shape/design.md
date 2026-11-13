## Decision
The tier follows the client entrypoint in the inbound `User-Agent` (`claude-cli/<v> (external, <entrypoint>...`). `sdk-cli` is a print-mode run that ends when its task ends, so its cache is never read past five minutes and the five-minute write price (1.25x input) beats the one-hour price (2x). Every other Claude Code entrypoint (`sdk-ts`, `t3-code`, `cli`) is a thread that speaks again within the hour; the one-hour tier turns a full rewrite after an idle gap into a read (0.1x).

The rule is stateless: it needs no per-session memory and survives restarts. Each flag can be turned off alone through settings.

## Evidence
Ledger rows and the audit: `~/.agent-rails/lanes/r1-quota-climb/audit.md`.
