import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { DropdownMenu, Dialog } from "radix-ui";
import {
  Search,
  MoreHorizontal,
  ChevronDown,
  Circle,
  CirclePause,
  Clock3,
  TriangleAlert,
  Unplug,
  CircleOff,
} from "lucide-react";
import { toast } from "sonner";
import { clock, dayTime, duration, pct } from "../format";
import { ProviderMark } from "./provider-mark";
import { modelMark } from "./provider-mark-helpers";

export function PageHead({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="phead">
      <div>
        <h1>{title}</h1>
        {children && <p>{children}</p>}
      </div>
      {action}
    </div>
  );
}
export function Section({
  title,
  aside,
  children,
  className = "",
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`sec ${className}`}>
      <div className="sec-h">
        <h2>{title}</h2>
        {aside && <span className="aside">{aside}</span>}
      </div>
      {children}
    </section>
  );
}
export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`panel ${className}`}>{children}</div>;
}
export function LimitMeter({
  remaining,
  resetAt,
  paceTick,
  paceDelta,
  empty = "No reading",
  unit,
  detail,
  noWindow = false,
  notStarted = false,
  title,
}: {
  remaining?: number | null;
  resetAt?: string | null;
  paceTick?: number | null;
  paceDelta?: number | null;
  empty?: string;
  unit?: "wk" | "mo" | "5h";
  detail?: string;
  noWindow?: boolean;
  notStarted?: boolean;
  title?: string;
}) {
  const [now] = useState(() => Date.now());
  if (noWindow) return <span className="sr">No 5-hour window</span>;
  if (remaining == null)
    return (
      <div className="lim na" title={title}>
        <span className="w">{empty}</span>
      </div>
    );
  const percent = Math.min(100, Math.max(0, remaining));
  const paceTitle =
    paceDelta == null
      ? "Expected remaining at this point"
      : paceDelta < -0.5
        ? `${Math.round(Math.abs(paceDelta))}% short of pace: at this rate it runs out before the reset`
        : `On pace for ${Math.round(Math.max(0, paceDelta))}% to spare at reset`;
  return (
    <div className={`lim ${fillTone(percent)}`} title={title}>
      <div className="lt">
        <span className={`v ${percent < 20 ? "warn" : ""}`}>
          {percent < 20 && <TriangleAlert size={12} aria-label="Low remaining" />}
          {pct(percent)}
          {unit && <span className={`u ${unit === "5h" ? "m" : ""}`}>{unit}</span>}
        </span>
        <span className="r">
          {notStarted
            ? "not started"
            : resetAt
              ? unit === "5h"
                ? duration(new Date(resetAt).getTime() - now)
                : dayTime(resetAt)
              : null}
        </span>
      </div>
      <div className="bar">
        <i style={{ width: `${percent}%` }} />
        {paceTick != null && (
          <b style={{ left: `${Math.min(100, Math.max(0, paceTick))}%` }} title={paceTitle} />
        )}
      </div>
      {detail && (
        <span className="ls" title={detail}>
          {detail}
        </span>
      )}
    </div>
  );
}
function StateGlyph({ status }: { status: string }) {
  const props = { size: 14, strokeWidth: 1.8, "aria-hidden": true as const };
  switch (status) {
    case "active":
      return <Circle {...props} fill="currentColor" />;
    case "paused":
      return <CirclePause {...props} />;
    case "rate_limited":
      return <Clock3 {...props} />;
    case "quota_exceeded":
      return <TriangleAlert {...props} />;
    case "reauth_required":
      return <Unplug {...props} />;
    case "deactivated":
      return <CircleOff {...props} />;
    default:
      return <Circle {...props} />;
  }
}
/** Bar fill tone by remaining: green above 30%, amber from 10 to 30%, red below 10%. */
const fillTone = (remaining: number) => (remaining > 30 ? "ok" : remaining >= 10 ? "warn" : "bad");
const accountTone: Record<string, string> = {
  active: "ok",
  rate_limited: "warn",
  quota_exceeded: "warn",
  reauth_required: "bad",
};
export function AccountState({
  status,
  resetAt,
  sub,
}: {
  status: string;
  resetAt?: string | null;
  sub?: string;
}) {
  const text: Record<string, string> = {
    active: "Active",
    paused: "Paused",
    rate_limited: "Rate limited",
    quota_exceeded: "Out",
    reauth_required: "Disconnected",
    deactivated: "Off",
  };
  const [now] = useState(() => Date.now());
  const suffix = resetAt
    ? new Date(resetAt).getTime() - now > 86_400_000
      ? dayTime(resetAt)
      : clock(resetAt)
    : "reset";
  return (
    <div className="qs">
      <span
        className={`st ${["paused", "deactivated"].includes(status) ? "d" : ""} ${accountTone[status] ? `tone ${accountTone[status]}` : ""}`}
      >
        <StateGlyph status={status} />
        {text[status] || status}
      </span>
      <span className="sb">
        {sub ||
          (status === "rate_limited"
            ? `until ${suffix}`
            : status === "quota_exceeded"
              ? `until ${suffix}`
              : status === "reauth_required"
                ? "out of rotation"
                : "")}
      </span>
    </div>
  );
}
export function PoolState({
  status,
  ready,
  total,
  note,
}: {
  status: string;
  ready?: number;
  total?: number;
  note?: string;
}) {
  const low = status === "low";
  const critical = status === "critical";
  const out = ["exhausted", "unavailable"].includes(status);
  return (
    <div className="qs">
      <span className="st">
        <span className={`tone ${out || low || critical ? "warn" : "ok"}`}>
          {critical ? (
            <TriangleAlert size={14} aria-hidden="true" />
          ) : out ? (
            <Circle size={14} aria-hidden="true" />
          ) : low ? (
            <Circle size={14} className="lb-half-circle" aria-hidden="true" />
          ) : (
            <Circle size={14} fill="currentColor" aria-hidden="true" />
          )}
          {out ? "Exhausted" : critical ? "Critical" : low ? "Low" : "OK"}
        </span>
        {ready != null && total != null && (
          <span className="rd">
            · {ready} of {total}
          </span>
        )}
      </span>
      {note && <span className="sb">{note}</span>}
    </div>
  );
}
export function Pace({ value }: { value?: number | null }) {
  return (
    <div className="pace">
      <span className="num">{value == null ? "—" : `${Math.round(Math.abs(value))}%`}</span>
      {value != null && (
        <span className="s">{value < -0.5 ? "short" : value > 0.5 ? "to spare" : "on pace"}</span>
      )}
    </div>
  );
}
export function ModelChip({
  model,
  alias,
  seat,
  excluded = false,
}: {
  model: string;
  alias?: string;
  seat?: string;
  excluded?: boolean;
}) {
  const mark = modelMark(model);
  return (
    <span className={`chip ${excluded ? "x" : ""}`}>
      {mark && <ProviderMark id={mark} size={16} />}
      <span className="nm">{alias || model}</span>
      {seat && <span className="seat">{seat}</span>}
    </span>
  );
}
export function Seg({
  options,
  value,
  onChange,
}: {
  options: { label: string; value: string; count?: number }[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="seg" role="group">
      {options.map((o) => (
        <button key={o.value} aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
          {o.count != null && <span className="n">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}
export function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { label: string; value: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="select">
        <span className="k">{label}</span>
        {options.find((o) => o.value === value)?.label || value}
        <ChevronDown size={13} />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="lb-dropdown" sideOffset={5} align="end">
          {options.map((o) => (
            <DropdownMenu.Item key={o.value} onSelect={() => onChange(o.value)}>
              {o.label}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
export function SearchField({
  value,
  onChange,
  placeholder = "Search accounts",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        e.key === "/" &&
        !(e.target instanceof HTMLInputElement) &&
        !(e.target instanceof HTMLTextAreaElement)
      ) {
        e.preventDefault();
        ref.current?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  return (
    <label className="field" style={{ width: 220 }}>
      <Search size={15} />
      <input
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
      />
      <span className="kbd">/</span>
    </label>
  );
}
export function RowMenu({
  label,
  items,
}: {
  label: string;
  items: { label: string; onSelect: () => void; hidden?: boolean }[];
}) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        className="btn ghost sm icon"
        aria-label={label}
        onClick={(e) => e.stopPropagation()}
      >
        <MoreHorizontal size={16} />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          className="lb-dropdown"
          sideOffset={5}
          align="end"
          onClick={(e) => e.stopPropagation()}
        >
          {items
            .filter((i) => !i.hidden)
            .map((i) => (
              <DropdownMenu.Item key={i.label} onSelect={i.onSelect}>
                {i.label}
              </DropdownMenu.Item>
            ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
export function Crumbs({ items }: { items: { label: string; to?: string }[] }) {
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      {items.map((item, i) => (
        <span key={item.label}>
          {i > 0 && " / "}
          {item.to ? <Link to={item.to}>{item.label}</Link> : item.label}
        </span>
      ))}
    </nav>
  );
}
export function EmptyState({ title, description }: { title: string; description?: string }) {
  return (
    <div className="lb-empty">
      <h2>{title}</h2>
      {description && <p>{description}</p>}
    </div>
  );
}
export { toast as Toast };
export function SidePanel({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="lb-sheet-overlay" />
        <Dialog.Content className="lb lb-sheet">
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Close
            className="btn ghost icon"
            aria-label="Close"
            style={{ position: "absolute", right: 20, top: 20 }}
          >
            ×
          </Dialog.Close>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
