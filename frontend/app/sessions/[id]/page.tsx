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

  const STAGES: { key: string; label: string; icon: string }[] = [
    { key: "discovery", label: "Discovery", icon: "🔍" },
    { key: "planning", label: "Planning", icon: "🧭" },
    { key: "review", label: "Review", icon: "🛡️" },
    { key: "exported", label: "Exported", icon: "📦" },
  ];
  const currentStageIndex = STAGES.findIndex((s) => s.key === status);

  return (
    <div className="min-h-screen">
      <NavBar />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4 animate-fade-up">
          <div>
            <h1 className="font-display text-2xl font-bold tracking-tight text-white">
              {state?.session.name ?? "Loading…"}
            </h1>
            <p className="mt-1 text-xs text-slate-500">
              Model v{state?.model.version} · {state?.model.components.length ?? 0} components
            </p>
          </div>
          {status && <StatusBadge status={status} pulse />}
        </div>

        {/* Stage stepper — a visual spine showing where this study is in the pipeline */}
        {status && (
          <div className="card mb-6 animate-fade-up" style={{ animationDelay: "40ms" }}>
            <div className="flex items-center">
              {STAGES.map((stage, i) => {
                const isDone = i < currentStageIndex;
                const isActive = i === currentStageIndex;
                return (
                  <div key={stage.key} className="flex flex-1 items-center last:flex-none">
                    <div className="flex flex-col items-center gap-1.5">
                      <div
                        className={`flex h-9 w-9 items-center justify-center rounded-full text-sm transition-all duration-300 ${
                          isDone
                            ? "bg-grad-primary text-white shadow-glow"
                            : isActive
                              ? "bg-grad-primary text-white shadow-glow animate-pulse-ring"
                              : "border border-white/15 bg-white/[0.03] text-slate-500"
                        }`}
                      >
                        {isDone ? "✓" : stage.icon}
                      </div>
                      <span
                        className={`text-[11px] font-medium ${isActive ? "text-white" : isDone ? "text-slate-300" : "text-slate-500"}`}
                      >
                        {stage.label}
                      </span>
                    </div>
                    {i < STAGES.length - 1 && (
                      <div
                        className={`mx-2 h-0.5 flex-1 rounded-full transition-colors duration-300 ${
                          isDone ? "bg-gradient-to-r from-brand-500 to-brand-400" : "bg-white/10"
                        }`}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {error && (
          <p role="alert" className="card mb-4 border-rose-500/30 bg-rose-500/10 text-sm text-rose-300 animate-fade-up">
            {error}
          </p>
        )}

        {!state ? (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="card h-72 shimmer" />
            <div className="card h-72 shimmer" />
          </div>
        ) : (
          <>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="space-y-4">
              {needsMigrationContext && (
                <div className="card-glow border-sky-400/25 bg-sky-500/[0.06] animate-fade-up">
                  <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-sky-200">
                    <span className="text-base">🧭</span> Gate 1 passed — migration context needed
                  </h3>
                  <p className="mb-3 text-xs text-sky-300/80">
                    The architecture model is frozen. Send the migration goal so the Planning Agent can build the
                    target architecture, sequence, cutover, rollback, and review package.
                  </p>
                  <p className="mb-3 text-xs text-sky-300/80">
                    After you send the goal, larger models can take a few minutes while planning and review run.
                  </p>
                  <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-xs text-slate-300">
                    <div className="font-medium text-slate-200">Include these details:</div>
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

              {status === "exported" ? (
                <div className="card-glow flex flex-col items-center gap-3 py-10 text-center animate-fade-up">
                  <span className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-500/15 text-3xl ring-1 ring-emerald-400/30">
                    ✅
                  </span>
                  <div>
                    <h3 className="font-display text-lg font-semibold text-white">Export ready</h3>
                    <p className="mt-1 max-w-sm text-sm text-slate-400">
                      This plan has been reviewed, approved, and finalized — no further turns are accepted. Download
                      the full 10-deliverable package below.
                    </p>
                  </div>
                  <div className="mt-2 w-full max-w-sm">
                    <ExportButtons sessionId={sessionId} />
                  </div>
                </div>
              ) : (
                <ChatPanel
                  sessionId={sessionId}
                  onTurnComplete={refresh}
                  workflowStatus={status}
                  componentCount={state.model.components.length}
                  placeholder={
                    status === "discovery"
                      ? "Describe your existing system, e.g. \"We have a customer portal, backend APIs, PostgreSQL, event streaming, and a data warehouse.\""
                      : status === "planning"
                        ? "Describe your migration goal, e.g. \"Move everything from on-prem to AWS, 4-hour maintenance window is acceptable.\""
                        : "Planning is complete. Review the plan and findings, then approve when ready."
                  }
                />
              )}

              {status === "discovery" && (
                <div className="card-glow animate-fade-up">
                  <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-200">
                    <span className="text-base">🔍</span> Gate 1 — Accept the architecture
                  </h3>
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
                <div className="card-glow animate-fade-up">
                  <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-200">
                    <span className="text-base">🛡️</span> Gate 2 — Approve the plan
                  </h3>
                  {hasBlockingFindings ? (
                    <p className="mb-3 text-xs text-amber-300">
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

            </div>

            <div className="space-y-4">
              <ArchitectureCanvas model={state.model} waves={state.plan?.waves} sessionId={sessionId} />

              {state.model.open_questions.some((q) => !q.resolved) && (
                <div className="card">
                  <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-200">
                    <span className="text-base">❓</span> Open questions
                  </h3>
                  <ul className="space-y-1.5 text-sm text-slate-300">
                    {state.model.open_questions
                      .filter((q) => !q.resolved)
                      .map((q) => (
                        <li key={q.id} className="flex gap-2">
                          <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
                          {q.text}
                        </li>
                      ))}
                  </ul>
                </div>
              )}

              {state.migration_context && (
                <div className="card">
                  <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-200">
                    <span className="text-base">🌐</span> Migration context
                  </h3>
                  <dl className="grid grid-cols-2 gap-y-2 text-sm">
                    <dt className="text-slate-500">Source</dt>
                    <dd className="text-slate-200">{state.migration_context.source_environment}</dd>
                    <dt className="text-slate-500">Target</dt>
                    <dd className="text-slate-200">
                      {state.migration_context.target_environment} — {state.migration_context.target_platform_description}
                    </dd>
                    <dt className="text-slate-500">Downtime tolerance</dt>
                    <dd className="text-slate-200">{state.migration_context.downtime_tolerance.replace(/_/g, " ")}</dd>
                  </dl>
                  {state.migration_context.constraints.length > 0 && (
                    <ul className="mt-3 space-y-1 border-t border-white/10 pt-3 text-xs text-slate-400">
                      {state.migration_context.constraints.map((c, i) => (
                        <li key={i} className="flex gap-2">
                          <span className="text-brand-400">•</span> {c}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {findings.length > 0 && (
                <div>
                  <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-200">
                    <span className="text-base">⚠️</span> Review findings
                  </h3>
                  <FindingsPanel findings={findings} sessionId={sessionId} onChanged={refresh} />
                </div>
              )}

              <ReviewQualityPanel scores={reviewQuality} />

            </div>
          </div>
          <div className="mt-6 animate-fade-up">
            <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-200">
              <span className="text-base">📜</span> Patch audit trail
            </h3>
            <AuditTrailPanel records={auditRecords} />
          </div>
          </>
        )}

        {state?.plan && (
          <div className="mt-8 animate-fade-up">
            <h2 className="mb-4 font-display text-xl font-bold text-white">
              Migration <span className="text-gradient">plan</span>
            </h2>
            <PlanViewer plan={state.plan} model={state.model} />
          </div>
        )}
      </main>
    </div>
  );
}
