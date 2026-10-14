# Optimize SQLite additional quota reads

Repeated latest-quota reads rank over one million history rows and consume multiple CPU cores. Replace SQLite history-wide ranking with indexed per-account/per-alias latest seeks while preserving all alias and tie behavior.
