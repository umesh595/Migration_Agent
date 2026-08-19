"use client";

import { useMemo, useState } from "react";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { layoutPositions } from "@/lib/graphLayout";
import type { ArchitectureModel, MigrationPlan, SevenR } from "@/lib/types";

/** Distinct per-disposition styling so the diagram itself communicates what's
 * happening to each component at a glance — retiring looks retired, a like-for-like
 * rehost looks stable, a refactor looks like it's genuinely being rebuilt. */
const DISPOSITION_STYLE: Record<SevenR, { bg: string; border: string; text: string; dashed?: boolean }> = {
  retire: { bg: "#fef2f2", border: "#fca5a5", text: "#991b1b", dashed: true },
  retain: { bg: "#f8fafc", border: "#cbd5e1", text: "#475569" },
  rehost: { bg: "#eff6ff", border: "#93c5fd", text: "#1e40af" },
  replatform: { bg: "#eef2ff", border: "#a5b4fc", text: "#3730a3" },
  refactor: { bg: "#faf5ff", border: "#d8b4fe", text: "#6b21a8" },
  repurchase: { bg: "#f0fdfa", border: "#5eead4", text: "#115e59" },
  relocate: { bg: "#fffbeb", border: "#fcd34d", text: "#92400e" },
};

const DISPOSITION_LABEL: Record<SevenR, string> = {
  retire: "Retire",
  retain: "Retain as-is",
  rehost: "Rehost (lift & shift)",
  replatform: "Replatform",
  refactor: "Refactor",
  repurchase: "Repurchase (SaaS)",
  relocate: "Relocate",
};

/** The migrated architecture, rendered as an actual diagram — not just a
 * paragraph. Node labels come from the LLM-authored target_description
 * (per-component decision, already validated by RULE-002/006), positioned by the
 * code-computed wave order — this component decides nothing, it only renders
 * what planning already produced (technique #12). */
export function TargetArchitectureCanvas({ model, plan }: { model: ArchitectureModel; plan: MigrationPlan }) {
  const [showText, setShowText] = useState(false);

  const mappingById = useMemo(
    () => new Map(plan.component_mappings.map((m) => [m.component_id, m])),
    [plan.component_mappings]
  );

  const positions = useMemo(
    () => layoutPositions(model.components.map((c) => c.id), model.dependencies, plan.waves),
    [model.components, model.dependencies, plan.waves]
  );

  const nodes: Node[] = useMemo(
    () =>
      model.components.map((c) => {
        const mapping = mappingById.get(c.id);
        const style = mapping ? DISPOSITION_STYLE[mapping.disposition] : DISPOSITION_STYLE.retain;
        const truncatedTarget = mapping && mapping.target_description.length > 90
          ? `${mapping.target_description.slice(0, 87)}...`
          : mapping?.target_description;
        return {
          id: c.id,
          position: positions.get(c.id) ?? { x: 0, y: 0 },
          data: { label: `${c.name}\n${truncatedTarget ?? "(no target decision recorded)"}` },
          style: {
            fontSize: 11,
            whiteSpace: "pre-line" as const,
            border: `1.5px ${style.dashed ? "dashed" : "solid"} ${style.border}`,
            borderRadius: 8,
            padding: 8,
            width: 220,
            background: style.bg,
            color: style.text,
          },
        };
      }),
    [model.components, mappingById, positions]
  );

  const edges: Edge[] = useMemo(
    () =>
      model.dependencies.map((d) => ({
        id: d.id,
        source: d.source_id,
        target: d.target_id,
        label: d.kind,
        animated: true,
        style: { stroke: "#16a34a" },
        labelStyle: { fontSize: 10, fill: "#166534" },
      })),
    [model.dependencies]
  );

  const dispositionCounts = useMemo(() => {
    const counts = new Map<SevenR, number>();
    for (const m of plan.component_mappings) {
      counts.set(m.disposition, (counts.get(m.disposition) ?? 0) + 1);
    }
    return counts;
  }, [plan.component_mappings]);

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-700">Target architecture (migrated)</h3>
          <p className="text-xs text-slate-400">
            What the system becomes — computed sequencing, per-component target decisions. Compare against
            "Current architecture" above to see exactly what changed.
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary shrink-0 text-xs"
          aria-pressed={showText}
          onClick={() => setShowText((v) => !v)}
        >
          {showText ? "Show diagram" : "View as text"}
        </button>
      </div>

      <div className="mb-2 flex flex-wrap gap-2">
        {[...dispositionCounts.entries()].map(([disposition, count]) => (
          <span
            key={disposition}
            className="badge"
            style={{
              background: DISPOSITION_STYLE[disposition].bg,
              color: DISPOSITION_STYLE[disposition].text,
              border: `1px solid ${DISPOSITION_STYLE[disposition].border}`,
            }}
          >
            {DISPOSITION_LABEL[disposition]}: {count}
          </span>
        ))}
      </div>

      {showText ? (
        <div className="card max-h-[520px] overflow-y-auto text-sm">
          <ul className="space-y-3">
            {plan.component_mappings.map((m) => {
              const component = model.components.find((c) => c.id === m.component_id);
              return (
                <li key={m.component_id}>
                  <div className="flex items-center gap-2">
                    <span
                      className="badge"
                      style={{
                        background: DISPOSITION_STYLE[m.disposition].bg,
                        color: DISPOSITION_STYLE[m.disposition].text,
                      }}
                    >
                      {DISPOSITION_LABEL[m.disposition]}
                    </span>
                    <span className="font-medium text-slate-800">{component?.name ?? m.component_id}</span>
                  </div>
                  <p className="mt-1 text-slate-600">{m.target_description}</p>
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <div style={{ height: 460 }} className="card p-0" role="img" aria-label="Target migrated architecture diagram">
          <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
            <Background />
            <Controls />
          </ReactFlow>
        </div>
      )}
    </div>
  );
}
