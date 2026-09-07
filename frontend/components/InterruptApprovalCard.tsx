"use client";

import { useInterrupt } from "@copilotkit/react-core/v2";

interface PatchDetail {
  op: string;
  summary: string;
  detail: Record<string, unknown>;
}

// Renders the real approve/edit/reject decision for a structural or
// high-impact patch the backend paused for confirmation (see
// apply_patches_node's interrupt() call in discovery.py) — this replaces the
// old behavior where such a change was just silently rejected with a text
// reason buried in the audit trail. `renderInChat: false` because this app
// has its own ChatPanel, not <CopilotChat> — the caller places the returned
// element wherever it belongs (directly under the in-flight turn).
//
// Only the `reason`/`message` fields are promoted to top-level Interrupt
// fields by ag-ui-langgraph (see lg_interrupt_to_agui) — the `patches` array
// discovery.py's interrupt() payload also sends lives under
// interrupt.metadata.langgraph.raw.patches instead.
export function InterruptApprovalCard() {
  return useInterrupt({
    renderInChat: false,
    enabled: (event) => event.value?.reason === "structural_confirmation",
    render: ({ interrupt, resolve }) => {
      const raw = (interrupt?.metadata?.langgraph as { raw?: { patches?: PatchDetail[] } } | undefined)?.raw;
      const patches = raw?.patches ?? [];

      return (
        <div className="mt-3 rounded-lg border border-amber-400/30 bg-amber-400/6 p-3 text-sm text-slate-200 animate-pop-in">
          <p className="font-semibold text-amber-100">Confirmation needed</p>
          <p className="mt-1 text-xs leading-5 text-slate-300">{interrupt?.message}</p>
          <ul className="mt-2 space-y-1.5">
            {patches.map((p, i) => (
              <li key={i} className="rounded-md border border-white/10 bg-white/3 px-2.5 py-1.5 text-xs text-slate-300">
                <span className="font-mono text-amber-200">{p.op}</span>
                {p.summary && <span className="ml-1.5 text-slate-400">— {p.summary}</span>}
              </li>
            ))}
          </ul>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              className="btn-primary py-1.5! px-3! text-xs"
              onClick={() => resolve({ approved: true })}
            >
              Approve
            </button>
            <button
              type="button"
              className="btn-secondary py-1.5! px-3! text-xs"
              onClick={() => resolve({ approved: false })}
            >
              Reject
            </button>
          </div>
        </div>
      );
    },
  });
}
