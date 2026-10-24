# Preserve Muse parallel tool calls exactly once

## Why
Muse emits parallel function-call added events before their done events. The generic sequential bridge reopens closed calls and repeats tool side effects. A captured real three-call upstream stream produced five Claude calls.

## What Changes
At the Muse boundary emit each complete function call once at its done event; suppress incremental function events and duplicate done IDs.

## Impact
Only Muse Messages translation; GPT and native Muse paths unchanged.
