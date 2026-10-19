export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
export function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}
export function entries(value: unknown) {
  return Object.entries(record(value));
}
export function text(value: unknown) {
  if (value === undefined) return "not set";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
