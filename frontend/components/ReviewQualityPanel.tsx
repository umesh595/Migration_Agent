import type { ReviewQualityScore } from "@/lib/types";

function scoreColor(score: number): string {
  if (score >= 80) return "text-green-700";
  if (score >= 50) return "text-amber-700";
  return "text-red-700";
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-32 shrink-0 text-slate-500">{label}</span>
      <div className="h-1.5 flex-1 rounded-full bg-slate-100">
        <div
          className={`h-1.5 rounded-full ${value >= 80 ? "bg-green-500" : value >= 50 ? "bg-amber-500" : "bg-red-500"}`}
          style={{ width: `${value}%` }}
        />
      </div>
      <span className={`w-8 text-right font-medium ${scoreColor(value)}`}>{value}</span>
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

  return (
    <div className="card">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700">AI critique quality</h3>
        <span className={`text-lg font-semibold ${scoreColor(latest.overall_score)}`}>
          {latest.overall_score}/100
        </span>
      </div>
      <p className="mb-3 text-xs text-slate-500">
        An independent judge model scores the semantic critic's own findings — never the deterministic
        rules, which are already provably correct. This is diagnostic, not a gate: it doesn't block approval.
      </p>
      <div className="space-y-1.5">
        <ScoreBar label="Relevance" value={latest.relevance_score} />
        <ScoreBar label="Specificity" value={latest.specificity_score} />
        <ScoreBar label="Actionability" value={latest.actionability_score} />
        <ScoreBar label="Context awareness" value={latest.context_awareness_score} />
      </div>
      <p className="mt-3 text-xs text-slate-600">{latest.rationale}</p>
      {latest.flagged_issues.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-xs text-amber-700">
          {latest.flagged_issues.map((issue, i) => (
            <li key={i}>{issue}</li>
          ))}
        </ul>
      )}
      {scores.length > 1 && (
        <p className="mt-3 text-xs text-slate-400">
          Scored across {scores.length} refine iterations — showing the latest (iteration {latest.iteration}).
        </p>
      )}
    </div>
  );
}
