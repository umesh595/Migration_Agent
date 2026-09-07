"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ApiError, getFindings, getSessionState } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { Finding, SessionState } from "@/lib/types";
import { FindingsPanel } from "@/components/FindingsPanel";
import { NavBar } from "@/components/NavBar";
import { StatusBadge } from "@/components/StatusBadge";

export default function SessionReviewFindingsPage() {
  const { user, loading: authLoading } = useRequireAuth();
  const params = useParams<{ id: string }>();
  const sessionId = params.id;

  const [state, setState] = useState<SessionState | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const nextState = await getSessionState(sessionId);
      setState(nextState);

      if (nextState.plan) {
        const { findings: nextFindings } = await getFindings(sessionId);
        setFindings(nextFindings);
      } else {
        setFindings([]);
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
    <div className="min-h-screen">
      <NavBar />
      <main className="mx-auto max-w-5xl px-4 py-8">
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
            <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-white">
              Review findings
            </h1>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-400">
              Review what the rule engine and semantic critic challenged before Gate 2 approval. Findings can be
              resolved, reopened, or accepted as documented migration risks.
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
          <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
            <div className="card h-72 shimmer" />
            <div className="card h-48 shimmer" />
          </div>
        ) : !state.plan ? (
          <div className="card max-w-2xl">
            <p className="text-sm font-semibold text-slate-200">Review has not run yet.</p>
            <p className="mt-1 text-sm leading-6 text-slate-500">
              Generate a migration plan first. Review findings appear after planning and review complete.
            </p>
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
            <div>
              <FindingsPanel findings={findings} sessionId={sessionId} onChanged={refresh} />
            </div>
            <aside className="space-y-4">
              <div className="card">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Finding summary</p>
                <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <dt className="text-slate-500">Total</dt>
                    <dd className="mt-0.5 font-medium text-slate-200">{findings.length}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Open</dt>
                    <dd className="mt-0.5 font-medium text-slate-200">{openCount}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Errors</dt>
                    <dd className="mt-0.5 font-medium text-slate-200">{errorCount}</dd>
                  </div>
                </dl>
              </div>
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}
