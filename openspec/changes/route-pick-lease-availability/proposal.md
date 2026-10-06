# Route pick observes reservation availability

## Why
Factory board item 5: Devin was selected while seat run refused all candidate capacity as reserved, seven times. Pool health is not a lease.

## What Changes
Pick consults the shared live reservation snapshot using the same capacity keys and policy as reserve. Full candidates and their reviewers are skipped with ledger evidence. The snapshot is advisory: reserve remains the atomic authority; unavailable snapshots are reported, not a quota-based denial.

## Impact
clients/route, route lease integration checks. No deployment in this task.
