import type { ArchitectureModel, MigrationPlan } from "@/lib/types";
import { TargetArchitectureCanvas } from "@/components/TargetArchitectureCanvas";

const RISK_STYLES: Record<string, string> = {
  low: "bg-slate-100 text-slate-700",
  medium: "bg-amber-100 text-amber-800",
  high: "bg-orange-100 text-orange-800",
  critical: "bg-red-100 text-red-800",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card">
      <h3 className="mb-2 text-sm font-semibold text-slate-700">{title}</h3>
      {children}
    </section>
  );
}

export function PlanViewer({ plan, model }: { plan: MigrationPlan; model: ArchitectureModel }) {
  return (
    <div className="space-y-4">
      <Section title="1. Target architecture">
        <div className="mb-4">
          <TargetArchitectureCanvas model={model} plan={plan} />
        </div>
        <p className="whitespace-pre-line text-sm text-slate-700">{plan.target_architecture_description}</p>
      </Section>

      <Section title="2. Component mapping">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-slate-500">
                <th className="pb-1 pr-4">Component</th>
                <th className="pb-1 pr-4">Disposition</th>
                <th className="pb-1">Target</th>
              </tr>
            </thead>
            <tbody>
              {plan.component_mappings.map((m) => (
                <tr key={m.component_id} className="border-t border-slate-100">
                  <td className="py-1 pr-4 font-medium">{m.component_id}</td>
                  <td className="py-1 pr-4">{m.disposition}</td>
                  <td className="py-1">{m.target_description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="3. Migration sequence (computed, not LLM-ordered)">
        <ol className="space-y-2 text-sm">
          {plan.waves.map((w) => (
            <li key={w.index} className="rounded-md border border-slate-100 p-2">
              <p className="font-medium">
                Wave {w.index}: {w.component_ids.join(", ")}
              </p>
              <p className="text-xs text-slate-500">{w.rationale}</p>
              {w.coexistence_groups.map((g, i) => (
                <p key={i} className="mt-1 text-xs text-amber-700">
                  Coexistence: {g.component_ids.join(", ")} — {g.coexistence_strategy}
                </p>
              ))}
            </li>
          ))}
        </ol>
      </Section>

      <Section title="4. Component migration approach">
        <div className="space-y-3">
          {plan.component_plans.map((p) => (
            <div key={p.component_id} className="rounded-md border border-slate-100 p-2 text-sm">
              <p className="font-medium">
                {p.component_id} — wave {p.wave_index} — {p.disposition}
              </p>
              <ol className="mt-1 list-decimal pl-5 text-xs text-slate-600">
                {p.steps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
              {p.estimated_effort && (
                <p className="mt-1 text-xs text-slate-400">Effort: {p.estimated_effort}</p>
              )}
            </div>
          ))}
        </div>
      </Section>

      <Section title="5. Risks & assumptions">
        <ul className="space-y-2 text-sm">
          {plan.risks.map((r) => (
            <li key={r.id}>
              <span className={`badge mr-2 ${RISK_STYLES[r.severity]}`}>{r.severity}</span>
              {r.description} — <span className="text-slate-500">{r.mitigation}</span>
            </li>
          ))}
          {plan.risks.length === 0 && <li className="text-slate-400">No risks recorded.</li>}
        </ul>
      </Section>

      <Section title="6. Validation approach">
        {plan.validation_summary ? (
          <>
            <p className="text-sm text-slate-700">{plan.validation_summary.overall_strategy}</p>
            <ul className="mt-2 list-disc pl-5 text-xs text-slate-600">
              {plan.validation_summary.cross_component_checks.map((c, i) => (
                <li key={i}>
                  {c.check_type}: {c.description}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-slate-400">Not generated yet.</p>
        )}
      </Section>

      <Section title="7. Cutover strategy">
        {plan.cutover_strategy ? (
          <>
            <p className="text-sm text-slate-700">{plan.cutover_strategy.approach}</p>
            <ol className="mt-1 list-decimal pl-5 text-xs text-slate-600">
              {plan.cutover_strategy.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
            <p className="mt-2 text-xs font-medium text-slate-500">Go/no-go criteria</p>
            <ul className="list-disc pl-5 text-xs text-slate-600">
              {plan.cutover_strategy.go_no_go_criteria.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-slate-400">Not generated yet.</p>
        )}
      </Section>

      <Section title="8. Rollback strategy">
        {plan.rollback_strategy ? (
          <>
            <p className="text-sm text-slate-700">{plan.rollback_strategy.approach}</p>
            <ol className="mt-1 list-decimal pl-5 text-xs text-slate-600">
              {plan.rollback_strategy.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          </>
        ) : (
          <p className="text-sm text-slate-400">Not generated yet.</p>
        )}
      </Section>

      <Section title="9. Migration roadmap">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-slate-500">
                <th className="pb-1 pr-4">Wave</th>
                <th className="pb-1 pr-4">Component</th>
                <th className="pb-1 pr-4">Disposition</th>
                <th className="pb-1 pr-4">Summary</th>
                <th className="pb-1">Effort</th>
              </tr>
            </thead>
            <tbody>
              {plan.roadmap_items.map((item, i) => (
                <tr key={i} className="border-t border-slate-100">
                  <td className="py-1 pr-4">{item.wave_index}</td>
                  <td className="py-1 pr-4 font-medium">{item.component_id}</td>
                  <td className="py-1 pr-4">{item.disposition}</td>
                  <td className="py-1 pr-4">{item.summary}</td>
                  <td className="py-1">{item.estimated_effort ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
