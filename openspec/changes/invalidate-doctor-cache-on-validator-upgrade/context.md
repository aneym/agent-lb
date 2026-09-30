# Context

## Scope and decision
Installed validator was updated to accept family aliases, but default doctor retained three cached errors while fresh state reported zero. Cache version is unchanged at 1. Hash the running validator source in the cache identity rather than deleting state or relying on manually incremented versions. Definition/catalog/root hashes retain their existing semantics.

## Failure example
An old validator rejects sol-latest-medium and stores the verdict. Replacing that executable at the same path with the current accepting validator must recompute the verdict even though definitions and cache state are unchanged.

## Test authoring gate
One CLI subprocess lifecycle regression copies the validator, reproduces its historical pre-family-alias predicate, confirms a real cached rejection, replaces the executable with current source at the same path, and invokes again with unchanged definitions/state. Baseline reuses the obsolete rejection. Existing cache tests vary definition/root/catalog but never validator content. No production test seam is introduced.
