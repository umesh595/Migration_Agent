"use client";

import { useEffect, useState } from "react";
import { Background, BackgroundVariant, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { Button } from "@/components/ui/button";

/** Shared canvas chrome for both the current- and target-architecture diagrams:
 * a taller default viewport than a bare ReactFlow box gets you, plus a REAL
 * fullscreen mode (a fixed-position overlay, not just the "fit view" control
 * button ReactFlow ships by default — that one only re-centers/re-zooms within
 * the same small box, which reads as "fullscreen does nothing" when someone
 * expects it to enlarge the viewport). Only one ReactFlow instance is ever
 * mounted at a time (inline or fullscreen, never both) — swapping remounts it,
 * which is cheap at these graph sizes and gives the new viewport a fresh fitView. */
export function DiagramFrame({
  nodes,
  edges,
  height = 560,
  title,
  ariaLabel,
}: {
  nodes: Node[];
  edges: Edge[];
  height?: number;
  title: string;
  ariaLabel: string;
}) {
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    if (!fullscreen) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setFullscreen(false);
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [fullscreen]);

  const canvas = (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      fitView
      fitViewOptions={{ padding: 0.25 }}
      minZoom={0.1}
      maxZoom={1.75}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--border)" />
      <Controls />
    </ReactFlow>
  );

  if (fullscreen) {
    return (
      <div className="blueprint-grid fixed inset-0 z-50 flex flex-col bg-background blueprint-reveal" role="img" aria-label={ariaLabel}>
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <p className="text-sm font-medium text-foreground">{title}</p>
          <Button type="button" variant="outline" size="sm" className="text-xs" onClick={() => setFullscreen(false)}>
            ✕ Close (Esc)
          </Button>
        </div>
        <div className="flex-1">{canvas}</div>
      </div>
    );
  }

  return (
    <div style={{ height }} className="relative overflow-hidden rounded-xl border border-border bg-card" role="img" aria-label={ariaLabel}>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="absolute right-3 top-3 z-10 text-xs"
        title="Expand to fullscreen"
        onClick={() => setFullscreen(true)}
      >
        ⛶ Fullscreen
      </Button>
      {canvas}
    </div>
  );
}
