"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ApiError, getFindings, getReviewQuality, getSessionState } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { Finding, ReviewQualityScore, SessionState } from "@/lib/types";
import { FindingsPanel } from "@/components/FindingsPanel";
import { NavBar } from "@/components/NavBar";
import { ReviewQualityPanel } from "@/components/ReviewQualityPanel";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export default function SessionReviewFindingsPage() {
  const { user, loading: authLoading } = useRequireAuth();
  const params = useParams<{ id: string }>();
  const sessionId = params.id;

  const [state, setState] = useState<SessionState | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [reviewQualityScores, setReviewQualityScores] = useState<ReviewQualityScore[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const nextState = await getSessionState(sessionId);
      setState(nextState);

      if (nextState.plan) {
        const [{ findings: nextFindings }, { scores }] = await Promise.all([
          getFindings(sessionId),
          getReviewQuality(sessionId),
        ]);
        setFindings(nextFindings);
        setReviewQualityScores(scores);
      } else {
        setFindings([]);
        setReviewQualityScores([]);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not load review findings.");
    }
  }, [sessionId]);

  useEffect(() => {
    if (!user) return;
    refresh();
  }, [user, refresh]);

  if (authLoading || !user) return null;

  const openCount = findings.filter((f) => f.resolution_status === "open").length;
  const errorCount = findings.filter((f) => f.severity === "error").length;

  return (
    <div className="min-h-screen bg-atlas-mist dark:bg-background">
      <NavBar />
      <main className="mx-auto max-w-5xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4 blueprint-reveal">
          <div>
            <Button asChild variant="outline" size="icon" className="h-9 w-9" aria-label="Back to conversation" title="Back to conversation">
              <Link href={`/sessions/${sessionId}`}>←</Link>
            </Button>
            <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-foreground">
              Review findings
            </h1>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
              Review what the rule engine and semantic critic challenged before Gate 2 approval. Findings can be
              resolved, reopened, or accepted as documented migration risks.
            </p>
          </div>
          {state?.session.status && <StatusBadge status={state.session.status} pulse />}
        </div>

        {error && (
          <Card className="mb-4 border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">{error}</Card>
        )}

        {!state ? (
          <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
            <Card className="h-72 shimmer" />
            <Card className="h-48 shimmer" />
          </div>
        ) : !state.plan ? (
          <Card className="max-w-2xl p-5">
            <p className="text-sm font-semibold text-foreground">Review has not run yet.</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Generate a migration plan first. Review findings appear after planning and review complete.
            </p>
          </Card>
        ) : (
          <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
            <div>
              <FindingsPanel findings={findings} sessionId={sessionId} onChanged={refresh} />
            </div>
            <aside className="space-y-4">
              <Card className="p-5">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Finding summary</p>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <div className="stat-tile !py-3">
                    <span className="stat-tile-value !text-lg">{findings.length}</span>
                    <span className="stat-tile-label">Total</span>
                  </div>
                  <div className="stat-tile !py-3">
                    <span className="stat-tile-value !text-lg">{openCount}</span>
                    <span className="stat-tile-label">Open</span>
                  </div>
                  <div className="stat-tile !py-3 col-span-2">
                    <span className="stat-tile-value !text-lg text-atlas-coral">{errorCount}</span>
                    <span className="stat-tile-label">Errors</span>
                  </div>
                </div>
              </Card>
              {reviewQualityScores.length > 0 && <ReviewQualityPanel scores={reviewQualityScores} />}
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}
