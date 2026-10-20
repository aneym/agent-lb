## ADDED Requirements

### Requirement: DeepSeek OpenRouter routing
The proxy SHALL route deepseek-flash to deepseek/deepseek-v4.1-flash-1017 using OpenRouter and the stored account Bearer token. The request SHALL pin provider order to inference-net/fp8, deepinfra/fp8/priority, morph/fp8, cloudflare with allow_fallbacks false. It SHALL accept the exact identifier and its :nitro and :floor variants. deepseek-flash-floor SHALL map to the :floor variant without provider pinning.

#### Scenario: OpenAI streamed tools and images
- **WHEN** a client sends images and tools through /v1/chat/completions with deepseek-flash
- **THEN** the proxy forwards the body to https://openrouter.ai/api/v1/chat/completions, preserves upstream SSE bytes, and attributes tokens and usage.cost to the OpenRouter account

#### Scenario: Anthropic messages
- **WHEN** a client sends deepseek-flash to /v1/messages
- **THEN** the existing Anthropic-compatible route uses the same model mapping and provider pin

#### Scenario: Discovery and existing routes
- **WHEN** a client lists /v1/models
- **THEN** deepseek-flash is listed and other models retain their existing routes
