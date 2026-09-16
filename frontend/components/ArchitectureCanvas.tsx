"use client";

import { useMemo, useState } from "react";
import { MarkerType, type Edge, type Node } from "@xyflow/react";

import { ApiError, getComponentImpact } from "@/lib/api";
import { DiagramFrame } from "@/components/DiagramFrame";
import { modelLayoutPositions } from "@/lib/graphLayout";
import type { ArchitectureModel, Wave, WorkloadType } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const WORKLOAD_ICON: Record<WorkloadType, string> = {
  web_service: "🌐",
  api_service: "🔌",
  batch_job: "⏱️",
  database: "🗄️",
  message_queue: "📮",
  cache: "⚡",
  ml_inference: "🧠",
  ml_training: "🏋️",
  data_pipeline: "🔀",
  data_warehouse: "🏭",
  storage: "📦",
  load_balancer: "🧭",
  cdn: "🛰️",
  third_party_integration: "🔗",
  other: "🔷",
};

export function ArchitectureCanvas({
  model,
  waves,
  sessionId,
}: {
  model: ArchitectureModel;
  waves?: Wave[];
  sessionId: string;
}) {
  const [showText, setShowText] = useState(true);
  const [impactFor, setImpactFor] = useState<string | null>(null);
  const [impact, setImpact] = useState<{ upstream: string[]; downstream: string[] } | null>(null);
  const [impactError, setImpactError] = useState<string | null>(null);
  const positions = useMemo(() => modelLayoutPositions(model, waves ?? null), [model, waves]);
  const componentById = useMemo(() => new Map(model.components.map((c) => [c.id, c])), [model.components]);

  async function handleShowImpact(componentId: string) {
    if (impactFor === componentId) {
      setImpactFor(null);
      setImpact(null);
      return;
    }
    setImpactFor(componentId);
    setImpact(null);
    setImpactError(null);
    try {
      setImpact(await getComponentImpact(sessionId, componentId));
    } catch (err) {
      setImpactError(err instanceof ApiError ? err.detail : "Could not compute impact.");
    }
  }

  const nodes: Node[] = useMemo(
    () =>
      model.components.map((c) => ({
        id: c.id,
        position: positions.get(c.id) ?? { x: 0, y: 0 },
        data: { label: `${WORKLOAD_ICON[c.workload_type] ?? "🔷"}  ${c.name}\n${c.workload_type.replace(/_/g, " ")}` },
        style: {
          fontSize: 12.5,
          fontFamily: "var(--font-body)",
          whiteSpace: "pre-line" as const,
          border: "1px solid var(--teal)",
          borderRadius: 12,
          padding: "12px 14px",
          width: 190,
          background: "color-mix(in oklch, var(--teal) 12%, var(--card))",
          color: "var(--card-foreground)",
          boxShadow: "0 4px 16px -6px rgba(0,0,0,0.35)",
        },
      })),
    [model.components, positions]
  );

  const edges: Edge[] = useMemo(
    () =>
      model.dependencies.map((d) => ({
        id: d.id,
        source: d.source_id,
        target: d.target_id,
        label: d.kind.replace(/_/g, " "),
        type: "smoothstep",
        style: { stroke: "var(--teal)", strokeWidth: 1.5, opacity: 0.85 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "var(--teal)", width: 16, height: 16 },
        labelStyle: { fontSize: 10, fill: "var(--teal)" },
        labelBgStyle: { fill: "var(--card)" },
      })),
    [model.dependencies]
  );

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <span className="text-base">🏛️</span> Current architecture (source)
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            What you described, as discovered. Frozen once accepted at Gate 1.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0 text-xs"
          aria-pressed={!showText}
          onClick={() => setShowText((v) => !v)}
        >
          {showText ? "🖼️ Diagram" : "📋 Text"}
        </Button>
      </div>

      {showText ? (
        <Card className="max-h-[520px] overflow-y-auto p-4 text-sm">
          <div className="flex items-center gap-2">
            <h4 className="font-medium text-foreground">Architecture summary</h4>
            <Badge variant="teal">{model.components.length} components</Badge>
            <Badge variant="secondary">{model.dependencies.length} dependencies</Badge>
          </div>

          <h5 className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Components</h5>
          <ul className="mt-2 space-y-3">
            {model.components.map((c) => (
              <li key={c.id} className="rounded-xl border border-border bg-background/50 p-3">
                <div className="flex items-center gap-2">
                  <span className="text-base">{WORKLOAD_ICON[c.workload_type] ?? "🔷"}</span>
                  <span className="font-medium text-foreground">{c.name}</span>
                  <button
                    type="button"
                    className="ml-auto text-xs font-medium text-atlas-teal hover:text-atlas-teal/80"
                    onClick={() => handleShowImpact(c.id)}
                  >
                    {impactFor === c.id ? "Hide impact" : "What depends on this? →"}
                  </button>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-2 text-xs text-muted-foreground">
                  <span>{c.workload_type.replace(/_/g, " ")}</span>
                  <span className="text-muted-foreground/50">·</span>
                  <span>{c.environment}</span>
                  {c.technology && (
                    <>
                      <span className="text-muted-foreground/50">·</span>
                      <span>{c.technology}</span>
                    </>
                  )}
                  {c.owner_team && (
                    <>
                      <span className="text-muted-foreground/50">·</span>
                      <span>owner: {c.owner_team}</span>
                    </>
                  )}
                </div>
                {c.description && <p className="mt-1.5 text-muted-foreground">{c.description}</p>}
                {impactFor === c.id && (
                  <div className="mt-2 rounded-lg border border-atlas-teal/20 bg-atlas-teal-soft p-2.5 text-xs blueprint-reveal">
                    {impactError ? (
                      <p className="text-destructive">{impactError}</p>
                    ) : impact ? (
                      <>
                        <p>
                          <span className="font-medium text-foreground">↑ Upstream (depends on this):</span>{" "}
                          <span className="text-muted-foreground">
                            {impact.upstream.length > 0 ? impact.upstream.join(", ") : "none"}
                          </span>
                        </p>
                        <p className="mt-1">
                          <span className="font-medium text-foreground">↓ Downstream (this depends on):</span>{" "}
                          <span className="text-muted-foreground">
                            {impact.downstream.length > 0 ? impact.downstream.join(", ") : "none"}
                          </span>
                        </p>
                      </>
                    ) : (
                      <p className="text-muted-foreground">Computing…</p>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>

          <h5 className="mt-5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Dependencies</h5>
          <ul className="mt-2 grid gap-2">
            {model.dependencies.map((d) => (
              <li key={d.id} className="rounded-lg border border-border bg-background/50 p-2.5">
                <div className="grid gap-2 text-xs sm:grid-cols-[1fr_auto_1fr] sm:items-center">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-foreground">
                      {componentById.get(d.source_id)?.name ?? d.source_id}
                    </p>
                    <p className="truncate font-mono text-[11px] text-muted-foreground/70">{d.source_id}</p>
                  </div>
                  <div className="flex items-center gap-2 sm:justify-center">
                    <span className="text-atlas-teal">to</span>
                    <Badge variant="secondary">{d.kind.replace(/_/g, " ")}</Badge>
                  </div>
                  <div className="min-w-0 sm:text-right">
                    <p className="truncate font-medium text-foreground">
                      {componentById.get(d.target_id)?.name ?? d.target_id}
                    </p>
                    <p className="truncate font-mono text-[11px] text-muted-foreground/70">{d.target_id}</p>
                  </div>
                </div>
                {d.description && (
                  <p className="mt-2 border-t border-border pt-2 text-xs leading-relaxed text-muted-foreground">
                    {d.description}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Card>
      ) : model.components.length === 0 ? (
        <Card
          style={{ height: 420 }}
          className="flex flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground"
        >
          <span className="text-2xl opacity-50">🏗️</span>
          Describe your system in the chat to start building the model.
        </Card>
      ) : (
        <DiagramFrame
          nodes={nodes}
          edges={edges}
          height={600}
          title="Current architecture (source)"
          ariaLabel="Architecture dependency diagram"
        />
      )}
    </div>
  );
}
