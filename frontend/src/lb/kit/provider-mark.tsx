import type { CSSProperties } from "react";

import { type ProviderId } from "./provider-mark-helpers";
export type { ProviderId } from "./provider-mark-helpers";

const color: Partial<Record<ProviderId, string>> = {
  claude: "claude-color",
  devin: "devin-color",
  glm: "glm-color",
  kimi: "kimi-color",
  gemini: "gemini-color",
};
export function ProviderMark({ id, size = 20 }: { id: ProviderId; size?: 16 | 20 | 28 }) {
  if (id === "unknown")
    return (
      <span
        className="logo"
        style={{
          width: size,
          height: size,
          borderRadius: "50%",
          boxShadow: "inset 0 0 0 1.5px var(--line)",
        }}
        aria-hidden="true"
      />
    );
  const maker = id === "codex" ? "openai" : id;
  const name = color[maker] ?? maker;
  const url = `/brand/providers/${name}.svg`;
  return color[maker] ? (
    <img
      className="logo"
      src={url}
      width={size}
      height={size}
      style={{ width: size, height: size }}
      alt=""
      aria-hidden="true"
    />
  ) : (
    <span
      className="logo m"
      style={{ width: size, height: size, "--m": `url("${url}")` } as CSSProperties}
      aria-hidden="true"
    />
  );
}
