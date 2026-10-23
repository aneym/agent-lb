# Factory owner usage

Persist project and job identifiers from factory callers. Derive the serving pool owner and host from the agent-lb instance configuration. Expose all four fields through request logs so pooled fleet usage can be grouped by owner without sharing accounts.

The nullable request-log columns are introduced by `20261023_000000_factory_usage_attribution.py`; historical rows remain unattributed. A separate factory usage token authorizes an endpoint that returns only attribution and token counts. Claude and Codex launchers attach project/job context. These identifiers are owner-attested; workload identity validation is outstanding.
