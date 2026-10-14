# SQLite latest-quota query cost

Additional quota routing and background cache warming request the latest row for each account. The previous SQLite path ranked the complete matching history for each quota and window. History growth therefore increased routing and background CPU costs even though only one result per account was needed.

The SQLite path now seeks the newest row for each canonical or historical alias using equality prefixes on account, window and quota key, lower-case limit name, or lower-case metered feature. It then compares the bounded candidate set by descending recorded_at, used_percent and id. This retains the previous OR-alias semantics, including legacy rows, and keeps supplied account IDs and since filters exact. Large supplied account pools are queried in sequential batches below SQLite's compound SELECT limit. Unfiltered reads retain historical account IDs, including orphan rows, rather than silently narrowing to the accounts table.

A benchmark against a database snapshot containing approximately 1.1 million additional-quota rows returned identical result IDs for four tested quota keys. Representative lookups fell from 4.21 seconds to 18.6 milliseconds, and from 0.72 seconds to approximately 3 milliseconds. These are observed snapshot results, not fixed performance guarantees. EXPLAIN QUERY PLAN confirmed covering-index equality seeks; the remaining sort compares only alias candidates for one account.

Regression coverage exercises canonical and legacy aliases, mixed-case limit/feature aliases, timestamp/usage/id tie precedence, account filtering, missing accounts, empty pools, window/since filtering and pools exceeding 500 accounts. The migration is additive and SQLite-only, with downgrade removing only the new indexes.
