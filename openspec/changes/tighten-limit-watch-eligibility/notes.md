# Validation Notes

## Pre-change regression run

Command:

```sh
PYTHONPYCACHEPREFIX=/private/tmp/limit-watch-pycache /usr/bin/python3 tests/unit/test_limit_watch.py
```

Result against the original poller: `FAILED (failures=3, errors=2)` across eight cases. The failures covered null Anthropic windows, Fable scoped-weekly admission, and stale refresh admission. The errors were missing `reasons` and `min_remaining_percent` snapshot fields. The numeric-above-reserve, reserve-boundary, and OpenAI weekly-only cases already passed.

## Auth-refresh regression

The following `/usr/bin/python3` predicate executed the `835e16f5` version of
`limit-watch` and confirmed that an otherwise healthy OpenAI account with a
four-day-old `lastRefreshAt` was rejected as `freshness: stale`. The revised
nine-case focused suite includes this fixture and now admits it while recording
`freshness: unknown`.

## Post-change regression run

The same command completed `Ran 9 tests ... OK`. `/usr/bin/python3 -m py_compile`, scoped Ruff, and `git diff --check` also passed. Repository-wide OpenSpec validation remains blocked by pre-existing failures in `anthropic-messages-compat` and `oauth-refresh-safety`; scoped validation of this change passed.
