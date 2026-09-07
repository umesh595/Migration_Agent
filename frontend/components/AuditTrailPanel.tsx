"use client";

import { useState } from "react";

import type { PatchAuditEntry } from "@/lib/types";

function summarizePatch(patch: Record<string, unknown>): string {
  const op = typeof patch.op === "string" ? patch.op : "unknown_op";
  switch (op) {
    case "add_component":
      return `add_component: ${patch.id}`;
    case "update_component":
      return `update_component: ${patch.id}`;
    case "remove_component":
      return `remove_component: ${patch.id}`;
    case "add_dependency":
      return `add_dependency: ${patch.source_id} -> ${patch.target_id} (${patch.kind})`;
    case "remove_dependency":
      return `remove_dependency: ${patch.source_id} -> ${patch.target_id}`;
    case "add_assumption":
      return "add_assumption";
    case "resolve_open_question":
      return `resolve_open_question: ${patch.question_id}`;
    default:
      return op;
  }
}

function patchImpact(patch: Record<string, unknown>): string {
  const op = typeof patch.op === "string" ? patch.op : "unknown_op";
  switch (op) {
    case "add_component":
      return `Adds ${patch.id} to the discovered architecture model so later planning can sequence, cost, and risk it.`;
    case "update_component":
      return `Updates the recorded properties for ${patch.id}, such as description, criticality, environment, or technology.`;
    case "remove_component":
      return `Removes ${patch.id} from the architecture model. This should only happen when the source model was wrong or the component is explicitly out of scope.`;
    case "add_dependency":
      return `Connects ${patch.source_id} to ${patch.target_id} as ${patch.kind}. This affects migration ordering, coexistence checks, risk review, and rollback planning.`;
    case "remove_dependency":
      return `Removes the recorded connection from ${patch.source_id} to ${patch.target_id}. This can change wave ordering and dependency-risk findings.`;
    case "add_assumption":
      return "Records an assumption that the agent will carry into planning and review until the user corrects it.";
    case "resolve_open_question":
      return `Marks open question ${patch.question_id} as answered so the workflow can move forward with less ambiguity.`;
    default:
      return "Records a proposed model change. Review the JSON if the short summary is not enough.";
  }
}

const OUTCOME_STYLES: Record<PatchAuditEntry["outcome"], string> = {
  applied: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  rejected: "border-rose-400/30 bg-rose-400/10 text-rose-300",
};

function formatPatchReviewSection(record: PatchAuditEntry, index?: number): string {
  const lines = [
    index === undefined ? `Patch: ${summarizePatch(record.patch)}` : `Patch ${index}: ${summarizePatch(record.patch)}`,
    `Outcome: ${record.outcome}`,
    `Model version: v${record.model_version_before}${
      record.model_version_after !== null ? ` -> v${record.model_version_after}` : ""
    }`,
    `Why recommended: ${record.justification}`,
  ];

  if (record.reason) {
    lines.push(`Validation note: ${record.reason}`);
  }

  lines.push("", "Patch JSON:", "```json", JSON.stringify(record.patch, null, 2), "```");
  return lines.join("\n");
}

function formatPatchReviewDraft(record: PatchAuditEntry): string {
  return ["Review this suggested architecture patch.", "", formatPatchReviewSection(record), "", "My question/change request:"].join(
    "\n"
  );
}

function formatMultiPatchReviewDraft(records: PatchAuditEntry[]): string {
  return [
    "Review these suggested architecture patches together.",
    "",
    ...records.flatMap((record, index) => [
      formatPatchReviewSection(record, index + 1),
      index < records.length - 1 ? "\n---\n" : "",
    ]),
    "My question/change request:",
  ].join("\n");
}

export function AuditTrailPanel({
  records,
  onReviewPatch,
}: {
  records: PatchAuditEntry[];
  onReviewPatch?: (draft: string) => void;
}) {
  const [selectedIndexes, setSelectedIndexes] = useState<Set<number>>(() => new Set());

  if (records.length === 0) {
    return (
      <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 text-sm text-slate-500">
        No patches proposed yet.
      </div>
    );
  }

  const appliedCount = records.filter((r) => r.outcome === "applied").length;
  const rejectedCount = records.length - appliedCount;
  const latest = records[records.length - 1]!;
  const history = records.map((record, index) => ({ record, index })).reverse();
  const selectedRecords = history.filter(({ index }) => selectedIndexes.has(index)).map(({ record }) => record);

  function toggleSelected(index: number) {
    setSelectedIndexes((current) => {
      const next = new Set(current);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }

  function selectAll() {
    setSelectedIndexes(new Set(records.map((_, index) => index)));
  }

  function clearSelected() {
    setSelectedIndexes(new Set());
  }

  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-4">
      <div className="mb-3 border-b border-white/[0.06] pb-3">
        <p className="text-sm font-semibold text-slate-200">Model change ledger</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">
          Every suggested architecture change is recorded here with its outcome, reason, and effect on planning.
          Applied patches changed the model. Rejected patches were blocked because they duplicated existing facts or
          failed validation.
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-[1fr_auto] sm:items-start">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Latest recommendation</p>
          <div className="mt-2 rounded-lg border border-brand-400/20 bg-brand-400/[0.055] p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`badge ${OUTCOME_STYLES[latest.outcome]}`}>{latest.outcome}</span>
              <span className="text-xs text-slate-500">
                v{latest.model_version_before}
                {latest.model_version_after !== null ? ` -> v${latest.model_version_after}` : ""}
              </span>
              {onReviewPatch && (
                <button
                  type="button"
                  className="btn-secondary ml-auto !px-2.5 !py-1 !text-xs"
                  onClick={() => onReviewPatch(formatPatchReviewDraft(latest))}
                >
                  Review
                </button>
              )}
            </div>
            <p className="mt-2 break-words font-mono text-xs text-slate-200">{summarizePatch(latest.patch)}</p>
            <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
              <div className="rounded-lg border border-white/[0.06] bg-black/10 p-2">
                <p className="font-medium text-slate-300">What this changes</p>
                <p className="mt-1 leading-5 text-slate-500">{patchImpact(latest.patch)}</p>
              </div>
              <div className="rounded-lg border border-white/[0.06] bg-black/10 p-2">
                <p className="font-medium text-slate-300">Why recommended</p>
                <p className="mt-1 leading-5 text-slate-500">{latest.justification}</p>
              </div>
            </div>
            {latest.reason && (
              <p className="mt-2 rounded-lg border border-amber-400/15 bg-amber-400/[0.05] p-2 text-xs leading-5 text-amber-100/80">
                Validator note: {latest.reason}
              </p>
            )}
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2 text-center sm:min-w-56">
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.03] p-2">
            <div className="text-lg font-semibold text-slate-100">{records.length}</div>
            <div className="text-[11px] text-slate-500">total</div>
          </div>
          <div className="rounded-lg border border-emerald-400/20 bg-emerald-400/[0.06] p-2">
            <div className="text-lg font-semibold text-emerald-300">{appliedCount}</div>
            <div className="text-[11px] text-emerald-300/70">accepted</div>
          </div>
          <div className="rounded-lg border border-rose-400/20 bg-rose-400/[0.06] p-2">
            <div className="text-lg font-semibold text-rose-300">{rejectedCount}</div>
            <div className="text-[11px] text-rose-300/70">rejected</div>
          </div>
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-white/[0.05]">
        {onReviewPatch && (
          <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.05] bg-white/[0.02] p-2.5 text-xs">
            <span className="mr-auto text-slate-400">
              {selectedIndexes.size === 0
                ? "Select patches to review together"
                : `${selectedIndexes.size} selected`}
            </span>
            <button type="button" className="btn-secondary !px-2.5 !py-1 !text-xs" onClick={selectAll}>
              Select all
            </button>
            <button
              type="button"
              className="btn-secondary !px-2.5 !py-1 !text-xs"
              disabled={selectedIndexes.size === 0}
              onClick={clearSelected}
            >
              Clear
            </button>
            <button
              type="button"
              className="btn-primary !px-2.5 !py-1 !text-xs"
              disabled={selectedRecords.length === 0}
              onClick={() => onReviewPatch(formatMultiPatchReviewDraft(selectedRecords))}
            >
              Review selected
            </button>
          </div>
        )}
        <div className="max-h-72 overflow-y-auto">
          <ul className="divide-y divide-white/[0.05]">
            {history.map(({ record: r, index }) => (
              <li
                key={`${index}-${summarizePatch(r.patch)}`}
                className="grid gap-2 p-2.5 text-xs sm:grid-cols-[10rem_1fr]"
              >
                <div className="flex items-center gap-2">
                  {onReviewPatch && (
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 rounded border-white/20 bg-white/[0.04] accent-brand-500"
                      checked={selectedIndexes.has(index)}
                      onChange={() => toggleSelected(index)}
                      aria-label={`Select ${summarizePatch(r.patch)} for batch review`}
                    />
                  )}
                  <span className={`badge ${OUTCOME_STYLES[r.outcome]}`}>{r.outcome}</span>
                  <span className="text-slate-600">
                    v{r.model_version_before}
                    {r.model_version_after !== null ? ` -> v${r.model_version_after}` : ""}
                  </span>
                </div>
                <div>
                  <div className="flex flex-wrap items-start gap-2">
                    <p className="min-w-0 flex-1 break-words font-mono text-slate-300">{summarizePatch(r.patch)}</p>
                    {onReviewPatch && (
                      <button
                        type="button"
                        className="btn-secondary shrink-0 !px-2.5 !py-1 !text-xs"
                        onClick={() => onReviewPatch(formatPatchReviewDraft(r))}
                      >
                        Review
                      </button>
                    )}
                  </div>
                  <p className="mt-1 text-slate-400">
                    <span className="font-medium text-slate-300">Effect:</span> {patchImpact(r.patch)}
                  </p>
                  <p className="mt-0.5 text-slate-400">
                    <span className="font-medium text-slate-300">Reason:</span> {r.justification}
                  </p>
                  {r.reason && <p className="mt-0.5 text-slate-500">Validator note: {r.reason}</p>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
