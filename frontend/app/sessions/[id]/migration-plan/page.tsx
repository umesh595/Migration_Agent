"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ApiError, getSessionState } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { SessionState } from "@/lib/types";
import { NavBar } from "@/components/NavBar";
import { PLAN_SECTION_LINKS, PlanViewer } from "@/components/PlanViewer";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export default function SessionMigrationPlanPage() {
  const { user, loading: authLoading } = useRequireAuth();
  const params = useParams<{ id: string }>();
  const sessionId = params.id;

  const [state, setState] = useState<SessionState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setState(await getSessionState(sessionId));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not load the migration plan.");
    }
  }, [sessionId]);

  useEffect(() => {
    if (!user) return;
    refresh();
  }, [user, refresh]);

  if (authLoading || !user) return null;

  return (
    <div className="min-h-screen bg-atlas-mist dark:bg-background">
      <NavBar />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4 blueprint-reveal">
          <div>
            <Button asChild variant="outline" size="icon" className="h-9 w-9" aria-label="Back to conversation" title="Back to conversation">
              <Link href={`/sessions/${sessionId}`}>←</Link>
            </Button>
            <h1 className="mt-4 font-display text-2xl font-bold tracking-tight text-foreground">
              Migration plan
            </h1>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
              Review the target architecture, component mappings, dependency waves, effort, efficiency, cost,
              validation, cutover, and rollback strategy away from the conversation panel.
            </p>
          </div>
          {state?.session.status && <StatusBadge status={state.session.status} pulse />}
        </div>

        {error && (
          <Card className="mb-4 border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">{error}</Card>
        )}

        {!state ? (
          <div className="space-y-4">
            <Card className="h-40 shimmer" />
            <Card className="h-72 shimmer" />
          </div>
        ) : !state.plan ? (
          <Card className="max-w-2xl p-5">
            <p className="text-sm font-semibold text-foreground">Migration plan has not been generated yet.</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Accept the architecture model and provide migration context in the conversation page first.
            </p>
          </Card>
        ) : (
          <>
            <Card className="mb-4 p-5">
              <p className="text-sm font-semibold text-foreground">Open a plan section</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                Use these shortcuts to review one section at a time without scrolling through the full plan.
              </p>
              <div className="mt-3 grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
                {PLAN_SECTION_LINKS.map((section) => (
                  <Link
                    key={section.number}
                    href={`/sessions/${sessionId}/migration-plan/${section.number}`}
                    className="stat-tile !gap-1.5 !py-3 no-underline"
                  >
                    <span className="stat-tile-value !text-lg">{section.number}</span>
                    <span className="stat-tile-label !normal-case !tracking-normal text-muted-foreground">{section.title}</span>
                  </Link>
                ))}
              </div>
            </Card>
            <PlanViewer plan={state.plan} model={state.model} onlySection={1} />
          </>
        )}
      </main>
    </div>
  );
}
