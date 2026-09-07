"use client";

import { useMemo, useState } from "react";
import { MarkerType, type Edge, type Node } from "@xyflow/react";

import { DiagramFrame } from "@/components/DiagramFrame";
import { layoutPositions } from "@/lib/graphLayout";
import type { ArchitectureModel, MigrationPlan, SevenR } from "@/lib/types";

/** Distinct per-disposition styling so the diagram itself communicates what's
 * happening to each component at a glance — retiring looks retired, a like-for-like
 * rehost looks stable, a refactor looks like it's genuinely being rebuilt. Dark-mode
 * native: translucent tinted backgrounds over the canvas rather than opaque pastels. */
const DISPOSITION_STYLE: Record<SevenR, { bg: string; border: string; text: string; dashed?: boolean }> = {
  retire: { bg: "rgba(244,63,94,0.12)", border: "rgba(251,113,133,0.5)", text: "#fda4af", dashed: true },
  retain: { bg: "rgba(148,163,184,0.10)", border: "rgba(203,213,225,0.35)", text: "#cbd5e1" },
  rehost: { bg: "rgba(56,189,248,0.12)", border: "rgba(125,211,252,0.5)", text: "#7dd3fc" },
  replatform: { bg: "rgba(99,102,241,0.14)", border: "rgba(165,180,252,0.5)", text: "#a5b4fc" },
  refactor: { bg: "rgba(192,132,252,0.14)", border: "rgba(216,180,254,0.5)", text: "#e9d5ff" },
  repurchase: { bg: "rgba(45,212,191,0.12)", border: "rgba(94,234,212,0.5)", text: "#5eead4" },
  relocate: { bg: "rgba(251,191,36,0.12)", border: "rgba(252,211,77,0.5)", text: "#fcd34d" },
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
            fontFamily: "var(--font-body)",
            whiteSpace: "pre-line" as const,
            border: `1.5px ${style.dashed ? "dashed" : "solid"} ${style.border}`,
            borderRadius: 12,
            padding: "10px 12px",
            width: 220,
            background: style.bg,
            color: style.text,
            boxShadow: "0 4px 16px -6px rgba(0,0,0,0.5)",
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
        label: d.kind.replace(/_/g, " "),
        type: "smoothstep",
        style: { stroke: "#34d399", strokeWidth: 1.5, opacity: 0.85 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#34d399", width: 16, height: 16 },
        labelStyle: { fontSize: 10, fill: "#6ee7b7" },
        labelBgStyle: { fill: "#0d1f1a" },
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
      <div className="mb-2 flex items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
            <span className="text-base">🎯</span> Target architecture (migrated)
          </h3>
          <p className="mt-0.5 text-xs text-slate-500">
            What the system becomes — computed sequencing, per-component target decisions.
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary shrink-0 !px-2.5 !py-1.5 !text-xs"
          aria-pressed={showText}
          onClick={() => setShowText((v) => !v)}
        >
          {showText ? "📋 Text" : "🖼️ Diagram"}
        </button>
      </div>

      <div className="mb-3 flex flex-wrap gap-1.5">
        {[...dispositionCounts.entries()].map(([disposition, count]) => (
          <span
            key={disposition}
            className="badge"
            style={{
              background: DISPOSITION_STYLE[disposition].bg,
              color: DISPOSITION_STYLE[disposition].text,
              borderColor: DISPOSITION_STYLE[disposition].border,
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
                <li key={m.component_id} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                  <div className="flex items-center gap-2">
                    <span
                      className="badge"
                      style={{
                        background: DISPOSITION_STYLE[m.disposition].bg,
                        color: DISPOSITION_STYLE[m.disposition].text,
                        borderColor: DISPOSITION_STYLE[m.disposition].border,
                      }}
                    >
                      {DISPOSITION_LABEL[m.disposition]}
                    </span>
                    <span className="font-medium text-slate-100">{component?.name ?? m.component_id}</span>
                  </div>
                  <p className="mt-1.5 text-slate-400">{m.target_description}</p>
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <DiagramFrame
          nodes={nodes}
          edges={edges}
          height={600}
          title="Target architecture (migrated)"
          ariaLabel="Target migrated architecture diagram"
        />
      )}
    </div>
  );
}
