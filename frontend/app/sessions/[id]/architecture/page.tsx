"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ApiError, getSessionState } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { SessionState } from "@/lib/types";
import { ArchitectureCanvas } from "@/components/ArchitectureCanvas";
import { NavBar } from "@/components/NavBar";
import { StatusBadge } from "@/components/StatusBadge";

export default function SessionArchitecturePage() {
  const { user, loading: authLoading } = useRequireAuth();
  const params = useParams<{ id: string }>();
  const sessionId = params.id;

  const [state, setState] = useState<SessionState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setState(await getSessionState(sessionId));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not load this session.");
    }
  }, [sessionId]);

  useEffect(() => {
    if (!user) return;
    refresh();
  }, [user, refresh]);

  if (authLoading || !user) return null;

  const unresolvedQuestions = state?.model.open_questions.filter((q) => !q.resolved) ?? [];

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
            <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-white">
              Current architecture
            </h1>
            <p className="mt-1 text-sm leading-6 text-slate-400">
              Review the discovered source model, dependency diagram, and unresolved questions separately from the AI
              conversation.
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
          <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
            <div className="card h-[520px] shimmer" />
            <div className="card h-64 shimmer" />
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
            <ArchitectureCanvas model={state.model} waves={state.plan?.waves} sessionId={sessionId} />

            <aside className="space-y-4">
              <div className="card">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Model summary</p>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <div className="stat-tile !py-3">
                    <span className="stat-tile-value !text-lg">v{state.model.version}</span>
                    <span className="stat-tile-label">Version</span>
                  </div>
                  <div className="stat-tile !py-3">
                    <span className="stat-tile-value !text-lg">{state.model.components.length}</span>
                    <span className="stat-tile-label">Components</span>
                  </div>
                  <div className="stat-tile !py-3">
                    <span className="stat-tile-value !text-lg">{state.model.dependencies.length}</span>
                    <span className="stat-tile-label">Dependencies</span>
                  </div>
                  <div className="stat-tile !py-3">
                    <span className="stat-tile-value !text-lg">{unresolvedQuestions.length}</span>
                    <span className="stat-tile-label">Open questions</span>
                  </div>
                </div>
              </div>

              <div className="card">
                <h2 className="text-sm font-semibold text-slate-200">Open questions</h2>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  These are the unresolved source-model questions. Answer them in the conversation page so the agent can
                  update the model with an auditable patch.
                </p>
                {unresolvedQuestions.length > 0 ? (
                  <ul className="mt-3 space-y-2 text-sm text-slate-300">
                    {unresolvedQuestions.map((q) => (
                      <li key={q.id} className="rounded-lg border border-amber-400/15 bg-amber-400/[0.045] p-2.5">
                        {q.text}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-3 rounded-lg border border-emerald-400/15 bg-emerald-400/[0.045] p-2.5 text-sm text-emerald-200">
                    No unresolved architecture questions.
                  </p>
                )}
              </div>
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}
