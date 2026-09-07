"use client";

import { useMemo, useState } from "react";
import { MarkerType, type Edge, type Node } from "@xyflow/react";

import { ApiError, getComponentImpact } from "@/lib/api";
import { DiagramFrame } from "@/components/DiagramFrame";
import { modelLayoutPositions } from "@/lib/graphLayout";
import type { ArchitectureModel, Wave, WorkloadType } from "@/lib/types";

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
          border: "1px solid rgba(129,140,248,0.4)",
          borderRadius: 12,
          padding: "12px 14px",
          width: 190,
          background: "linear-gradient(160deg, rgba(99,102,241,0.16), rgba(17,19,39,0.94))",
          color: "#e2e8f0",
          boxShadow: "0 4px 16px -6px rgba(0,0,0,0.5)",
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
        style: { stroke: "#818cf8", strokeWidth: 1.5, opacity: 0.85 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#818cf8", width: 16, height: 16 },
        labelStyle: { fontSize: 10, fill: "#c7d2fe" },
        labelBgStyle: { fill: "#12142a" },
      })),
    [model.dependencies]
  );

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
            <span className="text-base">🏛️</span> Current architecture (source)
          </h3>
          <p className="mt-0.5 text-xs text-slate-500">
            What you described, as discovered. Frozen once accepted at Gate 1.
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary shrink-0 px-2.5! py-1.5! text-xs!"
          aria-pressed={!showText}
          onClick={() => setShowText((v) => !v)}
        >
          {showText ? "🖼️ Diagram" : "📋 Text"}
        </button>
      </div>

      {showText ? (
        <div className="card max-h-[520px] overflow-y-auto text-sm">
          <div className="flex items-center gap-2">
            <h4 className="font-medium text-slate-200">Architecture summary</h4>
            <span className="badge border-brand-400/30 bg-brand-400/10 text-brand-300">
              {model.components.length} components
            </span>
            <span className="badge border-white/10 bg-white/4 text-slate-400">
              {model.dependencies.length} dependencies
            </span>
          </div>

          <h5 className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">Components</h5>
          <ul className="mt-2 space-y-3">
            {model.components.map((c) => (
              <li key={c.id} className="rounded-xl border border-white/6 bg-white/2 p-3">
                <div className="flex items-center gap-2">
                  <span className="text-base">{WORKLOAD_ICON[c.workload_type] ?? "🔷"}</span>
                  <span className="font-medium text-slate-100">{c.name}</span>
                  <button
                    type="button"
                    className="ml-auto text-xs font-medium text-brand-300 hover:text-brand-200"
                    onClick={() => handleShowImpact(c.id)}
                  >
                    {impactFor === c.id ? "Hide impact" : "What depends on this? →"}
                  </button>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-2 text-xs text-slate-500">
                  <span>{c.workload_type.replace(/_/g, " ")}</span>
                  <span className="text-slate-600">·</span>
                  <span>{c.environment}</span>
                  {c.technology && (
                    <>
                      <span className="text-slate-600">·</span>
                      <span>{c.technology}</span>
                    </>
                  )}
                  {c.owner_team && (
                    <>
                      <span className="text-slate-600">·</span>
                      <span>owner: {c.owner_team}</span>
                    </>
                  )}
                </div>
                {c.description && <p className="mt-1.5 text-slate-400">{c.description}</p>}
                {impactFor === c.id && (
                  <div className="mt-2 rounded-lg border border-brand-400/20 bg-brand-400/6 p-2.5 text-xs animate-pop-in">
                    {impactError ? (
                      <p className="text-rose-300">{impactError}</p>
                    ) : impact ? (
                      <>
                        <p>
                          <span className="font-medium text-slate-300">↑ Upstream (depends on this):</span>{" "}
                          <span className="text-slate-400">{impact.upstream.length > 0 ? impact.upstream.join(", ") : "none"}</span>
                        </p>
                        <p className="mt-1">
                          <span className="font-medium text-slate-300">↓ Downstream (this depends on):</span>{" "}
                          <span className="text-slate-400">{impact.downstream.length > 0 ? impact.downstream.join(", ") : "none"}</span>
                        </p>
                      </>
                    ) : (
                      <p className="text-slate-500">Computing…</p>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>

          <h5 className="mt-5 text-xs font-semibold uppercase tracking-wide text-slate-500">Dependencies</h5>
          <ul className="mt-2 grid gap-2">
            {model.dependencies.map((d) => (
              <li key={d.id} className="rounded-lg border border-white/6 bg-white/2 p-2.5">
                <div className="grid gap-2 text-xs sm:grid-cols-[1fr_auto_1fr] sm:items-center">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-200">
                      {componentById.get(d.source_id)?.name ?? d.source_id}
                    </p>
                    <p className="truncate font-mono text-[11px] text-slate-600">{d.source_id}</p>
                  </div>
                  <div className="flex items-center gap-2 sm:justify-center">
                    <span className="text-brand-300">to</span>
                    <span className="badge border-white/10 bg-white/3 text-slate-400">
                      {d.kind.replace(/_/g, " ")}
                    </span>
                  </div>
                  <div className="min-w-0 sm:text-right">
                    <p className="truncate font-medium text-slate-200">
                      {componentById.get(d.target_id)?.name ?? d.target_id}
                    </p>
                    <p className="truncate font-mono text-[11px] text-slate-600">{d.target_id}</p>
                  </div>
                </div>
                {d.description && (
                  <p className="mt-2 border-t border-white/5 pt-2 text-xs leading-relaxed text-slate-500">
                    {d.description}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : model.components.length === 0 ? (
        <div style={{ height: 420 }} className="card flex flex-col items-center justify-center gap-2 text-center text-sm text-slate-500">
          <span className="text-2xl opacity-50">🏗️</span>
          Describe your system in the chat to start building the model.
        </div>
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
