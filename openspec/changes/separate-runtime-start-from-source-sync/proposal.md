# Separate service startup from source deployment

Launchd cannot read the external source checkout under its current macOS access
context. Automatic service recovery must start the already approved internal
runtime without importing source. Keep the documented interactive sync helper as
the explicit deployment step, with log and candidate-hash verification.

Scope: one installed launcher and deployment documentation. No permissions,
credentials, launchd configuration, source promotion service or runtime update.
