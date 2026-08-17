"use client";

import { useMemo, useState } from "react";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { ArchitectureModel, Wave } from "@/lib/types";

const COLUMN_WIDTH = 240;
const ROW_HEIGHT = 90;

/** Presentation-only layout. When a plan exists, this mirrors backend-computed
 * waves; before that it falls back to readable client-side layering. */
function layoutPositions(model: ArchitectureModel, waves: Wave[] | null): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();

  if (waves && waves.length > 0) {
    for (const wave of waves) {
      wave.component_ids.forEach((id, row) => {
        positions.set(id, { x: wave.index * COLUMN_WIDTH, y: row * ROW_HEIGHT });
      });
    }
    return positions;
  }

  const ids = model.components.map((c) => c.id);
  const incoming = new Map<string, Set<string>>(ids.map((id) => [id, new Set()]));
  for (const dep of model.dependencies) {
    if (incoming.has(dep.source_id) && incoming.has(dep.target_id)) {
      incoming.get(dep.source_id)!.add(dep.target_id);
    }
  }

  const layerOf = new Map<string, number>();
  const remaining = new Set(ids);
  let layer = 0;
  while (remaining.size > 0 && layer < ids.length + 1) {
    const ready = [...remaining].filter((id) => [...incoming.get(id)!].every((dep) => !remaining.has(dep)));
    const resolved = ready.length > 0 ? ready : [...remaining];
    for (const id of resolved) {
      layerOf.set(id, layer);
      remaining.delete(id);
    }
    layer += 1;
  }

  const countPerLayer = new Map<number, number>();
  for (const id of ids) {
    const l = layerOf.get(id) ?? 0;
    const row = countPerLayer.get(l) ?? 0;
    positions.set(id, { x: l * COLUMN_WIDTH, y: row * ROW_HEIGHT });
    countPerLayer.set(l, row + 1);
  }
  return positions;
}

export function ArchitectureCanvas({ model, waves }: { model: ArchitectureModel; waves?: Wave[] }) {
  const [showText, setShowText] = useState(true);
  const positions = useMemo(() => layoutPositions(model, waves ?? null), [model, waves]);

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
        <h3 className="text-sm font-semibold text-slate-700">Architecture output</h3>
        <button
          type="button"
          className="btn-secondary text-xs"
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
