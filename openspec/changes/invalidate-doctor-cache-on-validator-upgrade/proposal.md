# Invalidate doctor cache on validator upgrade

## Why
The doctor cache hashes definitions, roots, catalog and constant version, but not validator implementation. An upgraded doctor can return an obsolete cached rejection indefinitely for unchanged valid definitions.

## Change
Include validator source bytes in cache identity. Preserve existing cache/state files and definition content; normal repeated runs still reuse verdicts.
