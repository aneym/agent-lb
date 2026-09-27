## Why

A team member key minted without account assignments is unscoped and can route to every account, including accounts not explicitly shared with that member. Member keys must be confined to accounts the owner chooses.

## What Changes

- Require at least one valid assigned account when creating a team member key, and refuse edits that would remove its last assignment.
- Accept `assignedAccountIds` on the team member key creation request and return a dedicated validation error when the scope is missing.
- Include only the assigned account count, not account identifiers, in the key creation audit event.
- Preserve existing non-member key behavior: these keys can remain unscoped or clear their assignments.

## Impact

- Team member key creation without an explicit account scope now returns HTTP 400. The Team page's issue-key form will show that error until it gains an account picker.
- Existing unscoped member keys remain editable when their account assignments are not changed. No existing database records or routing behavior are migrated.
