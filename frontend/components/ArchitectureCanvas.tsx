"use client";

import { useMemo, useState } from "react";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { modelLayoutPositions } from "@/lib/graphLayout";
import type { ArchitectureModel, Wave } from "@/lib/types";

export function ArchitectureCanvas({ model, waves }: { model: ArchitectureModel; waves?: Wave[] }) {
  const [showText, setShowText] = useState(true);
  const positions = useMemo(() => modelLayoutPositions(model, waves ?? null), [model, waves]);

  const nodes: Node[] = useMemo(
    () =>
      model.components.map((c) => ({
        id: c.id,
        position: positions.get(c.id) ?? { x: 0, y: 0 },
        data: { label: `${c.name}\n${c.workload_type}` },
        style: {
          fontSize: 12,
          whiteSpace: "pre-line" as const,
          border: "1px solid #cbd5e1",
          borderRadius: 8,
          padding: 8,
          background: "#ffffff",
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
        label: d.kind,
        animated: false,
        style: { stroke: "#64748b" },
        labelStyle: { fontSize: 10, fill: "#475569" },
      })),
    [model.dependencies]
  );

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-700">Current architecture (source)</h3>
          <p className="text-xs text-slate-400">
            What you described, as discovered. This is frozen once accepted at Gate 1 — it never becomes the
            target architecture. The migrated result appears separately, below, once planning has run.
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary shrink-0 text-xs"
          aria-pressed={!showText}
          onClick={() => setShowText((v) => !v)}
        >
          {showText ? "Show diagram" : "View as text"}
        </button>
      </div>

      {showText ? (
        <div className="card max-h-[520px] overflow-y-auto text-sm">
          <h4 className="font-medium text-slate-700">Architecture summary</h4>
          <p className="mt-1 text-xs text-slate-500">
            {model.components.length} components and {model.dependencies.length} dependencies were extracted.
          </p>

          <h5 className="mt-4 font-medium text-slate-700">Components</h5>
          <ul className="mt-1 space-y-3">
            {model.components.map((c) => (
              <li key={c.id}>
                <div className="font-medium text-slate-800">{c.name}</div>
                <div className="text-xs text-slate-500">
                  {c.workload_type.replace(/_/g, " ")} | {c.environment}
                  {c.technology ? ` | ${c.technology}` : ""}
                </div>
                {c.description && <p className="mt-1 text-slate-600">{c.description}</p>}
              </li>
            ))}
          </ul>

          <h5 className="mt-4 font-medium text-slate-700">Dependencies</h5>
          <ul className="mt-1 space-y-2">
            {model.dependencies.map((d) => (
              <li key={d.id}>
                <div className="text-slate-800">
                  {d.source_id} -&gt; {d.target_id} ({d.kind.replace(/_/g, " ")})
                </div>
                {d.description && <div className="text-xs text-slate-500">{d.description}</div>}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div style={{ height: 420 }} className="card p-0" role="img" aria-label="Architecture dependency diagram">
          {model.components.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-slate-400">
              Describe your system in the chat to start building the model.
            </div>
          ) : (
            <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
              <Background />
              <Controls />
            </ReactFlow>
          )}
        </div>
      )}
    </div>
  );
}
