# Operator model aliases

## Why
Operators need stable model names and a way to refuse selected models before routing.

## What Changes
- Add environment settings for alias targets and fnmatch exclusions.
- Resolve once before provider selection and quota admission; retain the alias in logs.
- Expose aliases and hide excluded models in discovery.

## Impact
Proxy ingress, model discovery, and request log storage gain this behavior.
