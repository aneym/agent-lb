# Context

The built-in image tool returned HTTP405. Exact live access logs showed POST /backend-api/codex/images/generations and edits hitting unregistered routes. The live service advertises /v1/images/generations and edits and already translates those to the provider image-generation tool. An exact alias in existing path middleware repairs route selection without implementing a second image pipeline or copying credentials.

Only app/core/middleware/path_rewrite.py needs runtime deployment. Its pre-edit SHA256 matches the internal runtime: d8122c2040b84e4ad2bd1d5241f31fd8a7fbdbf06289ffe3c8e8d88ba80fa6. Preserve unrelated dirty work and do not run broad source sync. Checkpoint the exact runtime file, verify selective copy, then restart only in a coordinated safe window; restore the checkpoint if readiness fails. Source test result:34 targeted tests passed, Ruff passed.
