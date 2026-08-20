"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { acceptModel, ApiError, approvePlan, getAudit, getFindings, getReviewQuality, getSessionState } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { Finding, PatchAuditEntry, ReviewQualityScore, SessionState } from "@/lib/types";
import { ArchitectureCanvas } from "@/components/ArchitectureCanvas";
import { AuditTrailPanel } from "@/components/AuditTrailPanel";
import { ChatPanel } from "@/components/ChatPanel";
import { ExportButtons } from "@/components/ExportButtons";
import { FindingsPanel } from "@/components/FindingsPanel";
import { NavBar } from "@/components/NavBar";
import { PlanViewer } from "@/components/PlanViewer";
import { ReviewQualityPanel } from "@/components/ReviewQualityPanel";
import { StatusBadge } from "@/components/StatusBadge";

export default function SessionWorkspacePage() {
  const { user, loading: authLoading } = useRequireAuth();
  const params = useParams<{ id: string }>();
  const sessionId = params.id;

  const [state, setState] = useState<SessionState | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [reviewQuality, setReviewQuality] = useState<ReviewQualityScore[]>([]);
  const [auditRecords, setAuditRecords] = useState<PatchAuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [gateBusy, setGateBusy] = useState(false);
  const needsMigrationContext = state?.session.status === "planning" && !state.migration_context && !state.plan;

  const refresh = useCallback(async () => {
    try {
      const nextState = await getSessionState(sessionId);
      setState(nextState);
      const { records } = await getAudit(sessionId);
      setAuditRecords(records);
      if (nextState.plan) {
        const [{ findings: f }, { scores }] = await Promise.all([
          getFindings(sessionId),
          getReviewQuality(sessionId),
        ]);
        setFindings(f);
        setReviewQuality(scores);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not load this session.");
    }
  }, [sessionId]);

  useEffect(() => {
    if (!user) return;
    refresh();
  }, [user, refresh]);

  async function handleAcceptModel() {
    setGateBusy(true);
    setError(null);
    try {
      await acceptModel(sessionId);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not accept the model.");
    } finally {
      setGateBusy(false);
    }
  }

  async function handleApprovePlan() {
    setGateBusy(true);
    setError(null);
    try {
      await approvePlan(sessionId);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not approve the plan.");
    } finally {
      setGateBusy(false);
    }
  }

  if (authLoading || !user) return null;

  const status = state?.session.status;
  const hasBlockingFindings = findings.some(
    (f) => f.severity === "error" && f.resolution_status === "open"
  );

  return (
    <div className="min-h-screen">
      <NavBar />
      <main className="mx-auto max-w-6xl px-4 py-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">{state?.session.name ?? "Loading…"}</h1>
            <p className="text-xs text-slate-400">
              Model v{state?.model.version} · {state?.model.components.length ?? 0} components
            </p>
          </div>
          {status && <StatusBadge status={status} />}
        </div>

        {error && (
          <p role="alert" className="card mb-4 border-red-200 bg-red-50 text-sm text-red-700">
            {error}
          </p>
        )}

        {!state ? (
          <p className="text-sm text-slate-400">Loading session…</p>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="space-y-4">
              {needsMigrationContext && (
                <div className="card border-blue-200 bg-blue-50">
                  <h3 className="mb-1 text-sm font-semibold text-blue-900">Gate 1 passed - migration context needed</h3>
                  <p className="mb-3 text-xs text-blue-800">
                    The architecture model is frozen. Send the migration goal so the Planning Agent can build the
                    target architecture, sequence, cutover, rollback, and review package.
                  </p>
                  <div className="rounded-md border border-blue-200 bg-white p-3 text-xs text-slate-700">
                    <div className="font-medium text-slate-800">Include these details:</div>
                    <ul className="mt-1 list-disc space-y-1 pl-5">
                      <li>source environment and target environment</li>
                      <li>target platform or cloud services you prefer</li>
                      <li>downtime tolerance or maintenance window</li>
                      <li>constraints such as compliance, timeline, budget, or services that must remain unchanged</li>
                    </ul>
                    <div className="mt-2 text-slate-500">
                      Example: Move this AWS-hosted platform to GCP Cloud Run and Cloud Storage. A 4-hour
                      maintenance window is acceptable. Keep user authentication behavior unchanged and preserve
                      private document access.
                    </div>
                  </div>
                </div>
              )}

              <ChatPanel
                sessionId={sessionId}
                onTurnComplete={refresh}
                disabled={status === "exported"}
                disabledReason="This plan has been finalized — no further turns are accepted."
                placeholder={
                  status === "discovery"
                    ? "Describe your existing system, e.g. \"We have a customer portal, backend APIs, PostgreSQL, event streaming, and a data warehouse.\""
                    : status === "planning"
                      ? "Describe your migration goal, e.g. \"Move everything from on-prem to AWS, 4-hour maintenance window is acceptable.\""
                      : "Planning is complete. Review the plan and findings, then approve when ready."
                }
              />

              {status === "discovery" && (
                <div className="card">
                  <h3 className="mb-1 text-sm font-semibold text-slate-700">Gate 1 — Accept the architecture</h3>
                  <p className="mb-3 text-xs text-slate-500">
                    Migration planning is unreachable until you accept this model — this is a structural gate,
                    not a suggestion.
                  </p>
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={gateBusy || state.model.components.length === 0}
                    onClick={handleAcceptModel}
                  >
                    {gateBusy ? "Accepting…" : "Accept architecture model"}
                  </button>
                </div>
              )}

              {status === "review" && (
                <div className="card">
                  <h3 className="mb-1 text-sm font-semibold text-slate-700">Gate 2 — Approve the plan</h3>
                  {hasBlockingFindings ? (
                    <p className="mb-3 text-xs text-amber-700">
                      There are still open error-severity findings below. You can approve anyway — the
                      remaining review-refinement budget for this plan has been used and unresolved
                      findings ship as documented risks — but check them first.
                    </p>
                  ) : (
                    <p className="mb-3 text-xs text-slate-500">Review is complete with no open blocking findings.</p>
                  )}
                  <button type="button" className="btn-primary" disabled={gateBusy} onClick={handleApprovePlan}>
                    {gateBusy ? "Approving…" : "Approve final plan"}
                  </button>
                </div>
              )}

              {status === "exported" && (
                <div className="card">
                  <h3 className="mb-2 text-sm font-semibold text-slate-700">Export</h3>
                  <ExportButtons sessionId={sessionId} />
                </div>
              )}
            </div>

            <div className="space-y-4">
              <ArchitectureCanvas model={state.model} waves={state.plan?.waves} sessionId={sessionId} />

              {state.model.open_questions.some((q) => !q.resolved) && (
                <div className="card">
                  <h3 className="mb-2 text-sm font-semibold text-slate-700">Open questions</h3>
                  <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
                    {state.model.open_questions
                      .filter((q) => !q.resolved)
                      .map((q) => (
                        <li key={q.id}>{q.text}</li>
                      ))}
                  </ul>
                </div>
              )}

              {state.migration_context && (
                <div className="card">
                  <h3 className="mb-2 text-sm font-semibold text-slate-700">Migration context</h3>
                  <dl className="grid grid-cols-2 gap-2 text-sm">
                    <dt className="text-slate-500">Source</dt>
                    <dd>{state.migration_context.source_environment}</dd>
                    <dt className="text-slate-500">Target</dt>
                    <dd>{state.migration_context.target_environment} — {state.migration_context.target_platform_description}</dd>
                    <dt className="text-slate-500">Downtime tolerance</dt>
                    <dd>{state.migration_context.downtime_tolerance.replace(/_/g, " ")}</dd>
                  </dl>
                  {state.migration_context.constraints.length > 0 && (
                    <ul className="mt-2 list-disc pl-5 text-xs text-slate-600">
                      {state.migration_context.constraints.map((c, i) => (
                        <li key={i}>{c}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {findings.length > 0 && (
                <div>
                  <h3 className="mb-2 text-sm font-semibold text-slate-700">Review findings</h3>
                  <FindingsPanel findings={findings} sessionId={sessionId} onChanged={refresh} />
                </div>
              )}

              <ReviewQualityPanel scores={reviewQuality} />

              <div>
                <h3 className="mb-2 text-sm font-semibold text-slate-700">Patch audit trail</h3>
                <AuditTrailPanel records={auditRecords} />
              </div>
            </div>
          </div>
        )}

        {state?.plan && (
          <div className="mt-6">
            <h2 className="mb-3 text-lg font-semibold text-slate-900">Migration plan</h2>
            <PlanViewer plan={state.plan} model={state.model} />
          </div>
        )}
      </main>
    </div>
  );
}
