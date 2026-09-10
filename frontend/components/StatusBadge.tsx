import type { SessionStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

// Restyled onto the ported "Atlas" token set (vivid-insights-hub) — same
// status->color mapping as before, just using the new semantic colors:
// discovery=amber (needs attention), planning=sky, review=cyan (no atlas
// equivalent, kept as a distinct 4th hue), exported=teal (done).
const STYLES: Record<SessionStatus, string> = {
  discovery: "border-transparent bg-atlas-amber-soft text-atlas-amber",
  planning: "border-transparent bg-atlas-sky/60 text-sky-700 dark:text-sky-200",
  review: "border-transparent bg-cyan-400/10 text-cyan-300",
  exported: "border-transparent bg-atlas-teal-soft text-atlas-teal",
};

const DOT_STYLES: Record<SessionStatus, string> = {
  discovery: "bg-atlas-amber",
  planning: "bg-sky-500",
  review: "bg-cyan-400",
  exported: "bg-atlas-teal",
};

const LABELS: Record<SessionStatus, string> = {
  discovery: "Discovery",
  planning: "Planning",
  review: "Review",
  exported: "Exported",
};

export function StatusBadge({ status, pulse = false }: { status: SessionStatus; pulse?: boolean }) {
  return (
    <span className={cn("badge", STYLES[status])}>
      <span className={cn("h-1.5 w-1.5 rounded-full", DOT_STYLES[status], pulse && "agent-pulse")} />
      {LABELS[status]}
    </span>
  );
}
