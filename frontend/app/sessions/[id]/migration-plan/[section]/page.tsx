"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ApiError, getSessionState } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { SessionState } from "@/lib/types";
import { NavBar } from "@/components/NavBar";
import { PLAN_SECTION_LINKS, PlanViewer, type PlanSectionNumber } from "@/components/PlanViewer";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const VALID_SECTIONS = new Set<number>(PLAN_SECTION_LINKS.map((section) => section.number));

export default function MigrationPlanSectionPage() {
  const { user, loading: authLoading } = useRequireAuth();
  const params = useParams<{ id: string; section: string }>();
  const sessionId = params.id;

  const sectionNumber = Number(params.section);
  const section = useMemo(
    () => PLAN_SECTION_LINKS.find((item) => item.number === sectionNumber),
    [sectionNumber]
  );

  const [state, setState] = useState<SessionState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setState(await getSessionState(sessionId));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not load this migration plan section.");
    }
  }, [sessionId]);

  useEffect(() => {
    if (!user) return;
    refresh();
  }, [user, refresh]);

  if (authLoading || !user) return null;

  const isValidSection = VALID_SECTIONS.has(sectionNumber);

  return (
    <div className="min-h-screen bg-atlas-mist dark:bg-background">
      <NavBar />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4 blueprint-reveal">
          <div>
            <Button
              asChild
              variant="outline"
              size="icon"
              className="h-9 w-9"
              aria-label="Back to migration plan"
              title="Back to migration plan"
            >
              <Link href={`/sessions/${sessionId}/migration-plan`}>←</Link>
            </Button>
            <h1 className="mt-4 font-display text-2xl font-bold tracking-tight text-foreground">
              {section ? `${section.number}. ${section.title}` : "Migration plan section"}
            </h1>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
              Review this migration plan section separately, then return to the full plan when ready.
            </p>
          </div>
          {state?.session.status && <StatusBadge status={state.session.status} pulse />}
        </div>

        {error && (
          <Card className="mb-4 border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">{error}</Card>
        )}

        {!isValidSection ? (
          <Card className="max-w-2xl p-5">
            <p className="text-sm font-semibold text-foreground">Unknown migration plan section.</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Use the Migration Plan page to open sections 2 through 10.
            </p>
          </Card>
        ) : !state ? (
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
        ) : sectionNumber === 10 && !state.plan.cost_summary ? (
          <Card className="max-w-2xl p-5">
            <p className="text-sm font-semibold text-foreground">Cost estimate not generated yet.</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              This plan does not currently include a cost summary section.
            </p>
          </Card>
        ) : (
          <PlanViewer
            plan={state.plan}
            model={state.model}
            onlySection={sectionNumber as PlanSectionNumber}
            showIntro={false}
          />
        )}
      </main>
    </div>
  );
}
