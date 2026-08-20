"use client";

import { useState } from "react";

import { ApiError, resolveFinding } from "@/lib/api";
import type { Finding } from "@/lib/types";

const SEVERITY_STYLES: Record<Finding["severity"], string> = {
  error: "bg-red-100 text-red-800",
  warning: "bg-amber-100 text-amber-800",
  info: "bg-slate-100 text-slate-700",
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
    return <p className="text-sm text-slate-400">No findings recorded yet.</p>;
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
        <p role="alert" className="mb-2 text-xs text-red-600">
          {error}
        </p>
      )}
      <ul className="space-y-2">
        {findings.map((f) => (
          <li key={f.id} className="card">
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
            <div className="mt-2 flex gap-2">
              {f.resolution_status !== "resolved" && (
                <button
                  type="button"
                  className="btn-secondary text-xs"
                  disabled={busyId === f.id}
                  onClick={() => handleSetStatus(f, "resolved")}
                >
                  Mark resolved
                </button>
              )}
              {f.resolution_status !== "accepted_as_risk" && (
                <button
                  type="button"
                  className="btn-secondary text-xs"
                  disabled={busyId === f.id}
                  onClick={() => handleSetStatus(f, "accepted_as_risk")}
                >
                  Accept as risk
                </button>
              )}
              {f.resolution_status !== "open" && (
                <button
                  type="button"
                  className="btn-secondary text-xs"
                  disabled={busyId === f.id}
                  onClick={() => handleSetStatus(f, "open")}
                >
                  Reopen
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
