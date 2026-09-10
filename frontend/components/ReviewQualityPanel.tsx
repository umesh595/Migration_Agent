import type { ReviewQualityScore } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

function scoreColorClass(score: number): string {
  if (score >= 80) return "text-atlas-teal";
  if (score >= 50) return "text-atlas-amber";
  return "text-atlas-coral";
}

function scoreBarColorClass(score: number): string {
  if (score >= 80) return "bg-atlas-teal";
  if (score >= 50) return "bg-atlas-amber";
  return "bg-atlas-coral";
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-3 text-xs">
      <span className="w-32 shrink-0 text-muted-foreground">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
        <div
          className={cn("h-1.5 rounded-full transition-all duration-700 ease-out", scoreBarColorClass(value))}
          style={{ width: `${value}%` }}
        />
      </div>
      <span className={cn("w-8 text-right font-semibold", scoreColorClass(value))}>{value}</span>
    </div>
  );
}

/** Shows the judge's assessment of the AI critic's OWN findings — not part of the
 * migration deliverable, purely "is the AI's review actually good" observability
 * (PRD Decision Q7, overridden from v2 — see DECISIONS.md). Never implies this
 * gates approval; it doesn't. */
export function ReviewQualityPanel({ scores }: { scores: ReviewQualityScore[] }) {
  const latest = scores.at(-1);
  if (!latest) return null;

  const circumference = 2 * Math.PI * 26;
  const offset = circumference * (1 - latest.overall_score / 100);

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <span className="text-base">🧪</span> AI critique quality
        </h3>
        <div className="relative flex h-14 w-14 items-center justify-center">
          <svg viewBox="0 0 60 60" className="absolute h-14 w-14 -rotate-90">
            <circle cx="30" cy="30" r="26" fill="none" stroke="var(--border)" strokeWidth="5" />
            <circle
              cx="30"
              cy="30"
              r="26"
              fill="none"
              stroke="currentColor"
              strokeWidth="5"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={offset}
              className={cn(scoreColorClass(latest.overall_score), "transition-all duration-700 ease-out")}
            />
          </svg>
          <span className={cn("text-sm font-bold", scoreColorClass(latest.overall_score))}>{latest.overall_score}</span>
        </div>
      </div>
      <p className="mb-3 text-xs text-muted-foreground">
        An independent judge model scores the semantic critic&apos;s own findings — never the deterministic
        rules, which are already provably correct. This is diagnostic, not a gate: it doesn&apos;t block approval.
      </p>
      <div className="space-y-2">
        <ScoreBar label="Relevance" value={latest.relevance_score} />
        <ScoreBar label="Specificity" value={latest.specificity_score} />
        <ScoreBar label="Actionability" value={latest.actionability_score} />
        <ScoreBar label="Context awareness" value={latest.context_awareness_score} />
      </div>
      <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">{latest.rationale}</p>
      {latest.flagged_issues.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-atlas-amber">
          {latest.flagged_issues.map((issue, i) => (
            <li key={i} className="flex gap-1.5">
              <span>⚑</span> {issue}
            </li>
          ))}
        </ul>
      )}
      {scores.length > 1 && (
        <p className="mt-3 text-xs text-muted-foreground">
          Scored across {scores.length} refine iterations — showing the latest (iteration {latest.iteration}).
        </p>
      )}
    </Card>
  );
}
