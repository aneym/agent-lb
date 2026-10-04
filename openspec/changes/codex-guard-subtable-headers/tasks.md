## 1. Fix

- [x] 1.1 Skip a managed key whose parsed value matches when it is set outside the provider table's lines.
- [x] 1.2 Refuse, without writing, a different value set outside those lines.

## 2. Proof

- [x] 2.1 `test_guard_accepts_seat_header_subtable_without_adding_duplicate` fails on base and passes with the fix.
