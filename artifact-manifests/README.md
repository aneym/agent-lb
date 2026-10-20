# Historical screenshots

The screenshots manifest preserves seven historical dashboard JPEGs. README images use pinned origin URLs. Restore the SHA-verified originals with `python3 -m scripts.artifact_store screenshots --dest /tmp/lb-screenshots`.

The plain object store is `/Volumes/StudioExt/artifacts/stack-lean` or `STACK_ARTIFACT_STORE`. When absent the reader recovers the pinned origin tag with normal git access and verifies every file SHA256 and mode.
