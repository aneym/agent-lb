# Muse Wonder FULL in Claude Code

## Why
The user wants Muse Wonder FULL to drive existing Claude Code lanes. Muse exposes Responses, while Claude Code speaks Anthropic Messages.

## What Changes
Add a dedicated Muse Messages adapter using the existing Responses bridge, and a launcher pinning every model default to muse-wonder-full-1.0. The Claude-facing model is an alias resolved through muse-latest to the provider's served model, currently muse-wonder-1.0. Expose the alias through Anthropic-format model discovery. Reuse the host Muse credential; never route to Anthropic. Prove discovery returns JSON and the real launcher returns OK.

## Impact
A new explicitly selected route and launcher; native Muse and GPT routes remain unchanged.
