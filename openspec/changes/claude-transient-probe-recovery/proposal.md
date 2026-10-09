# Claude transient probe recovery

## Why
The October 9 mirror-token failure burst left a healthy Claude account in process-local transient error backoff. A successful pinned account probe did not reconcile the shared selector; routing recovered after a controlled restart, although the backoff deadline expired concurrently. Selection diagnostics also omitted transient backoff and substituted unrelated quota reset deadlines.

## What Changes
A successful account probe clears only transient error state on the application's shared Anthropic-compatible selector. Failed probes do not clear it. Persisted quota, cooldown, spend, and auth gates remain unchanged. Selection failures expose the transient backoff deadline and count.
