import type { ArchitectureModel, MigrationPlan } from "@/lib/types";
import { TargetArchitectureCanvas } from "@/components/TargetArchitectureCanvas";

const RISK_STYLES: Record<string, string> = {
  low: "border-slate-400/30 bg-slate-400/10 text-slate-300",
  medium: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  high: "border-orange-400/30 bg-orange-400/10 text-orange-300",
  critical: "border-rose-400/30 bg-rose-400/10 text-rose-300",
};

function Section({
  number,
  icon,
  title,
  children,
}: {
  number: number;
  icon: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="card-glow animate-fade-up" style={{ animationDelay: `${Math.min(number - 1, 8) * 40}ms` }}>
      <h3 className="mb-3 flex items-center gap-2.5 text-sm font-semibold text-slate-100">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-grad-primary text-xs font-bold text-white shadow-md">
          {number}
        </span>
        <span className="text-base">{icon}</span>
        {title}
      </h3>
      {children}
    </section>
  );
}

export function PlanViewer({ plan, model }: { plan: MigrationPlan; model: ArchitectureModel }) {
  return (
    <div className="space-y-4">
      <Section number={1} icon="🎯" title="Target architecture">
        <div className="mb-4">
          <TargetArchitectureCanvas model={model} plan={plan} />
        </div>
        <p className="whitespace-pre-line text-sm text-slate-300">{plan.target_architecture_description}</p>
      </Section>

      <Section number={2} icon="🗺️" title="Component mapping">
        <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
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
                <tr key={m.component_id} className="border-b border-white/[0.05] last:border-0 hover:bg-white/[0.02]">
                  <td className="break-words px-3 py-2 font-medium text-slate-200">{m.component_id}</td>
                  <td className="px-3 py-2 text-slate-400">{m.disposition}</td>
                  <td className="whitespace-normal break-words px-3 py-2 leading-relaxed text-slate-400">{m.target_description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section number={3} icon="🔢" title="Migration sequence (computed, not LLM-ordered)">
        <ol className="space-y-2.5">
          {plan.waves.map((w) => (
            <li key={w.index} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
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

      <Section number={4} icon="🧩" title="Component migration approach">
        <div className="space-y-3">
          {plan.component_plans.map((p) => (
            <div key={p.component_id} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <p className="flex items-center gap-2 text-sm font-medium text-slate-200">
                {p.component_id}
                <span className="badge border-white/10 bg-white/[0.04] text-slate-400">wave {p.wave_index}</span>
                <span className="badge border-brand-400/30 bg-brand-400/10 text-brand-300">{p.disposition}</span>
              </p>
              <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-xs text-slate-400">
                {p.steps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
              {p.dependencies_considered.length > 0 && (
                <div className="mt-3 border-t border-white/[0.06] pt-2">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Dependencies considered</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {p.dependencies_considered.map((dependency) => (
                      <span key={dependency} className="badge border-white/10 bg-white/[0.03] text-slate-400">
                        {dependency}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {p.estimated_effort && <p className="mt-1.5 text-xs text-slate-500">⏱ Effort: {p.estimated_effort}</p>}
            </div>
          ))}
        </div>
      </Section>

      <Section number={5} icon="⚠️" title="Risks & assumptions">
        <ul className="space-y-2">
          {plan.risks.map((r) => (
            <li key={r.id} className="flex items-start gap-2.5 rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5 text-sm">
              <span className={`badge shrink-0 ${RISK_STYLES[r.severity]}`}>{r.severity}</span>
              <span className="text-slate-300">
                {r.description} — <span className="text-slate-500">{r.mitigation}</span>
              </span>
            </li>
          ))}
          {plan.risks.length === 0 && <li className="text-sm text-slate-500">No risks recorded.</li>}
        </ul>
      </Section>

      <Section number={6} icon="✅" title="Validation approach">
        {plan.validation_summary ? (
          <>
            <p className="text-sm text-slate-300">{plan.validation_summary.overall_strategy}</p>
            <ul className="mt-2.5 space-y-1">
              {plan.validation_summary.cross_component_checks.map((c, i) => (
                <li key={i} className="flex gap-2 text-xs text-slate-400">
                  <span className="badge border-white/10 bg-white/[0.04] text-slate-400">{c.check_type}</span>
                  {c.description}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-slate-500">Not generated yet.</p>
        )}
      </Section>

      <Section number={7} icon="🔀" title="Cutover strategy">
        {plan.cutover_strategy ? (
          <>
            <p className="text-sm text-slate-300">{plan.cutover_strategy.approach}</p>
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

      <Section number={8} icon="↩️" title="Rollback strategy">
        {plan.rollback_strategy ? (
          <>
            <p className="text-sm text-slate-300">{plan.rollback_strategy.approach}</p>
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

      <Section number={9} icon="🗓️" title="Migration roadmap">
        <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
          <table className="w-full min-w-[860px] table-fixed text-left text-sm">
            <thead>
              <tr className="border-b border-white/10 text-slate-400">
                <th className="w-[8%] px-3 py-2 font-medium">Wave</th>
                <th className="w-[20%] px-3 py-2 font-medium">Component</th>
                <th className="w-[15%] px-3 py-2 font-medium">Disposition</th>
                <th className="px-3 py-2 font-medium">Summary</th>
                <th className="w-[14%] px-3 py-2 font-medium">Effort</th>
                <th className="w-[14%] px-3 py-2 font-medium">Depends on waves</th>
              </tr>
            </thead>
            <tbody>
              {plan.roadmap_items.map((item, i) => (
                <tr key={i} className="border-b border-white/[0.05] last:border-0 hover:bg-white/[0.02]">
                  <td className="px-3 py-2 text-slate-400">{item.wave_index}</td>
                  <td className="break-words px-3 py-2 font-medium text-slate-200">{item.component_id}</td>
                  <td className="px-3 py-2 text-slate-400">{item.disposition}</td>
                  <td className="whitespace-normal break-words px-3 py-2 leading-relaxed text-slate-400">{item.summary}</td>
                  <td className="px-3 py-2 text-slate-500">{item.estimated_effort ?? "—"}</td>
                  <td className="px-3 py-2 text-slate-500">
                    {item.depends_on_waves.length > 0 ? item.depends_on_waves.join(", ") : "none"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
