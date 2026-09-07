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
    <div className="min-h-screen">
      <NavBar />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4 animate-fade-up">
          <div>
            <Link
              href={`/sessions/${sessionId}/migration-plan`}
              className="btn-secondary !h-9 !w-9 !px-0 !py-0"
              aria-label="Back to migration plan"
              title="Back to migration plan"
            >
              ←
            </Link>
            <h1 className="mt-4 font-display text-2xl font-bold tracking-tight text-white">
              {section ? `${section.number}. ${section.title}` : "Migration plan section"}
            </h1>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-400">
              Review this migration plan section separately, then return to the full plan when ready.
            </p>
          </div>
          {state?.session.status && <StatusBadge status={state.session.status} pulse />}
        </div>

        {error && (
          <p role="alert" className="card mb-4 border-rose-500/30 bg-rose-500/10 text-sm text-rose-300">
            {error}
          </p>
        )}

        {!isValidSection ? (
          <div className="card max-w-2xl">
            <p className="text-sm font-semibold text-slate-200">Unknown migration plan section.</p>
            <p className="mt-1 text-sm leading-6 text-slate-500">
              Use the Migration Plan page to open sections 2 through 10.
            </p>
          </div>
        ) : !state ? (
          <div className="space-y-4">
            <div className="card h-40 shimmer" />
            <div className="card h-72 shimmer" />
          </div>
        ) : !state.plan ? (
          <div className="card max-w-2xl">
            <p className="text-sm font-semibold text-slate-200">Migration plan has not been generated yet.</p>
            <p className="mt-1 text-sm leading-6 text-slate-500">
              Accept the architecture model and provide migration context in the conversation page first.
            </p>
          </div>
        ) : sectionNumber === 10 && !state.plan.cost_summary ? (
          <div className="card max-w-2xl">
            <p className="text-sm font-semibold text-slate-200">Cost estimate not generated yet.</p>
            <p className="mt-1 text-sm leading-6 text-slate-500">
              This plan does not currently include a cost summary section.
            </p>
          </div>
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
