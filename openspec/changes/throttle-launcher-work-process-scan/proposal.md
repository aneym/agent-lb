# Throttle the launcher's work-process scan

## Why

Every supervised headless `claude-lb-launch` ran `ps -axo pid=,ppid=,etime=,args=` once a second to find live tool processes. With about 100 sessions on Studio that is about 100 full process-table scans and forks a second, adding to a load average near 300 (2026-10-15).

## What Changes

- The stall supervisor scans the process table only when no other liveness signal (agent-lb traffic, transcript writes) has been seen for `min(30 s, stall timeout / 4)`, at most once per that interval, while a tool process it found is still running, and always right before it would stop a stalled session.
- When a scan finds the tool process gone after an earlier scan saw it, that scan still counts as life, so a session is never stopped earlier than before.
- A session with steady traffic or transcript writes runs no `ps` at all.

## Impact

- Affected code: `clients/claude-lb-launch` (`run_supervised`) and `tests/unit/test_claude_lb_launch_hang.py`.
- A tool that ends without being followed by traffic is noticed up to one interval (30 s at the default 300 s stall timeout) later than before.
