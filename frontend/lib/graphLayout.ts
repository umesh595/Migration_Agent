import type { ArchitectureModel, Wave } from "./types";

export const COLUMN_WIDTH = 280;
export const ROW_HEIGHT = 130;

type DependencyEdge = { source_id: string; target_id: string };

/** Dependency-order layering: a component with no unresolved dependencies is
 * ready to be placed as soon as everything it depends on has a layer. This
 * mirrors the backend-computed wave order (things nothing else depends on move
 * first) so the fallback (pre-planning) view and the post-planning wave view
 * read consistently left-to-right — presentation-only, never a decision
 * authority (see Doc 3 §3.3 / DECISIONS.md). */
function computeLayers(ids: string[], dependencies: DependencyEdge[]): string[][] {
  const dependsOn = new Map<string, Set<string>>(ids.map((id) => [id, new Set()]));
  for (const dep of dependencies) {
    if (dependsOn.has(dep.source_id) && dependsOn.has(dep.target_id)) {
      dependsOn.get(dep.source_id)!.add(dep.target_id);
    }
  }

  const layerOf = new Map<string, number>();
  const remaining = new Set(ids);
  let layer = 0;
  while (remaining.size > 0 && layer < ids.length + 1) {
    const ready = [...remaining].filter((id) => [...dependsOn.get(id)!].every((dep) => !remaining.has(dep)));
    const resolved = ready.length > 0 ? ready : [...remaining];
    for (const id of resolved) {
      layerOf.set(id, layer);
      remaining.delete(id);
    }
    layer += 1;
  }

  const layers: string[][] = [];
  for (const id of ids) {
    const l = layerOf.get(id) ?? 0;
    layers[l] = layers[l] ?? [];
    layers[l].push(id);
  }
  return layers;
}

/** Simplified barycenter crossing-reduction (Sugiyama-style layered-graph
 * drawing, one forward + one backward sweep): within each layer, reorder nodes
 * by the average position of their neighbors in the adjacent layer. This is
 * what turns "edges swooping across the whole canvas because two connected
 * nodes landed on opposite ends of their rows" into a diagram where connected
 * nodes tend to line up — a presentation nicety, not a layout guarantee, but
 * enough at the component counts this canvas actually renders (a handful to a
 * few dozen) to make the difference between readable and spaghetti. */
function reduceCrossings(layers: string[][], dependencies: DependencyEdge[]): string[][] {
  const neighbors = new Map<string, string[]>();
  for (const layer of layers) for (const id of layer) neighbors.set(id, []);
  for (const dep of dependencies) {
    if (!neighbors.has(dep.source_id) || !neighbors.has(dep.target_id)) continue;
    neighbors.get(dep.source_id)!.push(dep.target_id);
    neighbors.get(dep.target_id)!.push(dep.source_id);
  }

  const ordered = layers.map((l) => [...l]);

  function sweep(step: 1 | -1) {
    const start = step === 1 ? 0 : ordered.length - 1;
    for (let l = start; l >= 0 && l < ordered.length; l += step) {
      const adjacent = ordered[l - step];
      if (!adjacent) continue;
      const positionInAdjacent = new Map(adjacent.map((id, idx) => [id, idx]));
      const currentLayer = ordered[l]!;
      const currentIndex = new Map(currentLayer.map((id, idx) => [id, idx]));
      ordered[l] = [...currentLayer].sort((a, b) => {
        const aPositions = neighbors.get(a)!.map((n) => positionInAdjacent.get(n)).filter((v): v is number => v !== undefined);
        const bPositions = neighbors.get(b)!.map((n) => positionInAdjacent.get(n)).filter((v): v is number => v !== undefined);
        const aScore = aPositions.length ? aPositions.reduce((s, v) => s + v, 0) / aPositions.length : currentIndex.get(a)!;
        const bScore = bPositions.length ? bPositions.reduce((s, v) => s + v, 0) / bPositions.length : currentIndex.get(b)!;
        return aScore - bScore;
      });
    }
  }

  sweep(1); // forward: order each layer by its predecessor layer
  sweep(-1); // backward: order each layer by its successor layer
  return ordered;
}

export function layoutPositions(
  ids: string[],
  dependencies: DependencyEdge[],
  waves: Wave[] | null
): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();

  const layers = waves && waves.length > 0 ? waves.map((w) => [...w.component_ids]) : computeLayers(ids, dependencies);
  const ordered = reduceCrossings(layers, dependencies);
  const maxRows = Math.max(...ordered.map((l) => l.length), 1);

  ordered.forEach((layerIds, l) => {
    // Center shorter layers against the tallest one instead of top-aligning
    // everything — otherwise every layer's first node sits on the same row
    // regardless of how many neighbors it actually has, which biases edges
    // toward long diagonals for no structural reason.
    const offsetY = ((maxRows - layerIds.length) * ROW_HEIGHT) / 2;
    layerIds.forEach((id, row) => {
      positions.set(id, { x: l * COLUMN_WIDTH, y: offsetY + row * ROW_HEIGHT });
    });
  });

  return positions;
}

export function modelLayoutPositions(model: ArchitectureModel, waves: Wave[] | null) {
  return layoutPositions(
    model.components.map((c) => c.id),
    model.dependencies,
    waves
  );
}
