import type { PolicyDetail } from "../../api/policy";
import { ModelChip } from "../../kit/primitives";
import { isRecord, text } from "./policy-values";

function pathLabel(path: string) {
  const parts = path.split("/").filter(Boolean);
  const classIndex = parts.findIndex((part) => part === "classes");
  if (classIndex >= 0 && parts[classIndex + 2] === "chain") {
    return { title: parts[classIndex + 1], detail: "options in order" };
  }
  const last = parts.at(-1) || path;
  return {
    title: last.replace(/([a-z])([A-Z])/g, "$1 $2").replaceAll("_", " "),
    detail: parts.slice(0, -1).join(" · "),
  };
}
function chain(value: unknown): { model: string; seat?: string }[] | null {
  if (
    !Array.isArray(value) ||
    !value.every((item) => isRecord(item) && typeof item.model === "string")
  ) {
    return null;
  }
  return value.map((item) => ({
    model: item.model as string,
    seat: typeof item.seat === "string" ? item.seat : undefined,
  }));
}
export function Difference({ change }: { change: PolicyDetail["diff"][number] }) {
  const label = pathLabel(change.path);
  const before = chain(change.before);
  const after = chain(change.after);
  return (
    <div className="row">
      <div>
        <span className="strong">{label.title}</span>
        {label.detail && <div className="muted policy-small">{label.detail}</div>}
      </div>
      <div className="policy-change">
        {([change.before, change.after] as unknown[]).map((value, index) => {
          const options = index === 0 ? before : after;
          return (
            <div className={`policy-change-line ${index === 0 ? "old" : ""}`} key={index}>
              <span className="policy-sign" aria-label={index === 0 ? "Before" : "After"}>
                {index === 0 ? "−" : "+"}
              </span>
              {options ? (
                <div className="policy-chain">
                  {options.map((option, i) => (
                    <span className="policy-chain-item" key={`${option.model}-${i}`}>
                      {i > 0 && <span className="muted">›</span>}
                      <ModelChip model={option.model} seat={option.seat} />
                    </span>
                  ))}
                </div>
              ) : (
                <span className="mono policy-value">{text(value)}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
