import type { ArchitectureModel, EffortBreakdown, EfficiencyBreakdown, MigrationPlan } from "@/lib/types";
import { TargetArchitectureCanvas } from "@/components/TargetArchitectureCanvas";

const RISK_STYLES: Record<string, string> = {
  low: "border-slate-400/30 bg-slate-400/10 text-slate-300",
  medium: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  high: "border-orange-400/30 bg-orange-400/10 text-orange-300",
  critical: "border-rose-400/30 bg-rose-400/10 text-rose-300",
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
    <section className="card-glow animate-fade-up" style={{ animationDelay: `${Math.min(number - 1, 8) * 40}ms` }}>
      <h3 className="mb-3 flex items-center gap-2.5 text-sm font-semibold text-slate-100">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-grad-primary text-xs font-bold text-white shadow-md">
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
    <div className="rounded-lg border border-brand-400/15 bg-brand-400/4.5 p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-brand-200">{title}</p>
      <div className="mt-1 text-xs leading-5 text-slate-400">{children}</div>
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
    return <p className="mt-1.5 text-xs text-slate-500">Effort: {headline}</p>;
  }

  const rows = [
    ["Implementation", breakdown.implementation],
    ["Validation", breakdown.validation],
    ["Cutover", breakdown.cutover],
    ["Rollback readiness", breakdown.rollback],
  ];

  if (compact) {
    return (
      <div className="space-y-1 text-xs leading-relaxed text-slate-500">
        <p>{breakdown.total || headline}</p>
        <p>Confidence: {breakdown.confidence}</p>
      </div>
    );
  }

  return (
    <div className="mt-3 rounded-lg border border-white/6 bg-black/10 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Effort</span>
        <span className="badge border-brand-400/30 bg-brand-400/10 text-brand-300">
          {breakdown.total || headline}
        </span>
        <span className="badge border-white/10 bg-white/4 text-slate-400">
          confidence: {breakdown.confidence}
        </span>
      </div>
      <dl className="mt-2 grid gap-2 sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</dt>
            <dd className="mt-0.5 whitespace-normal wrap-break-word text-xs leading-relaxed text-slate-400">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 whitespace-normal wrap-break-word text-xs leading-relaxed text-slate-500">
        {breakdown.rationale}
      </p>
    </div>
  );
}

function EfficiencyDetails({ breakdown }: { breakdown: EfficiencyBreakdown | null }) {
  if (!breakdown) return null;

  return (
    <div className="mt-3 rounded-lg border border-emerald-400/15 bg-emerald-400/4 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-emerald-300/80">Efficiency</span>
        <span className="badge border-emerald-400/30 bg-emerald-400/10 text-emerald-300">
          confidence: {breakdown.confidence}
        </span>
      </div>
      <p className="mt-2 whitespace-normal wrap-break-word text-xs leading-relaxed text-slate-300">
        {breakdown.primary_efficiency_gain}
      </p>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Benefits</p>
          <ul className="mt-1 space-y-1 text-xs leading-relaxed text-slate-400">
            {breakdown.expected_benefits.map((benefit, i) => (
              <li key={i}>{benefit}</li>
            ))}
          </ul>
        </div>
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Tradeoffs</p>
          <ul className="mt-1 space-y-1 text-xs leading-relaxed text-slate-400">
            {breakdown.tradeoffs.map((tradeoff, i) => (
              <li key={i}>{tradeoff}</li>
            ))}
          </ul>
        </div>
      </div>
      <p className="mt-2 whitespace-normal wrap-break-word text-xs leading-relaxed text-slate-500">
        {breakdown.rationale}
      </p>
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

  return (
    <div className="space-y-4">
      {showIntro && (
      <div className="card border-brand-400/15 bg-brand-400/[0.035]">
        <p className="text-sm font-semibold text-slate-100">Plan decision record</p>
        <p className="mt-1 text-sm leading-6 text-slate-400">
          This plan shows what is being changed, why the target choices were made, how much effort they need,
          what efficiency is expected, what costs are assumed, and how the migration will be validated and rolled back.
        </p>
      </div>
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
        <div className="overflow-x-auto rounded-xl border border-white/6">
          <table className="w-full min-w-[760px] table-fixed text-left text-sm">
            <thead>
              <tr className="border-b border-white/10 text-slate-400">
                <th className="w-[24%] px-3 py-2 font-medium">Component</th>
                <th className="w-[18%] px-3 py-2 font-medium">Disposition</th>
                <th className="px-3 py-2 font-medium">Target</th>
              </tr>
            </thead>
            <tbody>
              {plan.component_mappings.map((m) => (
                <tr key={m.component_id} className="border-b border-white/5 last:border-0 hover:bg-white/2">
                  <td className="wrap-break-word px-3 py-2 font-medium text-slate-200">{m.component_id}</td>
                  <td className="px-3 py-2 text-slate-400">{m.disposition}</td>
                  <td className="whitespace-normal wrap-break-word px-3 py-2 leading-relaxed text-slate-400">{m.target_description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section visible={shouldShow(3)} number={3} icon="🔢" title="Migration sequence">
        <ExplanationBox title="Why this order">
          The sequence is computed from the dependency graph before the LLM writes component steps. This keeps
          request-path dependencies, data dependencies, and coexistence risks from being ordered casually.
        </ExplanationBox>
        <ol className="mt-3 space-y-2.5">
          {plan.waves.map((w) => (
            <li key={w.index} className="rounded-xl border border-white/6 bg-white/2 p-3">
              <div className="flex items-start gap-2.5">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-brand-500/20 text-[11px] font-bold text-brand-300">
                  {w.index}
                </span>
                <div>
                  <p className="text-sm font-medium text-slate-200">{w.component_ids.join(", ")}</p>
                  <p className="mt-0.5 text-xs text-slate-500">{w.rationale}</p>
                  {w.coexistence_groups.map((g, i) => (
                    <p key={i} className="mt-1.5 flex items-start gap-1.5 text-xs text-amber-300">
                      <span>⚑</span>
                      <span>
                        Coexistence: {g.component_ids.join(", ")} — {g.coexistence_strategy}
                      </span>
                    </p>
                  ))}
                </div>
              </div>
            </li>
          ))}
        </ol>
      </Section>

      <Section visible={shouldShow(4)} number={4} icon="🧩" title="Component migration approach">
        <div className="space-y-3">
          {plan.component_plans.map((p) => (
            <div key={p.component_id} className="rounded-xl border border-white/6 bg-white/2 p-3">
              <p className="flex items-center gap-2 text-sm font-medium text-slate-200">
                {p.component_id}
                <span className="badge border-white/10 bg-white/4 text-slate-400">wave {p.wave_index}</span>
                <span className="badge border-brand-400/30 bg-brand-400/10 text-brand-300">{p.disposition}</span>
              </p>
              <p className="mt-2 text-[11px] font-medium uppercase tracking-wide text-slate-500">Recommended steps</p>
              <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-xs text-slate-400">
                {p.steps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
              {p.dependencies_considered.length > 0 && (
                <div className="mt-3 border-t border-white/6 pt-2">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Dependencies considered</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {p.dependencies_considered.map((dependency) => (
                      <span key={dependency} className="badge border-white/10 bg-white/3 text-slate-400">
                        {dependency}
                      </span>
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
            <li key={r.id} className="flex items-start gap-2.5 rounded-lg border border-white/6 bg-white/2 p-2.5 text-sm">
              <span className={`badge shrink-0 ${RISK_STYLES[r.severity]}`}>{r.severity}</span>
              <span className="text-slate-300">
                {r.description} — <span className="text-slate-500">{r.mitigation}</span>
              </span>
            </li>
          ))}
          {plan.risks.length === 0 && <li className="text-sm text-slate-500">No risks recorded.</li>}
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
                <li key={i} className="flex gap-2 text-xs text-slate-400">
                  <span className="badge border-white/10 bg-white/4 text-slate-400">{c.check_type}</span>
                  {c.description}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-slate-500">Not generated yet.</p>
        )}
      </Section>

      <Section visible={shouldShow(7)} number={7} icon="🔀" title="Cutover strategy">
        {plan.cutover_strategy ? (
          <>
            <p className="text-sm text-slate-300">{plan.cutover_strategy.approach}</p>
            {plan.cutover_strategy.rationale && (
              <ExplanationBox title="Why this cutover strategy">{plan.cutover_strategy.rationale}</ExplanationBox>
            )}
            <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-xs text-slate-400">
              {plan.cutover_strategy.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
            <p className="mt-3 text-xs font-medium uppercase tracking-wide text-slate-500">Go/no-go criteria</p>
            <ul className="mt-1 space-y-1 list-disc pl-5 text-xs text-slate-400">
              {plan.cutover_strategy.go_no_go_criteria.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-slate-500">Not generated yet.</p>
        )}
      </Section>

      <Section visible={shouldShow(8)} number={8} icon="↩️" title="Rollback strategy">
        {plan.rollback_strategy ? (
          <>
            <p className="text-sm text-slate-300">{plan.rollback_strategy.approach}</p>
            {plan.rollback_strategy.rationale && (
              <ExplanationBox title="Why this rollback strategy">{plan.rollback_strategy.rationale}</ExplanationBox>
            )}
            <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-xs text-slate-400">
              {plan.rollback_strategy.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          </>
        ) : (
          <p className="text-sm text-slate-500">Not generated yet.</p>
        )}
      </Section>

      <Section visible={shouldShow(9)} number={9} icon="🗓️" title="Migration roadmap">
        <div className="overflow-x-auto rounded-xl border border-white/6">
          <table className="w-full min-w-[860px] table-fixed text-left text-sm">
            <thead>
              <tr className="border-b border-white/10 text-slate-400">
                <th className="w-[8%] px-3 py-2 font-medium">Wave</th>
                <th className="w-[20%] px-3 py-2 font-medium">Component</th>
                <th className="w-[15%] px-3 py-2 font-medium">Disposition</th>
                <th className="px-3 py-2 font-medium">Summary</th>
                <th className="w-[14%] px-3 py-2 font-medium">Effort</th>
                <th className="w-[16%] px-3 py-2 font-medium">Efficiency</th>
                <th className="w-[12%] px-3 py-2 font-medium">Depends on waves</th>
              </tr>
            </thead>
            <tbody>
              {plan.roadmap_items.map((item, i) => (
                <tr key={i} className="border-b border-white/5 last:border-0 hover:bg-white/2">
                  <td className="px-3 py-2 text-slate-400">{item.wave_index}</td>
                  <td className="wrap-break-word px-3 py-2 font-medium text-slate-200">{item.component_id}</td>
                  <td className="px-3 py-2 text-slate-400">{item.disposition}</td>
                  <td className="whitespace-normal wrap-break-word px-3 py-2 leading-relaxed text-slate-400">{item.summary}</td>
                  <td className="px-3 py-2 text-slate-500">
                    <EffortDetails headline={item.estimated_effort} breakdown={item.effort_breakdown} compact />
                  </td>
                  <td className="whitespace-normal wrap-break-word px-3 py-2 text-xs leading-relaxed text-slate-500">
                    {item.efficiency_breakdown?.primary_efficiency_gain ?? "-"}
                  </td>
                  <td className="px-3 py-2 text-slate-500">
                    {item.depends_on_waves.length > 0 ? item.depends_on_waves.join(", ") : "none"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {costSummary && (
        <Section visible={shouldShow(10)} number={10} icon="💰" title="Cost estimate">
          <ExplanationBox title="How to read this estimate">{costSummary.methodology_note}</ExplanationBox>
          <p className="mt-3 text-2xl font-semibold text-slate-100">
            ${costSummary.total_monthly_usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            <span className="ml-1.5 text-sm font-normal text-slate-500">/ month, estimated</span>
          </p>
          <div className="mt-3 overflow-x-auto rounded-xl border border-white/6">
            <table className="w-full min-w-[860px] table-fixed text-left text-sm">
              <thead>
                <tr className="border-b border-white/10 text-slate-400">
                  <th className="w-[16%] px-3 py-2 font-medium">Component</th>
                  <th className="w-[10%] px-3 py-2 font-medium">Provider</th>
                  <th className="w-[16%] px-3 py-2 font-medium">Category</th>
                  <th className="w-[12%] px-3 py-2 font-medium">Est. $/month</th>
                  <th className="px-3 py-2 font-medium">Sizing assumption</th>
                </tr>
              </thead>
              <tbody>
                {costSummary.estimates.map((e, i) => (
                  <tr key={i} className="border-b border-white/5 last:border-0 hover:bg-white/2">
                    <td className="wrap-break-word px-3 py-2 font-medium text-slate-200">{e.component_id}</td>
                    <td className="px-3 py-2 text-slate-400 uppercase">{e.provider}</td>
                    <td className="px-3 py-2 text-slate-400">{e.service_category.replace(/_/g, " ")}</td>
                    <td className="px-3 py-2 text-slate-300">
                      {e.monthly_usd !== null ? `$${e.monthly_usd.toFixed(2)}` : <span className="text-slate-600">not estimated</span>}
                    </td>
                    <td className="whitespace-normal wrap-break-word px-3 py-2 leading-relaxed text-slate-500">
                      {e.monthly_usd !== null ? e.sizing_assumption : e.note}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}
