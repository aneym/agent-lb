# Validation Notes

## Pre-change regression run

Command:

```sh
PYTHONPYCACHEPREFIX=/private/tmp/limit-watch-pycache /usr/bin/python3 tests/unit/test_limit_watch.py
```

Result against the original poller: `FAILED (failures=3, errors=2)` across eight cases. The failures covered null Anthropic windows, Fable scoped-weekly admission, and stale refresh admission. The errors were missing `reasons` and `min_remaining_percent` snapshot fields. The numeric-above-reserve, reserve-boundary, and OpenAI weekly-only cases already passed.

## Post-change regression run

The same command completed `Ran 8 tests ... OK`. Strict OpenSpec validation, `/usr/bin/python3 -m py_compile`, Ruff, and `git diff --check` also passed.
