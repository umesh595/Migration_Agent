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

export function AuditTrailPanel({ records }: { records: PatchAuditEntry[] }) {
  if (records.length === 0) {
    return <p className="text-sm text-slate-400">No patches proposed yet.</p>;
  }

  return (
    <ul className="space-y-2">
      {records.map((r, i) => (
        <li key={i} className="card">
          <div className="flex items-center gap-2">
            <span
              className={`badge ${
                r.outcome === "applied" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
              }`}
            >
              {r.outcome}
            </span>
            <span className="text-xs text-slate-400">
              v{r.model_version_before}
              {r.model_version_after !== null ? ` -> v${r.model_version_after}` : ""}
            </span>
          </div>
          <p className="mt-2 font-mono text-xs text-slate-800">{summarizePatch(r.patch)}</p>
          {r.reason && <p className="mt-1 text-xs text-slate-500">{r.reason}</p>}
        </li>
      ))}
    </ul>
  );
}
