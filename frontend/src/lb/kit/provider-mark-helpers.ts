export type ProviderId =
  | "anthropic"
  | "claude"
  | "openai"
  | "codex"
  | "cursor"
  | "devin"
  | "glm"
  | "kimi"
  | "openrouter"
  | "gemini"
  | "ollama"
  | "grok";
export function modelMark(modelId: string): ProviderId | null {
  const id = modelId.toLowerCase();
  if (/(claude|opus|sonnet|haiku)/.test(id)) return "claude";
  if (/(gpt|sol|luna|terra|codex)/.test(id)) return "openai";
  if (id.includes("grok")) return "grok";
  if (id.includes("glm")) return "glm";
  if (id.includes("kimi")) return "kimi";
  return null;
}
export function providerMark(id?: string): ProviderId {
  return (
    [
      "anthropic",
      "claude",
      "openai",
      "codex",
      "cursor",
      "devin",
      "glm",
      "kimi",
      "openrouter",
      "gemini",
      "ollama",
      "grok",
    ].includes(id ?? "")
      ? id
      : "anthropic"
  ) as ProviderId;
}
