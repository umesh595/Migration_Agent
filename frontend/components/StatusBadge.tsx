import type { SessionStatus } from "@/lib/types";

const STYLES: Record<SessionStatus, string> = {
  discovery: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  planning: "border-sky-400/30 bg-sky-400/10 text-sky-300",
  review: "border-cyan-400/30 bg-cyan-400/10 text-cyan-300",
  exported: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
};

const DOT_STYLES: Record<SessionStatus, string> = {
  discovery: "bg-amber-400",
  planning: "bg-sky-400",
  review: "bg-cyan-400",
  exported: "bg-emerald-400",
};

const LABELS: Record<SessionStatus, string> = {
  discovery: "Discovery",
  planning: "Planning",
  review: "Review",
  exported: "Exported",
};

export function StatusBadge({ status, pulse = false }: { status: SessionStatus; pulse?: boolean }) {
  return (
    <span className={`badge ${STYLES[status]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${DOT_STYLES[status]} ${pulse ? "animate-pulse-ring" : ""}`} />
      {LABELS[status]}
    </span>
  );
}
