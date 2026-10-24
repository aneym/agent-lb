# Muse Wonder FULL in Claude Code

## Why
The user wants Muse Wonder FULL to drive existing Claude Code lanes. Muse exposes Responses, while Claude Code speaks Anthropic Messages.

## What Changes
Add a dedicated Muse Messages adapter using the existing Responses bridge, and a launcher pinning every model default to muse-wonder-full-1.0. Reuse the host Muse credential; never route to Anthropic or a smaller variant.

## Impact
A new explicitly selected route and launcher; native Muse and GPT routes remain unchanged.
