const date = (value: string | number | Date) => new Date(value);
export const pct = (value: number | null | undefined) =>
  value == null ? "—" : `${Math.round(value)}%`;
export function duration(milliseconds: number) {
  const minutes = Math.max(0, Math.ceil(milliseconds / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const days = Math.floor(minutes / 1440),
    hours = Math.floor((minutes % 1440) / 60),
    mins = minutes % 60;
  if (days) return `${days}d${hours ? ` ${hours}h` : ""}`;
  return `${hours}h${mins ? ` ${mins}m` : ""}`;
}
export const clock = (value: string | number | Date) =>
  new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false }).format(
    date(value),
  );
export const dayTime = (value: string | number | Date) =>
  new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date(value));
export const resetText = (value: string | number | Date) =>
  `resets in ${duration(date(value).getTime() - Date.now())} · ${clock(value)}`;
export function compact(value: number) {
  const abs = Math.abs(value);
  if (abs < 1000) return new Intl.NumberFormat("en-US").format(value);
  const [unit, divisor] = abs >= 1e9 ? ["B", 1e9] : abs >= 1e6 ? ["M", 1e6] : ["k", 1e3];
  return `${Number((value / (divisor as number)).toPrecision(3))}${unit}`;
}
export const money = (value: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: Math.abs(value) >= 1 ? 0 : 2,
  }).format(value);
export function pace(value: number | null | undefined) {
  if (value == null) return "—";
  if (Math.abs(value) < 0.5) return "on pace";
  return value > 0 ? `${Math.round(value)}% to spare` : `${Math.round(-value)}% short`;
}
export function relative(value: string | number | Date) {
  const ms = Math.max(0, Date.now() - date(value).getTime());
  if (ms < 60_000) return "just now";
  if (ms < 3600_000) return `${Math.floor(ms / 60_000)} min ago`;
  if (ms < 86400_000) return `${Math.floor(ms / 3600_000)} hr ago`;
  return `${Math.floor(ms / 86400_000)} d ago`;
}
type Labelled = { alias?: string | null; planType: string; email: string };
const ACRONYMS = new Set(["glm", "api"]);
const titleCase = (s: string) =>
  ACRONYMS.has(s.toLowerCase()) ? s.toUpperCase() : s.charAt(0).toUpperCase() + s.slice(1);
/** "Max · alex", or "Max · a•••x" when emails are hidden; an alias always wins. */
export function accountLabel(a: Labelled, hideEmails: boolean) {
  if (a.alias) return a.alias;
  const local = a.email.split("@")[0];
  const shown = !hideEmails ? local : local.length <= 2 ? "•••" : `${local[0]}•••${local.at(-1)}`;
  return `${titleCase(a.planType)} · ${shown}`;
}
