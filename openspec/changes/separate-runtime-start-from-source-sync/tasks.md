## Preparation
- [x] Prepare the exact internal-runtime launcher and documentation.
- [x] Cold-test startup without the external source, preserving exec behavior.
- [ ] Independently review the launcher and source-documentation hashes.
## Activation
- [ ] Install the launcher with compare-before-write and a preserved rollback copy.
- [ ] At the next necessary restart, verify process, hashes, health and journal.
