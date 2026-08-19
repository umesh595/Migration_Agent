import type { ArchitectureModel, Wave } from "./types";

export const COLUMN_WIDTH = 240;
export const ROW_HEIGHT = 90;

/** Presentation-only layout — never a decision authority. When waves exist, this
 * mirrors the backend-computed wave order (GraphEngine's topological sort) so the
 * canvas visually reinforces the real sequencing. Before that, it falls back to a
 * simple client-side layering purely so the graph reads left-to-right — it does
 * not decide or influence any migration order (see Doc 3 §3.3 / DECISIONS.md). */
export function layoutPositions(
  ids: string[],
  dependencies: { source_id: string; target_id: string }[],
  waves: Wave[] | null
): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();

  if (waves && waves.length > 0) {
    for (const wave of waves) {
      wave.component_ids.forEach((id, row) => {
        positions.set(id, { x: wave.index * COLUMN_WIDTH, y: row * ROW_HEIGHT });
      });
    }
    return positions;
  }

  const incoming = new Map<string, Set<string>>(ids.map((id) => [id, new Set()]));
  for (const dep of dependencies) {
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

export function modelLayoutPositions(model: ArchitectureModel, waves: Wave[] | null) {
  return layoutPositions(
    model.components.map((c) => c.id),
    model.dependencies,
    waves
  );
}
