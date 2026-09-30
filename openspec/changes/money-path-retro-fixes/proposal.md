# Money-path retro fixes

## Why
After-the-fact reviews found lost Cursor burn signals, malformed telemetry hiding available accounts, unsafe token coercion, routing reads leaking defaults into persisted policy, and a duplicate menubar budget row.

## What Changes
- Preserve the public burn24hPercent wire name and prove the pool-to-route round trip.
- Treat malformed run token counts as zero and isolate usage validation from account availability.
- Read pace defaults without writing them into policy and safely default malformed thresholds.
- Render Cursor budget rows only for budget pools.

## Impact
Pool schemas and CLI-seat readers, clients/route, clients/seat, and macOS MakerRows, with focused regression coverage. No migrations, credential changes, or runtime restart.
