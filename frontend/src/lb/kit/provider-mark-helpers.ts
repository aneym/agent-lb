export type ProviderId =
  | "anthropic"
  | "claude"
  | "openai"
  | "codex"
  | "cursor"
  | "devin"
  | "muse"
  | "glm"
  | "kimi"
  | "openrouter"
  | "gemini"
  | "ollama"
  | "grok"
  | "unknown";
const KNOWN: readonly ProviderId[] = [
  "anthropic",
  "claude",
  "openai",
  "codex",
  "cursor",
  "devin",
  "muse",
  "glm",
  "kimi",
  "openrouter",
  "gemini",
  "ollama",
  "grok",
];
export function modelMark(modelId: string): ProviderId | null {
  const id = modelId.toLowerCase();
  if (/(claude|opus|sonnet|haiku)/.test(id)) return "claude";
  if (/(gpt|sol|luna|terra|codex)/.test(id)) return "openai";
  if (id.includes("grok")) return "grok";
  if (id.includes("glm")) return "glm";
  if (id.includes("kimi")) return "kimi";
  if (id.includes("muse")) return "muse";
  if (/(swe-|devin)/.test(id)) return "devin";
  if (id.includes("composer")) return "cursor";
  return null;
}
export function providerMark(id?: string | null): ProviderId {
  const key = (id ?? "").toLowerCase();
  return (KNOWN as readonly string[]).includes(key) ? (key as ProviderId) : "unknown";
}
export function poolProvider(poolId: string): ProviderId {
  const id = poolId.toLowerCase();
  if (id.startsWith("anthropic")) return "claude";
  if (id.startsWith("openai")) return "codex";
  if (id.startsWith("cursor")) return "cursor";
  if (id.startsWith("devin")) return "devin";
  if (id.startsWith("muse")) return "muse";
  return providerMark(id);
}
const VENDOR_NAMES: Record<string, string> = {
  anthropic: "Claude",
  claude: "Claude",
  openai: "Codex",
  codex: "Codex",
  cursor: "Cursor",
  devin: "Devin",
  muse: "Muse",
  glm: "GLM",
  kimi: "Kimi",
  openrouter: "OpenRouter",
  gemini: "Gemini",
  ollama: "Ollama",
  grok: "Grok",
};
export function vendorName(id?: string | null): string {
  if (!id) return "Other";
  return VENDOR_NAMES[id.toLowerCase()] ?? id.charAt(0).toUpperCase() + id.slice(1);
}
