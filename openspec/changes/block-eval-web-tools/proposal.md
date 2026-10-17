# Block eval web tools

## Why
Eval traffic must never use provider web tools.

## What Changes
Filter final provider bodies and persist removed tool types.

## Impact
Anthropic and OpenAI outbound bodies, request logs, and one nullable database column.
