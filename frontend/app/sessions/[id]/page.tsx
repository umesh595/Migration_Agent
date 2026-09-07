"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { acceptModel, ApiError, approvePlan, getAudit, getFindings, getSessionState } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { ArchitectureModel, Finding, PatchAuditEntry, SessionState } from "@/lib/types";
import { AuditTrailPanel } from "@/components/AuditTrailPanel";
import { ChatPanel, type ChatDraft } from "@/components/ChatPanel";
import { ExportButtons } from "@/components/ExportButtons";
import { NavBar } from "@/components/NavBar";
import { StatusBadge } from "@/components/StatusBadge";

export default function SessionWorkspacePage() {
  const { user, loading: authLoading } = useRequireAuth();
  const params = useParams<{ id: string }>();
  const sessionId = params.id;

  const [state, setState] = useState<SessionState | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [auditRecords, setAuditRecords] = useState<PatchAuditEntry[]>([]);
  const [chatDraft, setChatDraft] = useState<ChatDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gateBusy, setGateBusy] = useState(false);
  const [showGate1Confirm, setShowGate1Confirm] = useState(false);
  const needsMigrationContext = state?.session.status === "planning" && !state.migration_context && !state.plan;

  const refresh = useCallback(async () => {
    try {
      const nextState = await getSessionState(sessionId);
      setState(nextState);
      const { records } = await getAudit(sessionId);
      setAuditRecords(records);
      if (nextState.plan) {
        const { findings: f } = await getFindings(sessionId);
        setFindings(f);
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
      setShowGate1Confirm(false);
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

  function handleReviewPatch(draft: string) {
    setChatDraft({ id: crypto.randomUUID(), text: draft });
    window.requestAnimationFrame(() => {
      document.getElementById("conversation-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  if (authLoading || !user) return null;

  const status = state?.session.status;
  const hasBlockingFindings = findings.some(
    (f) => f.severity === "error" && f.resolution_status === "open"
  );
  const gate1Summary = state ? buildGate1Summary(state.model) : null;

  const STAGES: { key: string; label: string; step: string }[] = [
    { key: "discovery", label: "Discovery", step: "1" },
    { key: "planning", label: "Planning", step: "2" },
    { key: "review", label: "Review", step: "3" },
    { key: "exported", label: "Exported", step: "4" },
  ];
  const currentStageIndex = STAGES.findIndex((s) => s.key === status);
  const stageGuide = status
    ? {
        discovery: {
          title: "Current task: build the source architecture model",
          body: "The agent extracts components, dependencies, criticality, assumptions, and open questions from your input. Suggested patches explain what changed and why before the model is accepted.",
          review: "Check that the current-state model matches reality. Fix missing components or wrong dependencies before Gate 1.",
        },
        planning: {
          title: "Current task: create the target plan",
          body: "The agent captures migration context, computes dependency waves, drafts component plans, estimates effort and cost, and prepares validation, cutover, and rollback strategy.",
          review: "Answer only material questions. The app should avoid asking form-fill questions when it can infer safely.",
        },
        review: {
          title: "Current task: challenge and finalize the plan",
          body: "Deterministic rules and semantic review inspect dependency risks, coexistence, rollback, cost, efficiency, and missing justifications.",
          review: "Review open findings, target architecture, effort/cost assumptions, and patch reasoning before approving Gate 2.",
        },
        exported: {
          title: "Current task: download the approved package",
          body: "The architecture model and migration plan are finalized. Exports reflect the approved plan and documented residual risks.",
          review: "Use the export package for handoff. New changes should start a new study or revision flow.",
        },
      }[status]
    : null;

  return (
    <div className="min-h-screen">
      <NavBar />
      <main className="relative mx-auto max-w-6xl px-4 py-8">
        <div
          aria-hidden
          className="pointer-events-none absolute -top-16 left-1/2 h-72 w-[36rem] -translate-x-1/2 rounded-full bg-grad-pulse-soft opacity-60 blur-3xl animate-float-slow"
        />
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4 animate-fade-up">
          <div>
            <h1 className="font-display text-2xl font-bold tracking-tight text-white">
              {state?.session.name ?? "Loading…"}
            </h1>
            <p className="mt-1 text-xs text-slate-500">
              Model v{state?.model.version} · {state?.model.components.length ?? 0} components
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {state && (
              <Link href={`/sessions/${sessionId}/architecture`} className="btn-secondary !py-2 text-xs">
                Current architecture
              </Link>
            )}
            {state && (
              <Link href={`/sessions/${sessionId}/review-findings`} className="btn-secondary !py-2 text-xs">
                View Review Findings
              </Link>
            )}
            {state && (
              <Link href={`/sessions/${sessionId}/migration-plan`} className="btn-secondary !py-2 text-xs">
                Migration Plan
              </Link>
            )}
            {status && <StatusBadge status={status} pulse />}
          </div>
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
                        {isDone ? "OK" : stage.step}
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
                          isDone ? "bg-gradient-to-r from-brand-500 to-teal-400" : "bg-white/10"
                        }`}
                      />
                    )}
                  </div>
                );
              })}
            </div>
            {stageGuide && (
              <div className="mt-4 grid gap-3 border-t border-white/[0.06] pt-4 md:grid-cols-[1.25fr_1fr]">
                <div>
                  <p className="text-sm font-semibold text-slate-100">{stageGuide.title}</p>
                  <p className="mt-1 text-sm leading-6 text-slate-400">{stageGuide.body}</p>
                </div>
                <div className="rounded-lg border border-brand-400/15 bg-brand-400/[0.045] p-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-brand-200">What to review now</p>
                  <p className="mt-1 text-xs leading-5 text-slate-400">{stageGuide.review}</p>
                </div>
              </div>
            )}
          </div>
        )}

        {error && (
          <p role="alert" className="card mb-4 border-rose-500/30 bg-rose-500/10 text-sm text-rose-300 animate-fade-up">
            {error}
          </p>
        )}

        {!state ? (
          <div className="w-full">
            <div className="card min-h-[62vh] shimmer" />
          </div>
        ) : (
          <>
          <div className="w-full space-y-4">
              {needsMigrationContext && (
                <div className="card-glow border-sky-400/25 bg-sky-500/[0.06] animate-fade-up">
                  <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-sky-200">
                    Gate 1 passed — migration context needed
                  </h3>
                  <p className="mb-3 text-xs leading-5 text-sky-300/80">
                    The architecture model is frozen. Send the migration goal so the Planning Agent can build the
                    target architecture, sequence, cutover, rollback, and review package.
                  </p>
                  <p className="mb-3 text-xs leading-5 text-sky-300/80">
                    After you send the goal, larger models can take a few minutes while planning and review run.
                  </p>
                  <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3 text-xs text-slate-300">
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
                <div id="conversation-panel">
                <ChatPanel
                  sessionId={sessionId}
                  onTurnComplete={refresh}
                  workflowStatus={status}
                  componentCount={state.model.components.length}
                  draft={chatDraft}
                  placeholder={
                    status === "discovery"
                      ? "Describe your existing system, e.g. \"We have a customer portal, backend APIs, PostgreSQL, event streaming, and a data warehouse.\""
                      : status === "planning"
                        ? "Describe your migration goal, e.g. \"Move everything from on-prem to AWS, 4-hour maintenance window is acceptable.\""
                        : "Discuss the plan, e.g. \"Also add Kafka for event streaming\" — I'll ask before adding anything not grounded in what's already here."
                  }
                />
                </div>
              )}

              {status === "discovery" && (
                <div className="rail-card animate-fade-up">
                  <div className="rail-card-bar bg-grad-primary" />
                  <div className="rail-card-body">
                  <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-200">
                    Gate 1 — Accept the architecture
                  </h3>
                  <p className="mb-3 text-xs text-slate-500">
                    Migration planning is unreachable until you accept this model — this is a structural gate,
                    not a suggestion.
                  </p>
                  <div className="mb-3 rounded-lg border border-white/[0.06] bg-white/[0.025] p-3 text-xs leading-5 text-slate-400">
                    Approval means the discovered source architecture is good enough for planning. Later source changes
                    should be treated as explicit revisions because they can change sequencing, risk, effort, and rollback.
                  </div>
                  {state.discovery_confidence && (
                    <div
                      className={`mb-3 rounded-lg border p-3 ${
                        state.discovery_confidence.ready_for_planning
                          ? "border-emerald-400/25 bg-emerald-400/[0.06]"
                          : "border-amber-400/25 bg-amber-400/[0.06]"
                      }`}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p
                          className={`text-xs font-semibold uppercase tracking-wide ${
                            state.discovery_confidence.ready_for_planning ? "text-emerald-200" : "text-amber-200"
                          }`}
                        >
                          Discovery confidence
                        </p>
                        <span
                          className={`badge ${
                            state.discovery_confidence.ready_for_planning
                              ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
                              : "border-amber-400/30 bg-amber-400/10 text-amber-200"
                          }`}
                        >
                          {state.discovery_confidence.ready_for_planning ? "Ready for planning" : "Not ready yet"}
                        </span>
                      </div>
                      <div className="mt-3 grid grid-cols-3 gap-2">
                        <div className="stat-tile">
                          <span className="stat-tile-value">{state.discovery_confidence.completeness_percent}%</span>
                          <span className="stat-tile-label">Completeness</span>
                        </div>
                        <div className="stat-tile">
                          <span className="stat-tile-value">{state.discovery_confidence.blocking_unknowns}</span>
                          <span className="stat-tile-label">Blocking unknowns</span>
                        </div>
                        <div className="stat-tile">
                          <span className="stat-tile-value">{state.discovery_confidence.high_risk_assumptions}</span>
                          <span className="stat-tile-label">High-risk assumptions</span>
                        </div>
                      </div>
                    </div>
                  )}
                  {gate1Summary && (
                    <div className="mb-3 rounded-lg border border-sky-400/20 bg-sky-500/[0.05] p-3">
                      <p className="text-xs font-semibold uppercase tracking-wide text-sky-200">
                        Understanding before Gate 1
                      </p>
                      <p className="mt-2 text-sm leading-6 text-slate-300">{gate1Summary.headline}</p>
                      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                        {gate1Summary.items.map((item) => (
                          <div key={item.label} className="rounded-md border border-white/[0.06] bg-white/[0.025] p-2">
                            <dt className="font-medium text-slate-200">{item.label}</dt>
                            <dd className="mt-1 leading-5 text-slate-400">{item.value}</dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                  )}
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={gateBusy || state.model.components.length === 0}
                    onClick={() => setShowGate1Confirm(true)}
                  >
                    {gateBusy ? "Accepting…" : "Accept architecture model"}
                  </button>
                  </div>
                </div>
              )}

              {status === "review" && (
                <div className="rail-card animate-fade-up">
                  <div className={`rail-card-bar ${hasBlockingFindings ? "bg-amber-400" : "bg-grad-pulse"}`} />
                  <div className="rail-card-body">
                  <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-200">
                    Gate 2 — Approve the plan
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
                  <div className="mb-3 rounded-lg border border-white/[0.06] bg-white/[0.025] p-3 text-xs leading-5 text-slate-400">
                    Approval means you accept the target architecture, migration waves, effort and cost assumptions,
                    validation plan, cutover strategy, rollback approach, and any documented residual risks.
                  </div>
                  <button type="button" className="btn-primary" disabled={gateBusy} onClick={handleApprovePlan}>
                    {gateBusy ? "Approving…" : "Approve final plan"}
                  </button>
                  </div>
                </div>
              )}
              {state.migration_context && (
                <div className="card">
                  <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-200">
                    Migration context
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
          </div>
          <div className="mt-6 animate-fade-up">
            <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-200">
              Patch audit trail
            </h3>
            <AuditTrailPanel records={auditRecords} onReviewPatch={status === "exported" ? undefined : handleReviewPatch} />
          </div>
          </>
        )}
        {showGate1Confirm && state && gate1Summary && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 px-4 backdrop-blur-sm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="gate1-confirm-title"
          >
            <div className="w-full max-w-2xl rounded-lg border border-white/10 bg-slate-950 p-5 shadow-2xl">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 id="gate1-confirm-title" className="text-base font-semibold text-white">
                    Confirm Gate 1 acceptance
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-slate-400">
                    Accepting freezes this source architecture for planning. Review this summary once before
                    moving forward.
                  </p>
                </div>
                <button
                  type="button"
                  className="rounded-md border border-white/10 px-2 py-1 text-sm text-slate-400 hover:text-white"
                  onClick={() => setShowGate1Confirm(false)}
                  disabled={gateBusy}
                  aria-label="Close confirmation"
                >
                  x
                </button>
              </div>

              <div className="mt-4 rounded-lg border border-sky-400/20 bg-sky-500/[0.05] p-3">
                <p className="text-sm leading-6 text-slate-300">{gate1Summary.headline}</p>
                <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                  {gate1Summary.items.map((item) => (
                    <div key={item.label} className="rounded-md border border-white/[0.06] bg-white/[0.025] p-2">
                      <dt className="font-medium text-slate-200">{item.label}</dt>
                      <dd className="mt-1 leading-5 text-slate-400">{item.value}</dd>
                    </div>
                  ))}
                </dl>
              </div>

              {state.model.open_questions.some((question) => !question.resolved) && (
                <p className="mt-3 rounded-md border border-amber-400/20 bg-amber-500/[0.06] p-3 text-xs leading-5 text-amber-200">
                  There are still unresolved open questions. Accept only if this source model is good enough
                  for a first planning pass.
                </p>
              )}

              <div className="mt-5 flex flex-wrap justify-end gap-2">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setShowGate1Confirm(false)}
                  disabled={gateBusy}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={handleAcceptModel}
                  disabled={gateBusy || state.model.components.length === 0}
                >
                  {gateBusy ? "Accepting..." : "Confirm and accept Gate 1"}
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function buildGate1Summary(model: ArchitectureModel): {
  headline: string;
  items: { label: string; value: string }[];
} {
  const components = model.components;
  const dependencies = model.dependencies;
  const unresolvedQuestions = model.open_questions.filter((question) => !question.resolved);
  const componentNames = components.map((component) => component.name);
  const criticalityCounts = components.reduce<Record<string, number>>((counts, component) => {
    const key = component.criticality || "unclassified";
    counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {});
  const environments = Array.from(new Set(components.map((component) => component.environment))).filter(Boolean);
  const tierSummary = Object.entries(criticalityCounts)
    .map(([tier, count]) => `${count} ${tier}`)
    .join(", ");

  return {
    headline:
      components.length === 0
        ? "No source architecture components have been captured yet."
        : `The source model currently has ${components.length} component${components.length === 1 ? "" : "s"} and ${dependencies.length} dependenc${dependencies.length === 1 ? "y" : "ies"}.`,
    items: [
      {
        label: "Components",
        value: componentNames.length > 0 ? formatList(componentNames, 6) : "None captured yet",
      },
      {
        label: "Dependencies",
        value:
          dependencies.length > 0
            ? `${dependencies.length} relationship${dependencies.length === 1 ? "" : "s"} captured for planning waves and risk review`
            : "No dependencies captured yet",
      },
      {
        label: "Environment",
        value:
          environments.length > 0
            ? environments.map((environment) => environment.replace(/_/g, " ")).join(", ")
            : "Unknown",
      },
      {
        label: "Criticality",
        value: tierSummary || "No criticality classifications captured yet",
      },
      {
        label: "Open Questions",
        value:
          unresolvedQuestions.length > 0
            ? `${unresolvedQuestions.length} unresolved question${unresolvedQuestions.length === 1 ? "" : "s"} remain`
            : "No unresolved discovery questions",
      },
      {
        label: "Assumptions",
        value:
          model.assumptions.length > 0
            ? `${model.assumptions.length} assumption${model.assumptions.length === 1 ? "" : "s"} recorded`
            : "No assumptions recorded",
      },
    ],
  };
}

function formatList(values: string[], limit: number): string {
  if (values.length <= limit) return values.join(", ");
  return `${values.slice(0, limit).join(", ")} and ${values.length - limit} more`;
}
