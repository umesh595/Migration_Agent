import type { ArchitectureModel, EffortBreakdown, EfficiencyBreakdown, MigrationPlan } from "@/lib/types";
import { TargetArchitectureCanvas } from "@/components/TargetArchitectureCanvas";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const RISK_VARIANT: Record<string, "secondary" | "amber" | "coral" | "destructive"> = {
  low: "secondary",
  medium: "amber",
  high: "coral",
  critical: "destructive",
};

export const PLAN_SECTION_LINKS = [
  { number: 2, title: "Component mapping" },
  { number: 3, title: "Migration sequence" },
  { number: 4, title: "Component migration approach" },
  { number: 5, title: "Risks & assumptions" },
  { number: 6, title: "Validation approach" },
  { number: 7, title: "Cutover strategy" },
  { number: 8, title: "Rollback strategy" },
  { number: 9, title: "Migration roadmap" },
  { number: 10, title: "Cost estimate" },
] as const;

export type PlanSectionNumber = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10;

function Section({
  visible = true,
  number,
  icon: _icon,
  title,
  children,
}: {
  visible?: boolean;
  number: number;
  icon: string;
  title: string;
  children: React.ReactNode;
}) {
  void _icon;
  if (!visible) return null;

  return (
    <section
      className={cn(
        "rounded-xl border border-border bg-card p-5 text-card-foreground shadow-sm blueprint-reveal"
      )}
      style={{ animationDelay: `${Math.min(number - 1, 8) * 40}ms` }}
    >
      <h3 className="mb-3 flex items-center gap-2.5 text-sm font-semibold text-foreground">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-atlas-teal text-xs font-bold text-white shadow-sm">
          {number}
        </span>
        {title}
      </h3>
      {children}
    </section>
  );
}

function ExplanationBox({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-atlas-teal/20 bg-atlas-teal-soft p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-atlas-teal">{title}</p>
      <div className="mt-1 text-xs leading-5 text-muted-foreground">{children}</div>
    </div>
  );
}

function EffortDetails({
  headline,
  breakdown,
  compact = false,
}: {
  headline: string | null;
  breakdown: EffortBreakdown | null;
  compact?: boolean;
}) {
  if (!headline && !breakdown) return null;

  if (!breakdown) {
    return <p className="mt-1.5 text-xs text-muted-foreground">Effort: {headline}</p>;
  }

  const rows = [
    ["Implementation", breakdown.implementation],
    ["Validation", breakdown.validation],
    ["Cutover", breakdown.cutover],
    ["Rollback readiness", breakdown.rollback],
  ];

  if (compact) {
    return (
      <div className="space-y-1 text-xs leading-relaxed text-muted-foreground">
        <p>{breakdown.total || headline}</p>
        <p>Confidence: {breakdown.confidence}</p>
      </div>
    );
  }

  return (
    <div className="mt-3 rounded-lg border border-border bg-background/50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Effort</span>
        <Badge variant="teal">{breakdown.total || headline}</Badge>
        <Badge variant="secondary">confidence: {breakdown.confidence}</Badge>
      </div>
      <dl className="mt-2 grid gap-2 sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</dt>
            <dd className="mt-0.5 whitespace-normal break-words text-xs leading-relaxed text-muted-foreground">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 whitespace-normal break-words text-xs leading-relaxed text-muted-foreground">
        {breakdown.rationale}
      </p>
    </div>
  );
}

function EfficiencyDetails({ breakdown }: { breakdown: EfficiencyBreakdown | null }) {
  if (!breakdown) return null;

  return (
    <div className="mt-3 rounded-lg border border-atlas-mint/30 bg-atlas-mint/10 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-atlas-teal">Efficiency</span>
        <Badge variant="teal">confidence: {breakdown.confidence}</Badge>
      </div>
      <p className="mt-2 whitespace-normal break-words text-xs leading-relaxed text-foreground">
        {breakdown.primary_efficiency_gain}
      </p>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Benefits</p>
          <ul className="mt-1 space-y-1 text-xs leading-relaxed text-muted-foreground">
            {breakdown.expected_benefits.map((benefit, i) => (
              <li key={i}>{benefit}</li>
            ))}
          </ul>
        </div>
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Tradeoffs</p>
          <ul className="mt-1 space-y-1 text-xs leading-relaxed text-muted-foreground">
            {breakdown.tradeoffs.map((tradeoff, i) => (
              <li key={i}>{tradeoff}</li>
            ))}
          </ul>
        </div>
      </div>
      <p className="mt-2 whitespace-normal break-words text-xs leading-relaxed text-muted-foreground">
        {breakdown.rationale}
      </p>
    </div>
  );
}

function EfficiencyCell({ breakdown }: { breakdown: EfficiencyBreakdown | null }) {
  if (!breakdown) return <span className="text-muted-foreground/60">-</span>;
  return (
    <div className="space-y-1 text-xs leading-relaxed text-muted-foreground">
      <p>{breakdown.primary_efficiency_gain}</p>
      {breakdown.expected_benefits.length > 0 && (
        <p>
          <span className="font-medium text-foreground">Benefits: </span>
          {breakdown.expected_benefits.join("; ")}
        </p>
      )}
      {breakdown.tradeoffs.length > 0 && (
        <p>
          <span className="font-medium text-foreground">Tradeoffs: </span>
          {breakdown.tradeoffs.join("; ")}
        </p>
      )}
      <p className="text-muted-foreground/70">Confidence: {breakdown.confidence}</p>
    </div>
  );
}

export function PlanViewer({
  plan,
  model,
  onlySection,
  showIntro = true,
}: {
  plan: MigrationPlan;
  model: ArchitectureModel;
  onlySection?: PlanSectionNumber;
  showIntro?: boolean;
}) {
  const shouldShow = (section: PlanSectionNumber) => onlySection === undefined || onlySection === section;
  const costSummary = plan.cost_summary;
  const componentNameById = new Map(model.components.map((c) => [c.id, c.name]));

  return (
    <div className="space-y-4">
      {showIntro && (
        <Card className="border-atlas-teal/20 bg-atlas-teal-soft p-4">
          <p className="text-sm font-semibold text-foreground">Plan decision record</p>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            This plan shows what is being changed, why the target choices were made, how much effort they need,
            what efficiency is expected, what costs are assumed, and how the migration will be validated and rolled back.
          </p>
        </Card>
      )}
      <Section visible={shouldShow(1)} number={1} icon="🎯" title="Target architecture">
        <div className="mb-4">
          <TargetArchitectureCanvas model={model} plan={plan} />
        </div>
        <ExplanationBox title="Why this target architecture">
          <p className="whitespace-pre-line">{plan.target_architecture_description}</p>
        </ExplanationBox>
      </Section>

      <Section visible={shouldShow(2)} number={2} icon="🗺️" title="Component mapping">
        <div className="overflow-x-auto rounded-xl border border-border">
          <Table className="min-w-[760px] table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[24%]">Component</TableHead>
                <TableHead className="w-[18%]">Disposition</TableHead>
                <TableHead>Target</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {plan.component_mappings.map((m) => (
                <TableRow key={m.component_id}>
                  <TableCell className="break-words font-medium text-foreground">{m.component_id}</TableCell>
                  <TableCell className="text-muted-foreground">{m.disposition}</TableCell>
                  <TableCell className="whitespace-normal break-words leading-relaxed text-muted-foreground">
                    {m.target_description}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Section>

      <Section visible={shouldShow(3)} number={3} icon="🔢" title="Migration sequence">
        <ExplanationBox title="Why this order">
          The sequence is computed from the dependency graph before the LLM writes component steps. This keeps
          request-path dependencies, data dependencies, and coexistence risks from being ordered casually.
        </ExplanationBox>
        <ol className="mt-3">
          {plan.waves.map((w, i) => (
            <li key={w.index} className="relative flex gap-4">
              <div className="flex flex-col items-center">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-atlas-teal text-sm font-bold text-white shadow-sm">
                  {w.index}
                </span>
                {i < plan.waves.length - 1 && <span className="my-1 w-px flex-1 bg-border" />}
              </div>
              <div className="mb-3 min-w-0 flex-1 rounded-xl border border-border bg-card p-3.5 pb-4">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Wave {w.index}</p>
                <p className="mt-1 text-sm font-medium text-foreground">{w.component_ids.join(", ")}</p>
                <p className="mt-1.5 text-xs leading-5 text-muted-foreground">{w.rationale}</p>
                {w.coexistence_groups.map((g, gi) => (
                  <p key={gi} className="mt-1.5 flex items-start gap-1.5 text-xs text-atlas-amber">
                    <span>⚑</span>
                    <span>
                      Coexistence: {g.component_ids.join(", ")} — {g.coexistence_strategy}
                    </span>
                  </p>
                ))}
              </div>
            </li>
          ))}
        </ol>
      </Section>

      <Section visible={shouldShow(4)} number={4} icon="🧩" title="Component migration approach">
        <div className="space-y-3">
          {plan.component_plans.map((p) => (
            <div key={p.component_id} className="rounded-xl border border-border bg-background/50 p-3">
              <p className="flex items-center gap-2 text-sm font-medium text-foreground">
                {p.component_id}
                <Badge variant="secondary">wave {p.wave_index}</Badge>
                <Badge variant="teal">{p.disposition}</Badge>
              </p>
              <p className="mt-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Recommended steps</p>
              <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-xs text-muted-foreground">
                {p.steps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
              {p.dependencies_considered.length > 0 && (
                <div className="mt-3 border-t border-border pt-2">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Dependencies considered</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {p.dependencies_considered.map((dependency) => (
                      <Badge key={dependency} variant="secondary">
                        {dependency}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
              <EffortDetails headline={p.estimated_effort} breakdown={p.effort_breakdown} />
              <EfficiencyDetails breakdown={p.efficiency_breakdown} />
            </div>
          ))}
        </div>
      </Section>

      <Section visible={shouldShow(5)} number={5} icon="⚠️" title="Risks & assumptions">
        <ul className="space-y-2">
          {plan.risks.map((r) => (
            <li key={r.id} className="flex items-start gap-2.5 rounded-lg border border-border bg-background/50 p-2.5 text-sm">
              <Badge variant={RISK_VARIANT[r.severity]} className="shrink-0">
                {r.severity}
              </Badge>
              <span className="text-foreground">
                {r.description} — <span className="text-muted-foreground">{r.mitigation}</span>
              </span>
            </li>
          ))}
          {plan.risks.length === 0 && <li className="text-sm text-muted-foreground">No risks recorded.</li>}
        </ul>
      </Section>

      <Section visible={shouldShow(6)} number={6} icon="✅" title="Validation approach">
        {plan.validation_summary ? (
          <>
            <ExplanationBox title="Why this validation approach">
              {plan.validation_summary.overall_strategy}
            </ExplanationBox>
            <ul className="mt-2.5 space-y-1">
              {plan.validation_summary.cross_component_checks.map((c, i) => (
                <li key={i} className="flex gap-2 text-xs text-muted-foreground">
                  <Badge variant="secondary">{c.check_type}</Badge>
                  {c.description}
                </li>
              ))}
            </ul>
            {plan.validation_summary.sign_off_gates.length > 0 && (
              <div className="mt-3 border-t border-border pt-2.5">
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Sign-off gates</p>
                <ul className="mt-1.5 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                  {plan.validation_summary.sign_off_gates.map((g, i) => (
                    <li key={i}>{g}</li>
                  ))}
                </ul>
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Not generated yet.</p>
        )}
      </Section>

      <Section visible={shouldShow(7)} number={7} icon="🔀" title="Cutover strategy">
        {plan.cutover_strategy ? (
          <>
            <p className="text-sm text-foreground">{plan.cutover_strategy.approach}</p>
            {plan.cutover_strategy.rationale && (
              <ExplanationBox title="Why this cutover strategy">{plan.cutover_strategy.rationale}</ExplanationBox>
            )}
            <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-xs text-muted-foreground">
              {plan.cutover_strategy.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
            <p className="mt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">Go/no-go criteria</p>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
              {plan.cutover_strategy.go_no_go_criteria.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
            {plan.cutover_strategy.communication_plan && (
              <div className="mt-3 border-t border-border pt-2.5">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Communication plan</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{plan.cutover_strategy.communication_plan}</p>
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Not generated yet.</p>
        )}
      </Section>

      <Section visible={shouldShow(8)} number={8} icon="↩️" title="Rollback strategy">
        {plan.rollback_strategy ? (
          <>
            <p className="text-sm text-foreground">{plan.rollback_strategy.approach}</p>
            {plan.rollback_strategy.rationale && (
              <ExplanationBox title="Why this rollback strategy">{plan.rollback_strategy.rationale}</ExplanationBox>
            )}
            {plan.rollback_strategy.triggers.length > 0 && (
              <div className="mt-3">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Triggers</p>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                  {plan.rollback_strategy.triggers.map((t, i) => (
                    <li key={i}>{t}</li>
                  ))}
                </ul>
              </div>
            )}
            <ol className="mt-3 list-decimal space-y-0.5 pl-5 text-xs text-muted-foreground">
              {plan.rollback_strategy.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
            {plan.rollback_strategy.data_reconciliation_notes && (
              <div className="mt-3 border-t border-border pt-2.5">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Data reconciliation notes</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {plan.rollback_strategy.data_reconciliation_notes}
                </p>
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Not generated yet.</p>
        )}
      </Section>

      <Section visible={shouldShow(9)} number={9} icon="🗓️" title="Migration roadmap">
        <div className="overflow-x-auto rounded-xl border border-border">
          <Table className="min-w-[960px] table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[7%]">Wave</TableHead>
                <TableHead className="w-[15%]">Component</TableHead>
                <TableHead className="w-[12%]">Disposition</TableHead>
                <TableHead className="w-[13%]">Owner</TableHead>
                <TableHead>Summary</TableHead>
                <TableHead className="w-[13%]">Effort</TableHead>
                <TableHead className="w-[18%]">Efficiency</TableHead>
                <TableHead className="w-[10%]">Depends on waves</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {plan.roadmap_items.map((item, i) => (
                <TableRow key={i}>
                  <TableCell className="text-muted-foreground">{item.wave_index}</TableCell>
                  <TableCell className="break-words font-medium text-foreground">{item.component_id}</TableCell>
                  <TableCell className="text-muted-foreground">{item.disposition}</TableCell>
                  <TableCell className="whitespace-normal break-words text-xs text-muted-foreground">
                    {item.owner_placeholder}
                  </TableCell>
                  <TableCell className="whitespace-normal break-words leading-relaxed text-muted-foreground">
                    {item.summary}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    <EffortDetails headline={item.estimated_effort} breakdown={item.effort_breakdown} compact />
                  </TableCell>
                  <TableCell>
                    <EfficiencyCell breakdown={item.efficiency_breakdown} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {item.depends_on_waves.length > 0 ? item.depends_on_waves.join(", ") : "none"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Section>

      {costSummary && (
        <Section visible={shouldShow(10)} number={10} icon="💰" title="Cost estimate">
          <ExplanationBox title="How to read this estimate">{costSummary.methodology_note}</ExplanationBox>
          <p className="mt-3 text-2xl font-semibold text-foreground">
            ${costSummary.total_monthly_usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            <span className="ml-1.5 text-sm font-normal text-muted-foreground">/ month, estimated</span>
          </p>
          {costSummary.unestimated_component_ids.length > 0 && (
            <div className="mt-3 rounded-lg border border-atlas-amber/25 bg-atlas-amber-soft p-3 text-xs leading-5 text-atlas-amber">
              <span className="font-semibold">Not included in the total above: </span>
              {costSummary.unestimated_component_ids
                .map((id) => componentNameById.get(id) ?? id)
                .join(", ")}
            </div>
          )}
          <div className="mt-3 overflow-x-auto rounded-xl border border-border">
            <Table className="min-w-[860px] table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[16%]">Component</TableHead>
                  <TableHead className="w-[10%]">Provider</TableHead>
                  <TableHead className="w-[16%]">Category</TableHead>
                  <TableHead className="w-[12%]">Est. $/month</TableHead>
                  <TableHead>Sizing assumption</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {costSummary.estimates.map((e, i) => (
                  <TableRow key={i}>
                    <TableCell className="break-words font-medium text-foreground">{e.component_id}</TableCell>
                    <TableCell className="uppercase text-muted-foreground">{e.provider}</TableCell>
                    <TableCell className="text-muted-foreground">{e.service_category.replace(/_/g, " ")}</TableCell>
                    <TableCell className="text-foreground">
                      {e.monthly_usd !== null ? (
                        `$${e.monthly_usd.toFixed(2)}`
                      ) : (
                        <span className="text-muted-foreground/60">not estimated</span>
                      )}
                    </TableCell>
                    <TableCell className="whitespace-normal break-words leading-relaxed text-muted-foreground">
                      {e.monthly_usd !== null ? e.sizing_assumption : e.note}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Section>
      )}
    </div>
  );
}
