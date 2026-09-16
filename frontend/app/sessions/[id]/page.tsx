"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowRight,
  Bot,
  Check,
  Expand,
  FileDown,
  Flag,
  GitBranch,
  GripVertical,
  Layers3,
  LogOut,
  Maximize2,
  PanelRightClose,
  ShieldCheck,
  User as UserIcon,
  Workflow,
} from "lucide-react";

import { acceptModel, ApiError, approvePlan, getAudit, getFindings, getSessionState } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type {
  ArchitectureModel,
  Component,
  Finding,
  FindingSeverity,
  PatchAuditEntry,
  RiskSeverity,
  SessionState,
  SessionStatus,
  WorkloadType,
} from "@/lib/types";
import { AuditTrailPanel } from "@/components/AuditTrailPanel";
import { ChatPanel, type ChatDraft } from "@/components/ChatPanel";
import { ExportButtons } from "@/components/ExportButtons";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const STAGES: { key: SessionStatus; label: string; step: string }[] = [
  { key: "discovery", label: "Discovery", step: "1" },
  { key: "planning", label: "Planning", step: "2" },
  { key: "review", label: "Review", step: "3" },
  { key: "exported", label: "Exported", step: "4" },
];

const STAGE_GUIDE: Record<SessionStatus, { title: string; body: string; review: string }> = {
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
};

const WORKLOAD_KIND_LABEL: Record<WorkloadType, string> = {
  web_service: "WEB",
  api_service: "API",
  batch_job: "JOB",
  database: "DB",
  message_queue: "MQ",
  cache: "CACHE",
  ml_inference: "ML",
  ml_training: "MLT",
  data_pipeline: "ETL",
  data_warehouse: "WH",
  storage: "STORE",
  load_balancer: "LB",
  cdn: "CDN",
  third_party_integration: "3RD",
  other: "SVC",
};

const RISK_RANK: Record<RiskSeverity, number> = { critical: 3, high: 2, medium: 1, low: 0 };
const SEVERITY_RANK: Record<FindingSeverity, number> = { error: 2, warning: 1, info: 0 };

export default function SessionWorkspacePage() {
  const { user, loading: authLoading, logout } = useRequireAuth();
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const sessionId = params.id;

  const [state, setState] = useState<SessionState | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [auditRecords, setAuditRecords] = useState<PatchAuditEntry[]>([]);
  const [chatDraft, setChatDraft] = useState<ChatDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gateBusy, setGateBusy] = useState(false);
  const [showGate1Confirm, setShowGate1Confirm] = useState(false);
  const [selectedComponentId, setSelectedComponentId] = useState<string | null>(null);
  const [agentTab, setAgentTab] = useState<SessionStatus>("discovery");
  const [panelWidth, setPanelWidth] = useState(380);
  const [chatFocusMode, setChatFocusMode] = useState(false);

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

  useEffect(() => {
    if (state?.session.status) setAgentTab(state.session.status);
  }, [state?.session.status]);

  useEffect(() => {
    if (!state) return;
    if (!selectedComponentId || !state.model.components.some((c) => c.id === selectedComponentId)) {
      setSelectedComponentId(state.model.components[0]?.id ?? null);
    }
  }, [state, selectedComponentId]);

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
  }

  const status = state?.session.status;
  const model = state?.model;
  const plan = state?.plan ?? null;
  const hasBlockingFindings = findings.some((f) => f.severity === "error" && f.resolution_status === "open");
  const gate1Summary = state ? buildGate1Summary(state.model) : null;
  const currentStageIndex = STAGES.findIndex((s) => s.key === status);

  const selectedComponent: Component | null = useMemo(
    () => model?.components.find((c) => c.id === selectedComponentId) ?? null,
    [model, selectedComponentId]
  );

  const selectedComponentDeps = useMemo(() => {
    if (!model || !selectedComponent) return [];
    const byId = new Map(model.components.map((c) => [c.id, c.name]));
    return model.dependencies
      .filter((d) => d.source_id === selectedComponent.id || d.target_id === selectedComponent.id)
      .map((d) => {
        const outgoing = d.source_id === selectedComponent.id;
        const partnerId = outgoing ? d.target_id : d.source_id;
        return `${outgoing ? "→" : "←"} ${byId.get(partnerId) ?? partnerId}`;
      });
  }, [model, selectedComponent]);

  const selectedComponentRisk = useMemo(() => {
    if (!plan || !selectedComponent) return null;
    const related = plan.risks.filter((r) => r.related_component_ids.includes(selectedComponent.id));
    if (related.length === 0) return null;
    return related.reduce((worst, r) => (RISK_RANK[r.severity] > RISK_RANK[worst.severity] ? r : worst));
  }, [plan, selectedComponent]);

  const selectedComponentMapping = useMemo(
    () => plan?.component_mappings.find((m) => m.component_id === selectedComponent?.id) ?? null,
    [plan, selectedComponent]
  );

  const selectedComponentPlan = useMemo(
    () => plan?.component_plans.find((p) => p.component_id === selectedComponent?.id) ?? null,
    [plan, selectedComponent]
  );

  const topFindings = useMemo(
    () =>
      [...findings]
        .filter((f) => f.resolution_status === "open")
        .sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity])
        .slice(0, 3),
    [findings]
  );

  const riskCounts = useMemo(() => {
    const counts: Record<RiskSeverity, number> = { critical: 0, high: 0, medium: 0, low: 0 };
    for (const r of plan?.risks ?? []) counts[r.severity] += 1;
    return counts;
  }, [plan]);

  const environmentSummary = gate1Summary?.items.find((i) => i.label === "Environment") ?? null;
  const criticalitySummary = gate1Summary?.items.find((i) => i.label === "Criticality") ?? null;

  const modeledBadge = state
    ? plan
      ? `${plan.component_mappings.length} of ${model?.components.length ?? 0} nodes mapped`
      : `${state.discovery_confidence.completeness_percent}% complete`
    : null;

  if (authLoading || !user) return null;

  return (
    <div className="flex h-screen min-h-0 bg-atlas-mist font-body text-foreground antialiased dark:bg-background">
      {/* Left icon rail */}
      <aside className="flex w-14 shrink-0 flex-col items-center gap-1 border-r border-border bg-[oklch(0.19_0.025_210)] py-3 text-[oklch(0.85_0.02_210)]">
        <div className="mb-2 grid size-9 place-items-center rounded-md bg-atlas-teal font-display text-sm font-semibold text-white">
          A
        </div>
        <RailIconButton label="Workspace" active>
          <Layers3 className="size-4" />
        </RailIconButton>
        <RailIconButton label="Current architecture" onClick={() => router.push(`/sessions/${sessionId}/architecture`)}>
          <Workflow className="size-4" />
        </RailIconButton>
        <RailIconButton label="Migration plan" onClick={() => router.push(`/sessions/${sessionId}/migration-plan`)}>
          <GitBranch className="size-4" />
        </RailIconButton>
        <RailIconButton label="Review findings" onClick={() => router.push(`/sessions/${sessionId}/review-findings`)}>
          <Flag className="size-4" />
        </RailIconButton>
        <div className="flex-1" />
        {user.is_admin && (
          <RailIconButton label="Admin" onClick={() => router.push("/admin")}>
            <ShieldCheck className="size-4" />
          </RailIconButton>
        )}
        <RailIconButton label="Account" onClick={() => router.push("/account")}>
          <UserIcon className="size-4" />
        </RailIconButton>
        <RailIconButton
          label="Sign out"
          onClick={() => {
            logout();
            router.replace("/login");
          }}
        >
          <LogOut className="size-4" />
        </RailIconButton>
        <div className="mt-1 grid size-8 place-items-center rounded-full bg-atlas-mint text-[10px] font-bold text-[oklch(0.19_0.025_210)]">
          {(user.email || "?").slice(0, 2).toUpperCase()}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Header: session identity + stage stepper */}
        <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border bg-card px-5">
          <Link href="/sessions" className="flex items-center gap-2">
            <span className="agent-pulse size-2 rounded-full bg-atlas-teal" />
            <span className="font-display text-sm font-semibold text-foreground">{state?.session.name ?? "Migration workspace"}</span>
            {state?.migration_context && (
              <span className="text-[11px] text-muted-foreground">
                {state.migration_context.source_environment.replace(/_/g, " ")}
                <ArrowRight className="mx-1 inline size-3" />
                {state.migration_context.target_environment.replace(/_/g, " ")}
              </span>
            )}
          </Link>
          <div className="h-4 w-px bg-border" />
          <nav className="flex flex-1 items-center justify-center gap-1" aria-label="Migration stages">
            {STAGES.map((stage, index) => {
              const isActive = index === currentStageIndex;
              const isDone = index < currentStageIndex;
              return (
                <div key={stage.key} className="flex items-center gap-2">
                  <span
                    className={cn(
                      "flex h-7 items-center gap-2 rounded-md px-2 text-xs",
                      isActive && "bg-atlas-amber-soft text-atlas-amber"
                    )}
                  >
                    <span
                      className={cn(
                        "grid size-5 place-items-center rounded-md text-[10px] font-bold",
                        isActive ? "bg-atlas-amber text-white" : isDone ? "bg-atlas-teal text-white" : "bg-muted text-muted-foreground"
                      )}
                    >
                      {isDone ? <Check className="size-3" /> : stage.step}
                    </span>
                    <span className={cn(isActive ? "font-semibold text-foreground" : isDone ? "font-semibold text-atlas-teal" : "text-muted-foreground")}>
                      {stage.label}
                    </span>
                  </span>
                  {index < STAGES.length - 1 && <span className="hidden h-px w-8 bg-border md:block" />}
                </div>
              );
            })}
          </nav>
          <div className="flex items-center gap-3">
            {modeledBadge && (
              <Badge variant="secondary" className="hidden sm:inline-flex">
                {modeledBadge}
              </Badge>
            )}
            <ThemeToggle />
          </div>
        </header>

        {error && (
          <p role="alert" className="mx-5 mt-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        )}

        {!state ? (
          <div className="flex-1 p-5">
            <div className="h-full animate-pulse rounded-xl bg-muted" />
          </div>
        ) : (
          <div className="flex min-h-0 flex-1">
            {/* Main column */}
            <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto p-5">
              {status === "exported" ? (
                <Card className="flex flex-col items-center gap-3 py-10 text-center blueprint-reveal">
                  <span className="flex h-14 w-14 items-center justify-center rounded-full bg-atlas-teal-soft text-3xl ring-1 ring-atlas-teal/30">
                    ✅
                  </span>
                  <div>
                    <h3 className="font-display text-lg font-semibold text-foreground">Export ready</h3>
                    <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                      This plan has been reviewed, approved, and finalized — no further turns are accepted. Download the
                      full 10-deliverable package below.
                    </p>
                  </div>
                  <div className="mt-2 w-full max-w-sm">
                    <ExportButtons sessionId={sessionId} />
                  </div>
                </Card>
              ) : (
                <>
                  {/* Architecture canvas: real components as a node grid */}
                  <Card className="blueprint-reveal flex min-h-[360px] flex-col overflow-hidden p-0">
                    <div className="flex items-center justify-between border-b border-border px-4 py-3">
                      <div className="flex items-center gap-2">
                        <h1 className="font-display text-sm font-semibold text-foreground">Current architecture</h1>
                        <span className="text-[11px] text-muted-foreground">source: as discovered</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge variant="secondary">{model?.components.length ?? 0} nodes</Badge>
                        <Badge variant="secondary">{model?.dependencies.length ?? 0} dependencies</Badge>
                        <Button asChild variant="ghost" size="icon" className="size-7 text-muted-foreground" aria-label="Open full diagram">
                          <Link href={`/sessions/${sessionId}/architecture`}>
                            <Expand className="size-4" />
                          </Link>
                        </Button>
                      </div>
                    </div>
                    <div className="blueprint-grid flex-1 p-5">
                      {model && model.components.length > 0 ? (
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
                          {model.components.map((c) => (
                            <ComponentNodeCard
                              key={c.id}
                              component={c}
                              selected={selectedComponentId === c.id}
                              onClick={() => setSelectedComponentId(c.id)}
                            />
                          ))}
                        </div>
                      ) : (
                        <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
                          <span className="text-2xl opacity-50">🏗️</span>
                          Describe your system in the conversation to start building the model.
                        </div>
                      )}
                      {(environmentSummary || criticalitySummary) && (
                        <div className="mt-4 grid gap-3 sm:grid-cols-2">
                          {environmentSummary && (
                            <div className="rounded-lg bg-atlas-sky p-2.5">
                              <div className="text-[10px] font-bold tracking-wide text-muted-foreground">ENVIRONMENT</div>
                              <div className="mt-0.5 text-xs text-foreground">{environmentSummary.value}</div>
                            </div>
                          )}
                          {criticalitySummary && (
                            <div className="rounded-lg bg-atlas-mint p-2.5">
                              <div className="text-[10px] font-bold tracking-wide text-muted-foreground">CRITICALITY</div>
                              <div className="mt-0.5 text-xs text-foreground">{criticalitySummary.value}</div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </Card>

                  {/* Selected node detail */}
                  {selectedComponent && (
                    <Card className="blueprint-reveal p-4">
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                        <div className="flex items-center gap-2">
                          <span className="size-2.5 rounded-sm bg-atlas-amber" />
                          <h2 className="font-display text-sm font-semibold text-foreground">
                            {selectedComponent.name}
                            {selectedComponent.technology ? ` · ${selectedComponent.technology}` : ""}
                          </h2>
                        </div>
                        {selectedComponentMapping && (
                          <span className="text-[11px] text-muted-foreground">target: {selectedComponentMapping.target_description}</span>
                        )}
                        {selectedComponentRisk && (
                          <Badge variant={selectedComponentRisk.severity === "low" ? "secondary" : selectedComponentRisk.severity === "critical" ? "destructive" : selectedComponentRisk.severity === "high" ? "coral" : "amber"}>
                            {selectedComponentRisk.severity} risk
                          </Badge>
                        )}
                        <Badge variant="secondary">{selectedComponent.criticality || "unclassified"}</Badge>
                        {status === "discovery" && (
                          <div className="ml-auto flex items-center gap-2">
                            <Button
                              size="sm"
                              className="bg-atlas-teal text-white hover:bg-atlas-teal/90"
                              disabled={gateBusy || (model?.components.length ?? 0) === 0}
                              onClick={() => setShowGate1Confirm(true)}
                            >
                              Accept architecture model <Check className="size-3.5" />
                            </Button>
                          </div>
                        )}
                        {status !== "discovery" && (
                          <Badge variant="teal" className="ml-auto">
                            Model accepted
                          </Badge>
                        )}
                      </div>
                      <div className="mt-3 grid gap-3 border-t border-border pt-3 text-xs md:grid-cols-3">
                        <p className="text-muted-foreground">
                          <span className="font-semibold text-foreground">Role</span>
                          <br />
                          {selectedComponent.description || "No description captured yet."}
                        </p>
                        <p className="text-muted-foreground">
                          <span className="font-semibold text-foreground">Dependencies</span>
                          <br />
                          {selectedComponentDeps.length > 0 ? selectedComponentDeps.join(" · ") : "None captured"}
                        </p>
                        <p className="text-muted-foreground">
                          <span className="font-semibold text-foreground">Approach</span>
                          <br />
                          {selectedComponentPlan?.steps[0] ?? "Not planned yet — accept the model and provide a migration goal."}
                        </p>
                      </div>
                    </Card>
                  )}

                  {/* Risks & findings strip */}
                  {plan && (
                    <section className="blueprint-reveal">
                      <div className="mb-3 flex items-center justify-between">
                        <h2 className="font-display text-[15px] font-semibold text-foreground">Risks &amp; findings</h2>
                        <Link href={`/sessions/${sessionId}/review-findings`} className="text-[11px] font-medium text-atlas-teal hover:underline">
                          {findings.length} items — view all →
                        </Link>
                      </div>
                      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
                        {topFindings.length > 0 ? (
                          topFindings.map((f) => <FindingCard key={f.id} finding={f} />)
                        ) : (
                          <p className="text-sm text-muted-foreground xl:col-span-3">No open findings.</p>
                        )}
                      </div>
                    </section>
                  )}

                  {needsMigrationContext && (
                    <Card className="border-atlas-sky/40 bg-atlas-sky/20 p-5 blueprint-reveal">
                      <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-foreground">
                        Gate 1 passed — migration context needed
                      </h3>
                      <p className="mb-3 text-xs leading-5 text-muted-foreground">
                        The architecture model is frozen. Send the migration goal so the Planning Agent can build the
                        target architecture, sequence, cutover, rollback, and review package.
                      </p>
                      <div className="rounded-lg border border-border bg-background/60 p-3 text-xs text-foreground">
                        <div className="font-medium">Include these details:</div>
                        <ul className="mt-1 list-disc space-y-1 pl-5 text-muted-foreground">
                          <li>source environment and target environment</li>
                          <li>target platform or cloud services you prefer</li>
                          <li>downtime tolerance or maintenance window</li>
                          <li>constraints such as compliance, timeline, budget, or services that must remain unchanged</li>
                        </ul>
                      </div>
                    </Card>
                  )}

                  {status === "discovery" && (
                    <div className="rail-card blueprint-reveal">
                      <div className="rail-card-bar bg-atlas-teal" />
                      <div className="rail-card-body">
                        <h3 className="mb-1 text-sm font-semibold text-foreground">Gate 1 — Accept the architecture</h3>
                        <p className="mb-3 text-xs text-muted-foreground">
                          Migration planning is unreachable until you accept this model — this is a structural gate, not
                          a suggestion.
                        </p>
                        {state.discovery_confidence && (
                          <div
                            className={cn(
                              "mb-3 rounded-lg border p-3",
                              state.discovery_confidence.ready_for_planning
                                ? "border-atlas-teal/30 bg-atlas-teal-soft"
                                : "border-atlas-amber/30 bg-atlas-amber-soft"
                            )}
                          >
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <p
                                className={cn(
                                  "text-xs font-semibold uppercase tracking-wide",
                                  state.discovery_confidence.ready_for_planning ? "text-atlas-teal" : "text-atlas-amber"
                                )}
                              >
                                Discovery confidence
                              </p>
                              <Badge variant={state.discovery_confidence.ready_for_planning ? "teal" : "amber"}>
                                {state.discovery_confidence.ready_for_planning ? "Ready for planning" : "Not ready yet"}
                              </Badge>
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
                          <div className="mb-3 rounded-lg border border-atlas-sky/40 bg-atlas-sky/20 p-3">
                            <p className="text-xs font-semibold uppercase tracking-wide text-foreground">Understanding before Gate 1</p>
                            <p className="mt-2 text-sm leading-6 text-muted-foreground">{gate1Summary.headline}</p>
                            <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                              {gate1Summary.items.map((item) => (
                                <div key={item.label} className="rounded-md border border-border bg-background/60 p-2">
                                  <dt className="font-medium text-foreground">{item.label}</dt>
                                  <dd className="mt-1 leading-5 text-muted-foreground">{item.value}</dd>
                                </div>
                              ))}
                            </dl>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {status === "review" && (
                    <div className="rail-card blueprint-reveal">
                      <div className={cn("rail-card-bar", hasBlockingFindings ? "bg-atlas-amber" : "bg-atlas-teal")} />
                      <div className="rail-card-body">
                        <h3 className="mb-1 text-sm font-semibold text-foreground">Gate 2 — Approve the plan</h3>
                        {hasBlockingFindings ? (
                          <p className="mb-3 text-xs text-atlas-amber">
                            There are still open error-severity findings above. You can approve anyway — unresolved
                            findings ship as documented risks — but check them first.
                          </p>
                        ) : (
                          <p className="mb-3 text-xs text-muted-foreground">Review is complete with no open blocking findings.</p>
                        )}
                        <Button className="bg-atlas-teal text-white hover:bg-atlas-teal/90" disabled={gateBusy} onClick={handleApprovePlan}>
                          {gateBusy ? "Approving…" : "Approve final plan"}
                        </Button>
                      </div>
                    </div>
                  )}

                  {state.migration_context && (
                    <Card className="p-5">
                      <h3 className="mb-3 text-sm font-semibold text-foreground">Migration context</h3>
                      <dl className="grid grid-cols-2 gap-y-2 text-sm">
                        <dt className="text-muted-foreground">Source</dt>
                        <dd className="text-foreground">{state.migration_context.source_environment}</dd>
                        <dt className="text-muted-foreground">Target</dt>
                        <dd className="text-foreground">
                          {state.migration_context.target_environment} — {state.migration_context.target_platform_description}
                        </dd>
                        <dt className="text-muted-foreground">Downtime tolerance</dt>
                        <dd className="text-foreground">{state.migration_context.downtime_tolerance.replace(/_/g, " ")}</dd>
                      </dl>
                      {state.migration_context.constraints.length > 0 && (
                        <ul className="mt-3 space-y-1 border-t border-border pt-3 text-xs text-muted-foreground">
                          {state.migration_context.constraints.map((c, i) => (
                            <li key={i} className="flex gap-2">
                              <span className="text-atlas-teal">•</span> {c}
                            </li>
                          ))}
                        </ul>
                      )}
                    </Card>
                  )}

                  <div>
                    <h3 className="mb-2 text-sm font-semibold text-foreground">Patch audit trail</h3>
                    <AuditTrailPanel records={auditRecords} onReviewPatch={handleReviewPatch} />
                  </div>
                </>
              )}
            </main>

            {/* Right agent rail */}
            <aside
              style={chatFocusMode ? undefined : { width: `${panelWidth}px` }}
              className={cn(
                "relative flex shrink-0 flex-col border-l border-border bg-card transition-[width] duration-200",
                chatFocusMode && "fixed inset-4 z-40 w-auto border shadow-2xl"
              )}
            >
              <div className="flex items-center justify-between border-b border-border px-3 pt-3">
                <div className="flex text-[11px] font-semibold">
                  {STAGES.slice(0, 3).map((s) => (
                    <button
                      key={s.key}
                      type="button"
                      className={cn(
                        "h-7 rounded-t px-2.5 text-[11px]",
                        agentTab === s.key ? "bg-muted text-foreground" : "text-muted-foreground"
                      )}
                      onClick={() => setAgentTab(s.key)}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-muted-foreground"
                  onClick={() => setChatFocusMode((v) => !v)}
                  aria-label={chatFocusMode ? "Collapse conversation" : "Expand conversation"}
                >
                  {chatFocusMode ? <PanelRightClose className="size-4" /> : <Maximize2 className="size-4" />}
                </Button>
              </div>
              <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                <GripVertical className="size-3 text-muted-foreground" />
                <label className="flex-1 text-[10px] uppercase tracking-[0.15em] text-muted-foreground" htmlFor="agent-width">
                  Panel width
                </label>
                <input
                  id="agent-width"
                  type="range"
                  min="320"
                  max="560"
                  step="10"
                  value={panelWidth}
                  onChange={(e) => setPanelWidth(Number(e.target.value))}
                  className="w-24 accent-atlas-teal"
                />
                <span className="w-8 text-right font-mono text-[10px] text-muted-foreground">{panelWidth}</span>
              </div>
              <div className="flex min-h-0 flex-1 flex-col px-3">
                <div className="flex items-center gap-2 py-3">
                  <div className="grid size-7 place-items-center rounded-md bg-atlas-teal-soft text-atlas-teal">
                    <Bot className="size-4" />
                  </div>
                  <div>
                    <h2 className="font-display text-xs font-semibold text-foreground">{STAGE_GUIDE[agentTab].title.replace("Current task: ", "")}</h2>
                    <p className="text-[10px] text-muted-foreground">advisory · live conversation</p>
                  </div>
                </div>
                <p className="pb-2 text-[11px] leading-snug text-muted-foreground">{STAGE_GUIDE[agentTab].body}</p>
                <div className="min-h-0 flex-1">
                  <ChatPanel
                    embedded
                    sessionId={sessionId}
                    onTurnComplete={refresh}
                    workflowStatus={status}
                    componentCount={model?.components.length}
                    draft={chatDraft}
                    placeholder={
                      status === "discovery"
                        ? 'Describe your existing system, e.g. "We have a customer portal, backend APIs, PostgreSQL, event streaming, and a data warehouse."'
                        : status === "planning"
                          ? 'Describe your migration goal, e.g. "Move everything from on-prem to AWS, 4-hour maintenance window is acceptable."'
                          : "Discuss the plan, e.g. \"Also add Kafka for event streaming\" — I'll ask before adding anything not grounded in what's already here."
                    }
                  />
                </div>
              </div>
              {findings.length > 0 && (
                <div className="border-t border-border px-3 py-3">
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="font-display text-xs font-semibold text-foreground">Findings</h3>
                    <span className="text-[10px] text-muted-foreground">{findings.filter((f) => f.resolution_status === "open").length} open</span>
                  </div>
                  <div className="space-y-1.5">
                    {topFindings.map((f) => (
                      <div
                        key={f.id}
                        className={cn(
                          "flex items-start gap-2 rounded p-2 text-[11px] leading-tight",
                          f.severity === "error" ? "bg-atlas-coral-soft" : f.severity === "warning" ? "bg-atlas-amber-soft" : "bg-atlas-teal-soft"
                        )}
                      >
                        <span
                          className={cn(
                            "mt-1 size-1.5 shrink-0 rounded-full",
                            f.severity === "error" ? "bg-atlas-coral" : f.severity === "warning" ? "bg-atlas-amber" : "bg-atlas-teal"
                          )}
                        />
                        <span className="text-foreground">{f.message}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </aside>
          </div>
        )}

        {/* Footer: aggregate stats + primary CTA */}
        {state && status !== "exported" && (
          <footer className="flex h-16 shrink-0 items-center gap-5 border-t border-border bg-[oklch(0.19_0.025_210)] px-5 text-[oklch(0.85_0.02_210)]">
            <FooterStat label="Plan" value={plan ? `${plan.component_plans.length}/${model?.components.length ?? 0}` : "—"} suffix="components planned" />
            <div className="h-8 w-px bg-white/10" />
            <FooterStat
              label="Cost"
              value={plan?.cost_summary ? `$${plan.cost_summary.total_monthly_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
              suffix="est. / month"
            />
            <div className="h-8 w-px bg-white/10" />
            <FooterStat
              label="Risk"
              value={plan ? `${riskCounts.critical + riskCounts.high} high` : "—"}
              suffix={plan ? `${riskCounts.medium} medium · ${riskCounts.low} low` : "not planned yet"}
              accent={riskCounts.critical + riskCounts.high > 0}
            />
            <div className="ml-auto flex items-center gap-2">
              {status === "discovery" && (
                <Button
                  size="sm"
                  className="bg-atlas-teal text-white hover:bg-atlas-teal/90"
                  disabled={gateBusy || (model?.components.length ?? 0) === 0}
                  onClick={() => setShowGate1Confirm(true)}
                >
                  Accept architecture model <ArrowRight className="size-3.5" />
                </Button>
              )}
              {status === "review" && (
                <Button size="sm" className="bg-atlas-teal text-white hover:bg-atlas-teal/90" disabled={gateBusy} onClick={handleApprovePlan}>
                  Approve final plan <ArrowRight className="size-3.5" />
                </Button>
              )}
              {status === "planning" && (
                <span className="text-xs text-white/60">
                  {needsMigrationContext ? "Send your migration goal in the conversation to continue" : "Planning in progress…"}
                </span>
              )}
            </div>
          </footer>
        )}
        {state && status === "exported" && (
          <footer className="flex h-16 shrink-0 items-center gap-5 border-t border-border bg-[oklch(0.19_0.025_210)] px-5 text-[oklch(0.85_0.02_210)]">
            <FooterStat
              label="Cost"
              value={plan?.cost_summary ? `$${plan.cost_summary.total_monthly_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
              suffix="est. / month"
            />
            <div className="ml-auto">
              <Button
                asChild
                variant="outline"
                size="sm"
                className="border-white/15 bg-transparent text-[oklch(0.85_0.02_210)] hover:bg-white/5 hover:text-white"
              >
                <a href="#" onClick={(e) => e.preventDefault()}>
                  <FileDown className="size-3.5" /> See export panel above
                </a>
              </Button>
            </div>
          </footer>
        )}
      </div>

      <Dialog open={showGate1Confirm && !!state && !!gate1Summary} onOpenChange={(open) => !gateBusy && setShowGate1Confirm(open)}>
        <DialogContent className="max-w-2xl">
          {state && gate1Summary && (
            <>
              <DialogHeader>
                <DialogTitle>Confirm Gate 1 acceptance</DialogTitle>
                <p className="mt-1 text-sm leading-6 text-muted-foreground">
                  Accepting freezes this source architecture for planning. Review this summary once before moving
                  forward.
                </p>
              </DialogHeader>

              <div className="rounded-lg border border-atlas-sky/40 bg-atlas-sky/20 p-3">
                <p className="text-sm leading-6 text-foreground">{gate1Summary.headline}</p>
                <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                  {gate1Summary.items.map((item) => (
                    <div key={item.label} className="rounded-md border border-border bg-background/60 p-2">
                      <dt className="font-medium text-foreground">{item.label}</dt>
                      <dd className="mt-1 leading-5 text-muted-foreground">{item.value}</dd>
                    </div>
                  ))}
                </dl>
              </div>

              {state.model.open_questions.some((question) => !question.resolved) && (
                <p className="rounded-md border border-atlas-amber/30 bg-atlas-amber-soft p-3 text-xs leading-5 text-atlas-amber">
                  There are still unresolved open questions. Accept only if this source model is good enough for a
                  first planning pass.
                </p>
              )}

              <DialogFooter>
                <Button variant="outline" onClick={() => setShowGate1Confirm(false)} disabled={gateBusy}>
                  Cancel
                </Button>
                <Button
                  className="bg-atlas-teal text-white hover:bg-atlas-teal/90"
                  onClick={handleAcceptModel}
                  disabled={gateBusy || state.model.components.length === 0}
                >
                  {gateBusy ? "Accepting..." : "Confirm and accept Gate 1"}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function RailIconButton({
  label,
  active,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={onClick}
          className={cn(
            "size-9 rounded-md text-[oklch(0.7_0.02_210)] hover:bg-white/5 hover:text-[oklch(0.96_0.012_210)]",
            active && "bg-white/5 text-atlas-teal"
          )}
          aria-label={label}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

function ComponentNodeCard({
  component,
  selected,
  onClick,
}: {
  component: Component;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "relative flex flex-col items-center gap-0.5 rounded-md p-3 text-center ring-1 transition hover:-translate-y-0.5",
        selected ? "bg-atlas-amber-soft ring-atlas-amber ring-2" : "bg-atlas-teal-soft ring-atlas-teal/40"
      )}
      aria-label={`Inspect ${component.name}`}
    >
      {selected && <span className="agent-pulse absolute -right-1.5 -top-1.5 size-2.5 rounded-full bg-atlas-amber" />}
      <span className={cn("text-[10px] font-bold tracking-wide", selected ? "text-atlas-amber" : "text-atlas-teal")}>
        {WORKLOAD_KIND_LABEL[component.workload_type]}
      </span>
      <span className="font-display text-xs font-medium text-foreground">{component.name}</span>
      <span className="text-[10px] text-muted-foreground">{component.technology || component.environment}</span>
    </button>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  const tone = finding.severity === "error" ? "coral" : finding.severity === "warning" ? "amber" : "teal";
  return (
    <div
      className={cn(
        "rounded-xl border-l-4 border-border bg-card p-4 text-left",
        tone === "coral" ? "border-l-atlas-coral" : tone === "amber" ? "border-l-atlas-amber" : "border-l-atlas-teal"
      )}
    >
      <div className="flex items-center gap-2">
        <Badge variant={tone}>{finding.severity}</Badge>
        <span className="text-[11px] text-muted-foreground">{finding.rule_id ?? "LLM critic"}</span>
      </div>
      <p className="mt-2 text-[13px] font-semibold text-foreground">{finding.message}</p>
      {finding.suggested_fix && <p className="mt-1 text-[12px] text-muted-foreground">{finding.suggested_fix}</p>}
    </div>
  );
}

function FooterStat({ label, value, suffix, accent }: { label: string; value: string; suffix: string; accent?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] uppercase tracking-[0.15em] text-white/50">{label}</span>
      <span className={cn("font-display text-lg font-semibold leading-none", accent ? "text-atlas-amber" : "text-white")}>{value}</span>
      <span className="text-[11px] text-white/50">{suffix}</span>
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
