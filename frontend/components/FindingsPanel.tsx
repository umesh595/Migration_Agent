"use client";

import { useState } from "react";

import { ApiError, resolveFinding } from "@/lib/api";
import type { Finding } from "@/lib/types";

const SEVERITY_STYLES: Record<Finding["severity"], string> = {
  error: "border-rose-400/30 bg-rose-400/10 text-rose-300",
  warning: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  info: "border-slate-400/30 bg-slate-400/10 text-slate-300",
};

const SEVERITY_ICON: Record<Finding["severity"], string> = {
  error: "🔴",
  warning: "🟡",
  info: "🔵",
};

const STATUS_STYLES: Record<Finding["resolution_status"], string> = {
  open: "border-rose-400/30 bg-rose-400/10 text-rose-300",
  resolved: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  accepted_as_risk: "border-slate-400/30 bg-slate-400/10 text-slate-300",
};

export function FindingsPanel({
  findings,
  sessionId,
  onChanged,
}: {
  findings: Finding[];
  sessionId: string;
  onChanged?: () => void;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (findings.length === 0) {
    return <p className="text-sm text-slate-500">No findings recorded yet.</p>;
  }

  async function handleSetStatus(f: Finding, status: "resolved" | "accepted_as_risk" | "open") {
    setBusyId(f.id);
    setError(null);
    try {
      await resolveFinding(sessionId, f.id, status);
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not update this finding.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      {error && (
        <p role="alert" className="mb-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-300">
          {error}
        </p>
      )}
      <ul className="space-y-2.5">
        {findings.map((f, i) => (
          <li
            key={f.id}
            className="card animate-fade-up transition-opacity"
            style={{ animationDelay: `${Math.min(i, 6) * 30}ms`, opacity: busyId === f.id ? 0.6 : 1 }}
          >
            <div className="flex items-center gap-2">
              <span className={`badge ${SEVERITY_STYLES[f.severity]}`}>
                {SEVERITY_ICON[f.severity]} {f.severity}
              </span>
              <span className="text-xs font-mono uppercase tracking-wide text-slate-500">
                {f.rule_id ?? "LLM critic"}
              </span>
              <span className={`badge ml-auto ${STATUS_STYLES[f.resolution_status]}`}>
                {f.resolution_status.replace(/_/g, " ")}
              </span>
            </div>
            <p className="mt-2.5 text-sm text-slate-200">{f.message}</p>
            {f.related_component_ids.length > 0 && (
              <p className="mt-1.5 text-xs text-slate-500">
                Related: <span className="font-mono text-slate-400">{f.related_component_ids.join(", ")}</span>
              </p>
            )}
            {(f.violated_requirement || f.suggested_fix || f.risk_if_ignored) && (
              <div className="mt-2.5 space-y-1.5 rounded-lg border border-white/10 bg-white/3 p-3 text-xs">
                {f.violated_requirement && (
                  <p>
                    <span className="font-semibold text-slate-300">Why this is flagged: </span>
                    <span className="text-slate-400">{f.violated_requirement}</span>
                  </p>
                )}
                {f.suggested_fix && (
                  <p>
                    <span className="font-semibold text-slate-300">Suggested fix: </span>
                    <span className="text-slate-400">{f.suggested_fix}</span>
                  </p>
                )}
                {f.risk_if_ignored && (
                  <p>
                    <span className="font-semibold text-slate-300">Risk if ignored: </span>
                    <span className="text-slate-400">{f.risk_if_ignored}</span>
                  </p>
                )}
              </div>
            )}
            <div className="mt-3 flex gap-2 border-t border-white/6 pt-3">
              {f.resolution_status !== "resolved" && (
                <button
                  type="button"
                  className="btn-secondary px-2.5! py-1! text-xs!"
                  disabled={busyId === f.id}
                  onClick={() => handleSetStatus(f, "resolved")}
                >
                  ✓ Mark resolved
                </button>
              )}
              {f.resolution_status !== "accepted_as_risk" && (
                <button
                  type="button"
                  className="btn-secondary px-2.5! py-1! text-xs!"
                  disabled={busyId === f.id}
                  onClick={() => handleSetStatus(f, "accepted_as_risk")}
                >
                  Accept as risk
                </button>
              )}
              {f.resolution_status !== "open" && (
                <button
                  type="button"
                  className="btn-secondary px-2.5! py-1! text-xs!"
                  disabled={busyId === f.id}
                  onClick={() => handleSetStatus(f, "open")}
                >
                  ↺ Reopen
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
