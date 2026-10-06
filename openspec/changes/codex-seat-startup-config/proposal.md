# Isolated Codex seat startup configuration

## Why
Factory board item 15: no-network homes, inherited websocket settings and a sessions archive symlink caused blocked checks and slow first turns. Chromium Mach bootstrap crashes need an outside-sandbox capture, not an unrestricted default seat.

## What Changes
- Install a canonical isolated home writer with workspace-write plus network, HTTP transport, no implicit apps/MCP/plugins, and a job-local real sessions directory.
- Ship the external cx-bg compatibility patch that stops re-linking the archive after home generation.
- Document reported Chromium SIGTRAP and transport failures separately from settings proof.

## Impact
config/coding-agents/install-policy.py and codex-seat-home.py; cx-bg deployment patch. No live writes or restart in this task.
