"use client";

import { useState } from "react";

import { ApiError, resolveFinding } from "@/lib/api";
import type { Finding } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const SEVERITY_VARIANT: Record<Finding["severity"], "coral" | "amber" | "secondary"> = {
  error: "coral",
  warning: "amber",
  info: "secondary",
};

const SEVERITY_ICON: Record<Finding["severity"], string> = {
  error: "🔴",
  warning: "🟡",
  info: "🔵",
};

const SEVERITY_BAR: Record<Finding["severity"], string> = {
  error: "bg-atlas-coral",
  warning: "bg-atlas-amber",
  info: "bg-atlas-sky",
};

const STATUS_VARIANT: Record<Finding["resolution_status"], "coral" | "teal" | "secondary"> = {
  open: "coral",
  resolved: "teal",
  accepted_as_risk: "secondary",
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
    return <p className="text-sm text-muted-foreground">No findings recorded yet.</p>;
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
        <p
          role="alert"
          className="mb-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-1.5 text-xs text-destructive"
        >
          {error}
        </p>
      )}
      <ul className="space-y-2.5">
        {findings.map((f, i) => (
          <li
            key={f.id}
            className="rail-card blueprint-reveal transition-opacity"
            style={{ animationDelay: `${Math.min(i, 6) * 30}ms`, opacity: busyId === f.id ? 0.6 : 1 }}
          >
            <div className={cn("rail-card-bar", SEVERITY_BAR[f.severity])} />
            <div className="rail-card-body">
              <div className="flex items-center gap-2">
                <Badge variant={SEVERITY_VARIANT[f.severity]}>
                  {SEVERITY_ICON[f.severity]} {f.severity}
                </Badge>
                <span className="font-mono text-xs uppercase tracking-wide text-muted-foreground">
                  {f.rule_id ?? "LLM critic"}
                </span>
                <Badge variant={STATUS_VARIANT[f.resolution_status]} className="ml-auto">
                  {f.resolution_status.replace(/_/g, " ")}
                </Badge>
              </div>
              <p className="mt-2.5 text-sm text-foreground">{f.message}</p>
              {f.related_component_ids.length > 0 && (
                <p className="mt-1.5 text-xs text-muted-foreground">
                  Related: <span className="font-mono text-muted-foreground/80">{f.related_component_ids.join(", ")}</span>
                </p>
              )}
              {(f.violated_requirement || f.suggested_fix || f.risk_if_ignored) && (
                <div className="mt-2.5 space-y-1.5 rounded-lg border border-border bg-background/50 p-3 text-xs">
                  {f.violated_requirement && (
                    <p>
                      <span className="font-semibold text-foreground">Why this is flagged: </span>
                      <span className="text-muted-foreground">{f.violated_requirement}</span>
                    </p>
                  )}
                  {f.suggested_fix && (
                    <p>
                      <span className="font-semibold text-foreground">Suggested fix: </span>
                      <span className="text-muted-foreground">{f.suggested_fix}</span>
                    </p>
                  )}
                  {f.risk_if_ignored && (
                    <p>
                      <span className="font-semibold text-foreground">Risk if ignored: </span>
                      <span className="text-muted-foreground">{f.risk_if_ignored}</span>
                    </p>
                  )}
                </div>
              )}
              <div className="mt-3 flex gap-2 border-t border-border pt-3">
                {f.resolution_status !== "resolved" && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="text-xs"
                    disabled={busyId === f.id}
                    onClick={() => handleSetStatus(f, "resolved")}
                  >
                    ✓ Mark resolved
                  </Button>
                )}
                {f.resolution_status !== "accepted_as_risk" && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="text-xs"
                    disabled={busyId === f.id}
                    onClick={() => handleSetStatus(f, "accepted_as_risk")}
                  >
                    Accept as risk
                  </Button>
                )}
                {f.resolution_status !== "open" && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="text-xs"
                    disabled={busyId === f.id}
                    onClick={() => handleSetStatus(f, "open")}
                  >
                    ↺ Reopen
                  </Button>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
