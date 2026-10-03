# Request log room and unified utilization

## Why
Rails host quota evidence needs upstream Anthropic five-hour and seven-day utilization on each request receipt, with room attribution across providers.

## What Changes
- Add nullable room and unified utilization columns with an additive, re-runnable migration.
- Normalize the inbound x-agent-lb-room header into request-local attribution without rejecting requests.
- Read Anthropic upstream utilization headers for the response attempt represented by a log row, retaining finite nonnegative raw fractions.
- Expose the fields in request-log entries without changing payloads, forwarded headers, downstream responses, selection, or quota tripwires.

## Impact
Database request_logs, request-log API schemas, client-session middleware, and Anthropic request logging. Other providers retain null utilization values.
