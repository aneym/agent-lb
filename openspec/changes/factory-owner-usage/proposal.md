# Factory owner usage

Persist project and job identifiers from factory callers. Derive the serving pool owner and host from the agent-lb instance configuration. Expose all four fields through request logs so pooled fleet usage can be grouped by owner without sharing accounts.

The nullable request-log columns are introduced by `20261023_000000_factory_usage_attribution.py`; historical rows remain unattributed. A separate factory usage token authorizes an endpoint that returns only attribution and token counts. Claude and Codex launchers attach project/job context. Project and job are client-supplied, owner-attested telemetry, not billing authority. Workload identity validation is outstanding.

Usage consumers should follow `nextBeforeId` as `beforeId` with no offset. Rows use descending database IDs so a newly inserted, backdated request cannot move into a later page. Legacy offset consumers must deduplicate the returned row IDs. Both project and job tags are required; a partial pair stays unattributed. Codex header overrides follow the selected provider in the base config, named profile file or legacy profile table, and command-line provider override.
