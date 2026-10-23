# Factory owner usage

Persist project and job identifiers from factory callers. Derive the serving pool owner and host from the agent-lb instance configuration. Expose all four fields through request logs so pooled fleet usage can be grouped by owner without sharing accounts.

The nullable request-log columns are introduced by `20261023_000000_factory_usage_attribution.py`; historical rows remain unattributed.
