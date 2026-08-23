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

const OUTCOME_STYLES: Record<PatchAuditEntry["outcome"], string> = {
  applied: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  rejected: "border-rose-400/30 bg-rose-400/10 text-rose-300",
};

export function AuditTrailPanel({ records }: { records: PatchAuditEntry[] }) {
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
  const history = [...records].reverse();

  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
      <div className="grid gap-2 sm:grid-cols-[1fr_auto] sm:items-start">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Latest model change</p>
          <div className="mt-2 rounded-lg border border-brand-400/20 bg-brand-400/[0.06] p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`badge ${OUTCOME_STYLES[latest.outcome]}`}>{latest.outcome}</span>
              <span className="text-xs text-slate-500">
                v{latest.model_version_before}
                {latest.model_version_after !== null ? ` -> v${latest.model_version_after}` : ""}
              </span>
            </div>
            <p className="mt-2 break-words font-mono text-xs text-slate-200">{summarizePatch(latest.patch)}</p>
            {latest.reason && <p className="mt-1 text-xs text-slate-500">{latest.reason}</p>}
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

      <div className="mt-3 max-h-72 overflow-y-auto rounded-lg border border-white/[0.05]">
        <ul className="divide-y divide-white/[0.05]">
          {history.map((r, i) => (
            <li
              key={`${history.length - i}-${summarizePatch(r.patch)}`}
              className="grid gap-2 p-2.5 text-xs sm:grid-cols-[8rem_1fr]"
            >
              <div className="flex items-center gap-2">
                <span className={`badge ${OUTCOME_STYLES[r.outcome]}`}>{r.outcome}</span>
                <span className="text-slate-600">
                  v{r.model_version_before}
                  {r.model_version_after !== null ? ` -> v${r.model_version_after}` : ""}
                </span>
              </div>
              <div>
                <p className="break-words font-mono text-slate-300">{summarizePatch(r.patch)}</p>
                {r.reason && <p className="mt-0.5 text-slate-500">{r.reason}</p>}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
