"use client";

import type { PatchAuditEntry } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

function summarizeUpdate(patch: Record<string, unknown>): string {
  const op = typeof patch.op === "string" ? patch.op : "unknown";
  switch (op) {
    case "add_component": return `Captured component: ${patch.name ?? patch.id}`;
    case "update_component": return `Updated source detail for: ${patch.id}`;
    case "remove_component": return `Removed incorrect source component: ${patch.id}`;
    case "add_dependency": return `Captured connection: ${patch.source_id} to ${patch.target_id}`;
    case "remove_dependency": return `Removed incorrect connection: ${patch.source_id} to ${patch.target_id}`;
    case "add_assumption": return "Recorded assumption";
    case "confirm_assumption": return `Verified assumption: ${patch.assumption_id}`;
    case "add_open_question": return "Recorded open question";
    case "resolve_open_question": return `Answered open question: ${patch.question_id}`;
    default: return op.replace(/_/g, " ");
  }
}

function evidenceFor(patch: Record<string, unknown>): string | null {
  if (typeof patch.source === "string" && patch.source.trim()) return patch.source;
  if (typeof patch.confidence === "string" && patch.confidence.trim()) return `Confidence: ${patch.confidence}`;
  if (typeof patch.description === "string" && patch.description.trim()) return patch.description;
  return null;
}

function isAssumption(patch: Record<string, unknown>): boolean {
  return patch.op === "add_assumption" || patch.op === "confirm_assumption";
}

function isQuestion(patch: Record<string, unknown>): boolean {
  return patch.op === "add_open_question" || patch.op === "resolve_open_question";
}

export function DiscoveryEvidencePanel({ records }: { records: PatchAuditEntry[] }) {
  if (records.length === 0) {
    return <Card className="p-4 text-sm text-muted-foreground">No source-model evidence has been captured yet.</Card>;
  }

  const recorded = records.filter((record) => record.outcome === "applied");
  const notRecorded = records.length - recorded.length;
  const factCount = recorded.filter((record) => !isAssumption(record.patch) && !isQuestion(record.patch)).length;
  const assumptionCount = recorded.filter((record) => isAssumption(record.patch)).length;
  const questionCount = recorded.filter((record) => isQuestion(record.patch)).length;

  return (
    <Card className="p-4">
      <div className="border-b border-border pb-3">
        <p className="text-sm font-semibold text-foreground">Discovery evidence trail</p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          A record of what was captured about the current system. These are source-model updates, not suggested target
          changes or migration recommendations.
        </p>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <EvidenceStat value={factCount} label="facts" />
        <EvidenceStat value={assumptionCount} label="assumptions" />
        <EvidenceStat value={questionCount} label="questions" />
        <EvidenceStat value={notRecorded} label="not recorded" muted />
      </div>
      <ul className="mt-3 max-h-80 divide-y divide-border overflow-y-auto rounded-lg border border-border">
        {[...records].reverse().map((record, index) => {
          const evidence = evidenceFor(record.patch);
          const wasRecorded = record.outcome === "applied";
          return (
            <li key={`${records.length - index}-${summarizeUpdate(record.patch)}`} className="p-3 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={wasRecorded ? "teal" : "coral"}>{wasRecorded ? "recorded" : "not recorded"}</Badge>
                <span className="text-muted-foreground">v{record.model_version_before}{record.model_version_after !== null ? ` -> v${record.model_version_after}` : ""}</span>
              </div>
              <p className="mt-2 font-medium text-foreground">{summarizeUpdate(record.patch)}</p>
              {evidence && <p className="mt-1 leading-5 text-muted-foreground">Evidence: {evidence}</p>}
              {record.reason && <p className="mt-1 leading-5 text-muted-foreground">Note: {record.reason}</p>}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function EvidenceStat({ value, label, muted = false }: { value: number; label: string; muted?: boolean }) {
  return (
    <div className="stat-tile !p-2.5">
      <span className={muted ? "stat-tile-value !text-lg text-muted-foreground" : "stat-tile-value !text-lg"}>{value}</span>
      <span className="stat-tile-label">{label}</span>
    </div>
  );
}
