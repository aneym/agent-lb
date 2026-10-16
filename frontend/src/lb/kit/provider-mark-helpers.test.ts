import { describe, expect, it } from "vitest";

import { modelMark, poolProvider, providerMark, vendorName } from "./provider-mark-helpers";

describe("provider marks", () => {
  it.each([
    ["muse", "muse"],
    ["cursor", "cursor"],
    ["devin", "devin"],
    ["anthropic", "anthropic"],
    ["MUSE", "muse"],
    ["mystery-vendor", "unknown"],
    ["", "unknown"],
    [undefined, "unknown"],
    [null, "unknown"],
  ])("providerMark(%s) is %s, never the Anthropic glyph for an unknown id", (id, mark) => {
    expect(providerMark(id)).toBe(mark);
  });

  it.each([
    ["anthropic-general", "claude"],
    ["anthropic-fable", "claude"],
    ["openai-codex", "codex"],
    ["cursor-models", "cursor"],
    ["cursor-other", "cursor"],
    ["devin", "devin"],
    ["muse", "muse"],
    ["glm", "glm"],
    ["new-pool", "unknown"],
  ])("pool %s shows the %s mark", (pool, mark) => {
    expect(poolProvider(pool)).toBe(mark);
  });

  it.each([
    ["grok-4", "grok"],
    ["muse-wonder-1.0", "muse"],
    ["swe-1.5", "devin"],
    ["composer-1", "cursor"],
    ["claude-opus-5-5", "claude"],
    ["mystery-model", null],
  ])("model %s shows the %s mark", (model, mark) => {
    expect(modelMark(model)).toBe(mark);
  });

  it.each([
    ["muse", "Muse"],
    ["cursor", "Cursor"],
    ["devin", "Devin"],
    ["openai", "Codex"],
    ["acme", "Acme"],
    [null, "Other"],
  ])("vendor %s is labelled %s", (vendor, label) => {
    expect(vendorName(vendor)).toBe(label);
  });
});
