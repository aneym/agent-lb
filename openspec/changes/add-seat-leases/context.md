# Seat lease decisions

A long `seat run` used to save an old state snapshot, dropping leases and releases created while it ran. Writers now lock and merge their own fields into fresh state. They reread the registry inside the lock so a concurrent addition survives the run's save.

A keychain login cannot travel off its machine, so only API-key Cursor and data-dir Devin accounts can be leased. Active leases count as use; the newest lease creation time breaks ties, spreading parallel work across accounts.

An auth outcome from a failed run is confirmed by an independent auth-only probe (S-11). Only an explicit auth rejection confirms failure; a limit or service failure is inconclusive. A log without an explicit outcome represents a failed seat, rather than a successful transcript that might mention a limit. Stored diagnostics use fixed vocabulary because vendor log and probe lines may contain credentials; neither raw text is stored or printed.

A lease expires after its TTL and then stops counting as use. Released leases and expired unreleased leases are pruned after seven days. Per the owner's 2026-09-27 steer, this piece ships basic protections (0600 files, 0700 directories, no secret in any output) and leaves further hardening for later.
