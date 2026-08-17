import type { Finding } from "@/lib/types";

const SEVERITY_STYLES: Record<Finding["severity"], string> = {
  error: "bg-red-100 text-red-800",
  warning: "bg-amber-100 text-amber-800",
  info: "bg-slate-100 text-slate-700",
};

export function FindingsPanel({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) {
    return <p className="text-sm text-slate-400">No findings recorded yet.</p>;
  }

  return (
    <ul className="space-y-2">
      {findings.map((f, i) => (
        <li key={i} className="card">
          <div className="flex items-center gap-2">
            <span className={`badge ${SEVERITY_STYLES[f.severity]}`}>{f.severity}</span>
            <span className="text-xs uppercase tracking-wide text-slate-400">
              {f.rule_id ?? "LLM critic"}
            </span>
            <span
              className={`badge ml-auto ${
                f.resolution_status === "open"
                  ? "bg-red-50 text-red-700"
                  : f.resolution_status === "resolved"
                    ? "bg-green-50 text-green-700"
                    : "bg-slate-100 text-slate-600"
              }`}
            >
              {f.resolution_status.replace(/_/g, " ")}
            </span>
          </div>
          <p className="mt-2 text-sm text-slate-800">{f.message}</p>
          {f.related_component_ids.length > 0 && (
            <p className="mt-1 text-xs text-slate-400">
              Related: {f.related_component_ids.join(", ")}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}
