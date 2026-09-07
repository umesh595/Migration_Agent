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
    <div className="min-h-screen">
      <NavBar />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4 animate-fade-up">
          <div>
            <Link
              href={`/sessions/${sessionId}`}
              className="btn-secondary !h-9 !w-9 !px-0 !py-0"
              aria-label="Back to conversation"
              title="Back to conversation"
            >
              ←
            </Link>
            <h1 className="mt-4 font-display text-2xl font-bold tracking-tight text-white">
              Migration plan
            </h1>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-400">
              Review the target architecture, component mappings, dependency waves, effort, efficiency, cost,
              validation, cutover, and rollback strategy away from the conversation panel.
            </p>
          </div>
          {state?.session.status && <StatusBadge status={state.session.status} pulse />}
        </div>

        {error && (
          <p role="alert" className="card mb-4 border-rose-500/30 bg-rose-500/10 text-sm text-rose-300">
            {error}
          </p>
        )}

        {!state ? (
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
        ) : (
          <>
            <div className="card mb-4">
              <p className="text-sm font-semibold text-slate-100">Open a plan section</p>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                Use these shortcuts to review one section at a time without scrolling through the full plan.
              </p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {PLAN_SECTION_LINKS.map((section) => (
                  <Link
                    key={section.number}
                    href={`/sessions/${sessionId}/migration-plan/${section.number}`}
                    className="btn-secondary justify-start !px-3 !py-2 text-left text-xs"
                  >
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-brand-500/15 text-[11px] font-bold text-brand-200">
                      {section.number}
                    </span>
                    {section.title}
                  </Link>
                ))}
              </div>
            </div>
            <PlanViewer plan={state.plan} model={state.model} onlySection={1} />
          </>
        )}
      </main>
    </div>
  );
}
