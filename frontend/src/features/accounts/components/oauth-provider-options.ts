import type { AccountProvider } from "@/features/accounts/schemas";

export const OAUTH_PROVIDER_OPTIONS: Array<{
  provider: AccountProvider;
  title: string;
  detail: string;
  deviceEnabled: boolean;
}> = [
  {
    provider: "openai",
    title: "OpenAI",
    detail: "ChatGPT / Codex OAuth",
    deviceEnabled: true,
  },
  {
    provider: "anthropic",
    title: "Anthropic",
    detail: "Claude Code OAuth",
    deviceEnabled: false,
  },
  {
    provider: "gemini",
    title: "Google",
    detail: "Gemini · Antigravity OAuth",
    deviceEnabled: false,
  },
];
