# Evidence and limits (10/06/2026)

Board.md:37 reports seven Devin runs refusing all candidate capacity as reserved while route pick named Devin. clients/seat:1640 goes through reserve_capacity; the local credential-bundle lease command is distinct and was not the source of that reservation error. clients/route originally checked pools/seat health only. app/modules/pools/reservations.py:106 is the actual refusal. No credentials were read.

Before: the real reservation HTTP service refused the second Devin reservation, but the real pick still named devin-seat; tests/integration/test_route_pick_leases.py failed at its fallback assertion. Receipt /tmp/seat-p4-pick-before.txt.
After: pick reads one reservation snapshot, applies reserve's capacity key/multiplier, excludes full candidates and reviewers, and logs route_pick_lease_skip. Unknown snapshots remain advisory and emit one fixed stderr diagnostic, not a ledger row; reserve still performs atomic launch admission. Fixture-mode picks read only fixture snapshots (a missing file is unknown); reservation commands still use the real network. There is necessarily a race between advisory pick and reserve; this is not a lease guarantee. Expired holds do not count. --prefer cannot bypass full capacity.

Test authoring gate: real CLI + fresh real HTTP server + real reservation store, with fixture provider status and third-party model lists. No mocks of our code or new production seam. Scenario: Devin healthy, reserve refused, pick falls back to Sol, ledger records why, release restores Devin. Sibling reservation scenario retains all original admission/expiry/heartbeat checks. Its pick-only advisory-equality oracle conflicted with this explicit task, so only that oracle and the descriptive header changed; admission was not weakened. The obsolete without_clock helper was removed.

Final integration command and receipt: /tmp/seat-p4-final-integration.txt, 22 passed in 20.56s (new home checks, installer lifecycle, lease regression, reservation scenarios, auditor-via scenarios). Checks use env -u RAILS_CHECK_HOST with isolated HOME/data under /tmp.

Cross-vendor review was not run: task forbids agent messages. No deployment or runtime restart.

## Fix round one (10/06/2026)

The six reported exact-ledger failures reproduce on HEAD; receipt:
/tmp/seat-launch-p4-delivery/before.txt. Unknown availability no longer mutates
the dispatch ledger. Fixture picks no longer bypass ROUTE_FIXTURE_DIR.
The existing real-reservation integration scenarios now serve pool/model fixtures
at the HTTP edge and forward reservation reads to the actual isolated service;
all admission, expiry, heartbeat, pick-fallback and ledger assertions remain intact.
Only the shared CLI helper changes in the protected reservation scenario file.

Test authoring gate for fixture isolation: real CLI + full live Devin reservation,
with either a missing snapshot or an empty fixture snapshot. The observable
contract is selecting from fixture leases, producing no spurious ledger rows,
and emitting one diagnostic only for unknown availability. Existing unreachable
service coverage cannot catch reading a reachable live service during fixture
mode. No new production flag or mocked collaborator is required. Both cases fail
against the original route script at the seat assertion; receipt:
/tmp/seat-launch-p4-delivery/fixture-before.txt. The final requested check receipt
is /tmp/seat-launch-p4-delivery/pytest.txt; Ruff receipt is
/tmp/seat-launch-p4-delivery/ruff.txt. Installer edits are line wrapping only.
