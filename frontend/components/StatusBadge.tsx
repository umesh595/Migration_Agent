import type { SessionStatus } from "@/lib/types";

const STYLES: Record<SessionStatus, string> = {
  discovery: "bg-amber-100 text-amber-800",
  planning: "bg-blue-100 text-blue-800",
  review: "bg-purple-100 text-purple-800",
  exported: "bg-green-100 text-green-800",
};

const LABELS: Record<SessionStatus, string> = {
  discovery: "Discovery",
  planning: "Planning",
  review: "Review",
  exported: "Exported",
};

export function StatusBadge({ status }: { status: SessionStatus }) {
  return <span className={`badge ${STYLES[status]}`}>{LABELS[status]}</span>;
}
