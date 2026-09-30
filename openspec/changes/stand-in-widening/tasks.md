## 1. Implementation
- [x] 1.1 Exclude review lanes at launcher and server boundaries.
- [x] 1.2 Print resolved banner and dry-run tags on stderr.
- [x] 1.3 Strip internal headers upstream and implement stable half-stage selection.
- [x] 1.4 Add isolated forced-swap and recovery script.

## 2. Validation
- [ ] 2.1 Full chained check blocked by pre-existing E501 in scripts/claude_cache_eval.py:97; compile, 197 selected unit tests and separate strict OpenSpec validation pass.
- [x] 2.2 Run the forced-swap script against the isolated harness.
