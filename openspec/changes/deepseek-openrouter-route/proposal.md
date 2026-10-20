# DeepSeek routing through OpenRouter

## Why
OpenAI chat requests currently send DeepSeek models to subscription Codex accounts. Browser agents need the imported OpenRouter account instead.

## What Changes
- Route the DeepSeek Flash aliases and exact model identifiers through OpenRouter.
- Preserve OpenAI chat bodies and response bytes, and record reported usage cost.
- Publish the aliases in the models endpoint and support Anthropic messages.

## Impact
The change affects proxy routing and OpenRouter usage logs. Other models retain their routes.
