# Tasks

- [x] Record Cursor as a provider-native candidate observed from `discovered` to `blocked`, with consent action `agent login`.
- [x] Leave catalog, entitlement, and usage unobserved: no inferred models, `meter_mode=provider_managed`, headroom `NOT_EXPOSED`, admission `NATIVE_MANAGED_BLOCKED`.
- [x] Record Grok as disabled, with API spend off and no enabled mark.
- [x] Keep both names rejected by `get_provider`.
- [x] Validate: `pytest tests/unit/test_native_backup.py -q` and `ruff check` on the changed Python files.
