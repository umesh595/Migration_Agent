"use client";

import { useMemo, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { ArchitectureModel, Wave } from "@/lib/types";

const COLUMN_WIDTH = 240;
const ROW_HEIGHT = 90;

/** Presentation-only layout — never a decision authority. When a plan exists, this
 * mirrors the backend-computed wave order (waves[] came from GraphEngine's
 * topological sort) so the canvas visually reinforces the real sequencing. Before
 * a plan exists, it falls back to a simple client-side layering purely so the graph
 * reads left-to-right instead of overlapping at the origin — it does not decide or
 * influence any migration order (see Doc 3 §3.3 / DECISIONS.md). */
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

  // Fallback: Kahn's-algorithm-style layering for readability only.
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
    const resolved = ready.length > 0 ? ready : [...remaining]; // break any cycle rather than looping forever
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
  const [showText, setShowText] = useState(false);
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
        <h3 className="text-sm font-semibold text-slate-700">Architecture canvas</h3>
        <button
          type="button"
          className="btn-secondary text-xs"
          aria-pressed={showText}
          onClick={() => setShowText((v) => !v)}
        >
          {showText ? "Show diagram" : "View as text"}
        </button>
      </div>

      {showText ? (
        <div className="card text-sm">
          <h4 className="font-medium text-slate-700">Components ({model.components.length})</h4>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {model.components.map((c) => (
              <li key={c.id}>
                <span className="font-medium">{c.name}</span> ({c.workload_type}, {c.environment})
                {c.technology ? ` — ${c.technology}` : ""}
              </li>
            ))}
          </ul>
          <h4 className="mt-3 font-medium text-slate-700">Dependencies ({model.dependencies.length})</h4>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {model.dependencies.map((d) => (
              <li key={d.id}>
                {d.source_id} → {d.target_id} ({d.kind})
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
