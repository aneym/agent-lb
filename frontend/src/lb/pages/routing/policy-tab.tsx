import { useState, type FormEvent } from "react";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api-client";
import {
  createPolicyDraft,
  getPolicyDetail,
  discardPolicyDraft,
  policyKeys,
  updatePolicyDraft,
  useRoutingPolicyDetail,
  useRoutingPolicyVersions,
  type PolicyDetail,
  type PolicyTable,
  type PolicyVersion,
} from "../../api/policy";
import { EmptyState, ModelChip, SidePanel } from "../../kit/primitives";
import "./policy.css";

const approvalMessage = "No replay runner exists yet; activate through install-policy.";
type SectionKey = "Classes" | "Aliases" | "Retired" | "Pace limits" | "Decider";
const sections: SectionKey[] = ["Classes", "Aliases", "Retired", "Pace limits", "Decider"];

function errorMessage(error: unknown) {
  return error instanceof ApiError || error instanceof Error ? error.message : "Request failed";
}
function relativeDate(date: string) {
  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return date;
  const minutes = Math.floor((Date.now() - parsed.getTime()) / 60_000);
  if (minutes >= 0 && minutes < 60) return `${Math.max(1, minutes)} min ago`;
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function versionSource(version: PolicyVersion, blur: boolean) {
  const date = relativeDate(version.approvedAt || version.createdAt);
  if (version.approvedBy) return `approved by ${blur ? "••••" : version.approvedBy} · ${date}`;
  if (version.source === "file") return `installed from file · ${date}`;
  if (version.state === "draft") return `you · ${date}`;
  return `${blur ? "••••" : version.createdBy || "created"} · ${date}`;
}
function VersionBadge({ state }: { state: PolicyVersion["state"] }) {
  return <span className={`policy-badge ${state}`}>{state[0].toUpperCase() + state.slice(1)}</span>;
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}
function entries(value: unknown) {
  return Object.entries(record(value));
}
function text(value: unknown) {
  if (value === undefined) return "not set";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
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
function groupDifferences(detail: PolicyDetail, active?: PolicyDetail) {
  const changes = new Map<string, PolicyDetail["diff"][number]>();
  for (const change of detail.diff) {
    const chainPath = change.path.match(/^\/routingTable\/classes\/([^/]+)\/chain(?:\/.*)?$/);
    if (chainPath && active) {
      const name = chainPath[1].replaceAll("~1", "/").replaceAll("~0", "~");
      const path = `/routingTable/classes/${chainPath[1]}/chain`;
      changes.set(path, {
        path,
        before: record(record(record(active.routingTable).classes)[name]).chain,
        after: record(record(record(detail.routingTable).classes)[name]).chain,
      });
    } else {
      changes.set(change.path, change);
    }
  }
  return [...changes.values()];
}
function Difference({ change }: { change: PolicyDetail["diff"][number] }) {
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
function sectionValue(detail: PolicyDetail, section: SectionKey) {
  const table = detail.routingTable;
  switch (section) {
    case "Classes":
      return table.classes;
    case "Aliases":
      return table.aliases;
    case "Retired":
      return table.retired;
    case "Pace limits":
      return record(table.policy).pace;
    case "Decider":
      return detail.decider;
  }
}
function summary(
  detail: PolicyDetail,
  section: SectionKey,
  expanded: boolean,
  onExpand: () => void,
) {
  const table = detail.routingTable;
  if (section === "Classes") {
    const judgment = record(table.policy).judgment_classes;
    const kinds = Array.isArray(judgment) ? judgment.join(", ") : "";
    return (
      <span className="muted">
        {detail.counts.classes} kinds of task, {detail.counts.options} options across their lists.
        {kinds && ` Judgment work: ${kinds}.`}
      </span>
    );
  }
  if (section === "Aliases") {
    return (
      <div className="policy-tags">
        {entries(table.aliases).map(([alias, value]) => {
          const definition = record(value);
          const target =
            definition.model ||
            definition.resolved ||
            (definition.family ? `${definition.family} family` : null) ||
            (definition.harness ? `${definition.harness} harness` : null) ||
            "not set";
          return (
            <span className="tag" key={alias}>
              {alias} → {text(target)}
            </span>
          );
        })}
      </div>
    );
  }
  if (section === "Retired") {
    const patterns = Array.isArray(table.retired) ? table.retired : [];
    return (
      <div className="policy-tags">
        {(expanded ? patterns : patterns.slice(0, 5)).map((pattern, i) => (
          <span className="tag" key={i}>
            {text(pattern)}
          </span>
        ))}
        {!expanded && patterns.length > 5 && (
          <button className="tag policy-more" onClick={onExpand}>
            +{patterns.length - 5} more
          </button>
        )}
      </div>
    );
  }
  if (section === "Pace limits") {
    const pace = record(record(table.policy).pace);
    const ahead = pace.ahead_gt;
    const behind = pace.behind_lt;
    const cycle = pace.cycle_hours;
    const sentences = [
      typeof ahead === "number" &&
        `More than ${ahead}% to spare: the pool also takes lighter work.`,
      typeof behind === "number" &&
        `More than ${Math.abs(behind)}% short: it takes judgment work only.`,
      typeof cycle === "number" && `Pace is measured over each ${cycle / 24}-day cycle.`,
    ].filter(Boolean);
    return (
      <span className="muted">
        {sentences.length ? sentences.join(" ") : "No pace limits configured."}
      </span>
    );
  }
  const decider = detail.decider;
  const parts = [
    decider.decider && `${text(decider.decider)} decider`,
    decider.jev_timeout_s != null && `gives up after ${text(decider.jev_timeout_s)} s`,
    decider.pick_ttl_s != null && `reuses a pick for ${text(decider.pick_ttl_s)} s`,
    decider.max_candidates != null && `weighs up to ${text(decider.max_candidates)} options`,
  ].filter(Boolean);
  return (
    <span className="muted">
      {parts.length ? parts.join(" · ") : "No decider settings configured."}
    </span>
  );
}
function withSection(detail: PolicyDetail, section: SectionKey, value: unknown) {
  if (section === "Decider") return { decider: value as PolicyTable };
  const routingTable = { ...detail.routingTable };
  if (section === "Pace limits") {
    routingTable.policy = { ...record(routingTable.policy), pace: value };
  } else {
    routingTable[section.toLowerCase()] = value;
  }
  return { routingTable };
}

export function PolicyTab() {
  const client = useQueryClient();
  const blurred = usePrivacyStore((state) => state.blurred);
  const versions = useRoutingPolicyVersions();
  const [selection, setSelection] = useState<number | null>(null);
  const selected =
    selection != null && versions.data?.versions.some((v) => v.version === selection)
      ? selection
      : (versions.data?.versions[0]?.version ?? null);
  const detail = useRoutingPolicyDetail(selected);
  const active = useRoutingPolicyDetail(versions.data?.activeVersion ?? null);
  const changes = detail.data ? groupDifferences(detail.data, active.data) : [];
  const [newDraft, setNewDraft] = useState(false);
  const [draftSummary, setDraftSummary] = useState("");
  const [editor, setEditor] = useState<SectionKey | null>(null);
  const [editorVersion, setEditorVersion] = useState<number | null>(null);
  const [editorText, setEditorText] = useState("");
  const [jsonOpen, setJsonOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["lb", "policy"] });
  };
  async function makeDraft(event: FormEvent) {
    event.preventDefault();
    if (!draftSummary.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await createPolicyDraft({ summary: draftSummary.trim() });
      await refresh();
      setSelection(result.version);
      setNewDraft(false);
      setDraftSummary("");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function startEdit(section: SectionKey) {
    if (!detail.data) return;
    setBusy(true);
    setMessage("");
    try {
      let target = detail.data;
      if (target.state !== "draft") {
        target = await createPolicyDraft({
          summary: `Edit ${section.toLowerCase()} from v${target.version}`,
          routingTable: target.routingTable,
          decider: target.decider,
        });
        await refresh();
        setSelection(target.version);
      }
      setEditorVersion(target.version);
      setEditorText(JSON.stringify(sectionValue(target, section), null, 2));
      setEditor(section);
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function saveEdit() {
    if (!editor || editorVersion == null || !detail.data) return;
    let value: unknown;
    try {
      value = JSON.parse(editorText);
    } catch {
      setMessage("Enter valid JSON before saving.");
      return;
    }
    if (
      (editor === "Retired" &&
        (!Array.isArray(value) || !value.every((v) => typeof v === "string"))) ||
      (editor !== "Retired" && !isRecord(value))
    ) {
      setMessage(
        editor === "Retired"
          ? "Retired must be an array of strings."
          : "This section must be a JSON object.",
      );
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const target =
        editorVersion === detail.data.version
          ? detail.data
          : await client.fetchQuery({
              queryKey: policyKeys.detail(editorVersion),
              queryFn: () => getPolicyDetail(editorVersion),
            });
      await updatePolicyDraft(editorVersion, withSection(target, editor, value));
      await refresh();
      setEditor(null);
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function discard() {
    if (
      !detail.data ||
      !window.confirm(`Discard draft v${detail.data.version}? This cannot be undone.`)
    )
      return;
    setBusy(true);
    setMessage("");
    try {
      await discardPolicyDraft(detail.data.version);
      setSelection(null);
      await refresh();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  if (versions.isPending) return <EmptyState title="Loading policy versions…" />;
  if (versions.isError)
    return (
      <div className="policy-error" role="alert">
        Could not load routing policy versions. {errorMessage(versions.error)}
      </div>
    );
  return (
    <div className="policy-split">
      <aside className="policy-list">
        <div className="sec-h">
          <h2>Versions</h2>
          <button className="btn sm" onClick={() => setNewDraft((open) => !open)}>
            + New draft
          </button>
        </div>
        {newDraft && (
          <form className="policy-new" onSubmit={(event) => void makeDraft(event)}>
            <label htmlFor="policy-summary">Draft summary</label>
            <input
              id="policy-summary"
              autoFocus
              value={draftSummary}
              onChange={(event) => setDraftSummary(event.target.value)}
              placeholder="What will change?"
            />
            <div>
              <button className="btn sm primary" disabled={busy || !draftSummary.trim()}>
                Create draft
              </button>
              <button type="button" className="btn sm ghost" onClick={() => setNewDraft(false)}>
                Cancel
              </button>
            </div>
          </form>
        )}
        <div className="policy-versions">
          {versions.data.versions.map((version) => (
            <button
              className={`policy-version ${selected === version.version ? "selected" : ""}`}
              aria-current={selected === version.version ? "true" : undefined}
              key={version.version}
              onClick={() => {
                setSelection(version.version);
                setExpanded(false);
                setMessage("");
              }}
            >
              <span className="policy-version-top">
                <span className="mono">v{version.version}</span>
                <VersionBadge state={version.state} />
              </span>
              <span>{version.summary}</span>
              <span className="muted policy-small">{versionSource(version, blurred)}</span>
            </button>
          ))}
          {!versions.data.versions.length && <EmptyState title="No policy versions yet" />}
        </div>
        <p className="muted policy-hint">
          Every change is a new version. A draft goes live only after a replay against the eval
          baseline and an approval.
        </p>
      </aside>
      <div className="policy-detail">
        {message && (
          <p className="policy-error" role="alert">
            {message}
          </p>
        )}
        {detail.isPending && selected != null && <EmptyState title="Loading version…" />}
        {detail.isError && (
          <div className="policy-error" role="alert">
            Could not load this policy version. {errorMessage(detail.error)}
          </div>
        )}
        {detail.data && (
          <>
            <div className="policy-header">
              <div className="policy-heading">
                <div className="policy-heading-line">
                  <span className="mono strong">v{detail.data.version}</span>
                  <VersionBadge state={detail.data.state} />
                  {detail.data.version !== versions.data.activeVersion &&
                    versions.data.activeVersion != null && (
                      <span className="muted policy-small">
                        compared with v{versions.data.activeVersion} (active)
                      </span>
                    )}
                </div>
                <span>{detail.data.summary}</span>
              </div>
              {detail.data.state === "draft" && (
                <div className="policy-actions">
                  <button className="btn" disabled title="No replay runner yet">
                    Replay
                  </button>
                  <button className="btn primary" disabled title={approvalMessage}>
                    Approve and activate
                  </button>
                  <button className="btn ghost" disabled={busy} onClick={() => void discard()}>
                    Discard
                  </button>
                  <span className="muted policy-approval">{approvalMessage}</span>
                </div>
              )}
            </div>
            <section className="sec">
              <div className="sec-h">
                <h2>What changes</h2>
                <span className="aside">
                  {changes.length} {changes.length === 1 ? "edit" : "edits"}
                </span>
              </div>
              <div className="rows policy-diff">
                {detail.data.diff.length ? (
                  changes.map((change, index) => (
                    <Difference change={change} key={`${change.path}-${index}`} />
                  ))
                ) : (
                  <p className="policy-no-diff muted">
                    {detail.data.state === "active"
                      ? "This is the active version."
                      : "No changes compared with the active version."}
                  </p>
                )}
              </div>
            </section>
            <section className="sec">
              <div className="sec-h">
                <h2>Everything in v{detail.data.version}</h2>
                <button className="policy-link" onClick={() => setJsonOpen(true)}>
                  View as JSON
                </button>
              </div>
              <div className="rows policy-config">
                {sections.map((section) => (
                  <div className="row" key={section}>
                    <span className="strong">{section}</span>
                    {summary(detail.data!, section, expanded, () => setExpanded(true))}
                    <span className="policy-edit">
                      <button
                        className="btn sm"
                        disabled={busy}
                        onClick={() => void startEdit(section)}
                      >
                        Edit
                      </button>
                    </span>
                  </div>
                ))}
              </div>
            </section>
            <SidePanel
              open={jsonOpen}
              onOpenChange={setJsonOpen}
              title={`Policy v${detail.data.version} · JSON`}
            >
              <pre className="policy-json">
                {JSON.stringify(
                  { routingTable: detail.data.routingTable, decider: detail.data.decider },
                  null,
                  2,
                )}
              </pre>
            </SidePanel>
            <SidePanel
              open={editor != null}
              onOpenChange={(open) => {
                if (!open) {
                  setEditor(null);
                  setMessage("");
                }
              }}
              title={`Edit ${editor || "policy"} · v${editorVersion}`}
            >
              <p className="muted">Edit this section as JSON. Saving updates the draft only.</p>
              <textarea
                className="policy-editor"
                aria-label={`${editor} JSON`}
                spellCheck={false}
                value={editorText}
                onChange={(event) => setEditorText(event.target.value)}
              />
              {message && (
                <p className="policy-error" role="alert">
                  {message}
                </p>
              )}
              <div className="policy-editor-actions">
                <button className="btn primary" disabled={busy} onClick={() => void saveEdit()}>
                  Save draft
                </button>
                <button className="btn ghost" onClick={() => setEditor(null)}>
                  Cancel
                </button>
              </div>
            </SidePanel>
          </>
        )}
      </div>
    </div>
  );
}
