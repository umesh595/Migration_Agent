"use client";

import { useState } from "react";

import type { PatchAuditEntry } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

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

const OUTCOME_VARIANT: Record<PatchAuditEntry["outcome"], "teal" | "coral"> = {
  applied: "teal",
  rejected: "coral",
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
      <Card className="p-4 text-sm text-muted-foreground">
        No patches proposed yet.
      </Card>
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
    <Card className="p-4">
      <div className="mb-3 border-b border-border pb-3">
        <p className="text-sm font-semibold text-foreground">Model change ledger</p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          Every suggested architecture change is recorded here with its outcome, reason, and effect on planning.
          Applied patches changed the model. Rejected patches were blocked because they duplicated existing facts or
          failed validation.
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-[1fr_auto] sm:items-start">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Latest recommendation</p>
          <div className="mt-2 rounded-lg border border-atlas-teal/20 bg-atlas-teal-soft p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={OUTCOME_VARIANT[latest.outcome]}>{latest.outcome}</Badge>
              <span className="text-xs text-muted-foreground">
                v{latest.model_version_before}
                {latest.model_version_after !== null ? ` -> v${latest.model_version_after}` : ""}
              </span>
              {onReviewPatch && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="ml-auto text-xs"
                  onClick={() => onReviewPatch(formatPatchReviewDraft(latest))}
                >
                  Review
                </Button>
              )}
            </div>
            <p className="mt-2 break-words font-mono text-xs text-foreground">{summarizePatch(latest.patch)}</p>
            <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
              <div className="rounded-lg border border-border bg-background/50 p-2">
                <p className="font-medium text-foreground">What this changes</p>
                <p className="mt-1 leading-5 text-muted-foreground">{patchImpact(latest.patch)}</p>
              </div>
              <div className="rounded-lg border border-border bg-background/50 p-2">
                <p className="font-medium text-foreground">Why recommended</p>
                <p className="mt-1 leading-5 text-muted-foreground">{latest.justification}</p>
              </div>
            </div>
            {latest.reason && (
              <p className="mt-2 rounded-lg border border-atlas-amber/25 bg-atlas-amber-soft p-2 text-xs leading-5 text-atlas-amber">
                Validator note: {latest.reason}
              </p>
            )}
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2 text-center sm:min-w-56">
          <div className="stat-tile !p-2.5">
            <span className="stat-tile-value !text-lg">{records.length}</span>
            <span className="stat-tile-label">total</span>
          </div>
          <div className="stat-tile !p-2.5">
            <span className="stat-tile-value !text-lg text-atlas-teal">{appliedCount}</span>
            <span className="stat-tile-label">accepted</span>
          </div>
          <div className="stat-tile !p-2.5">
            <span className="stat-tile-value !text-lg text-atlas-coral">{rejectedCount}</span>
            <span className="stat-tile-label">rejected</span>
          </div>
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-border">
        {onReviewPatch && (
          <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/40 p-2.5 text-xs">
            <span className="mr-auto text-muted-foreground">
              {selectedIndexes.size === 0
                ? "Select patches to review together"
                : `${selectedIndexes.size} selected`}
            </span>
            <Button type="button" variant="outline" size="sm" className="text-xs" onClick={selectAll}>
              Select all
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="text-xs"
              disabled={selectedIndexes.size === 0}
              onClick={clearSelected}
            >
              Clear
            </Button>
            <Button
              type="button"
              size="sm"
              className="bg-atlas-teal text-xs text-white hover:bg-atlas-teal/90"
              disabled={selectedRecords.length === 0}
              onClick={() => onReviewPatch(formatMultiPatchReviewDraft(selectedRecords))}
            >
              Review selected
            </Button>
          </div>
        )}
        <div className="max-h-72 overflow-y-auto">
          <ul className="divide-y divide-border">
            {history.map(({ record: r, index }) => (
              <li
                key={`${index}-${summarizePatch(r.patch)}`}
                className={cn(
                  "grid gap-2 border-l-2 p-2.5 text-xs sm:grid-cols-[10rem_1fr]",
                  r.outcome === "applied" ? "border-l-atlas-teal/60" : "border-l-atlas-coral/60"
                )}
              >
                <div className="flex items-center gap-2">
                  {onReviewPatch && (
                    <Checkbox
                      checked={selectedIndexes.has(index)}
                      onCheckedChange={() => toggleSelected(index)}
                      aria-label={`Select ${summarizePatch(r.patch)} for batch review`}
                    />
                  )}
                  <Badge variant={OUTCOME_VARIANT[r.outcome]}>{r.outcome}</Badge>
                  <span className="text-muted-foreground">
                    v{r.model_version_before}
                    {r.model_version_after !== null ? ` -> v${r.model_version_after}` : ""}
                  </span>
                </div>
                <div>
                  <div className="flex flex-wrap items-start gap-2">
                    <p className="min-w-0 flex-1 break-words font-mono text-foreground">{summarizePatch(r.patch)}</p>
                    {onReviewPatch && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="shrink-0 text-xs"
                        onClick={() => onReviewPatch(formatPatchReviewDraft(r))}
                      >
                        Review
                      </Button>
                    )}
                  </div>
                  <p className="mt-1 text-muted-foreground">
                    <span className="font-medium text-foreground">Effect:</span> {patchImpact(r.patch)}
                  </p>
                  <p className="mt-0.5 text-muted-foreground">
                    <span className="font-medium text-foreground">Reason:</span> {r.justification}
                  </p>
                  {r.reason && <p className="mt-0.5 text-muted-foreground">Validator note: {r.reason}</p>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}
