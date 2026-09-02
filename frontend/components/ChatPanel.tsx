"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, getConversation, streamMessage } from "@/lib/api";
import type { NodeCompleteEvent, RequestImpact, SessionStatus, TurnCompleteEvent } from "@/lib/types";
import { InterruptApprovalCard } from "@/components/InterruptApprovalCard";

interface ChatMessage {
  role: "user" | "agent" | "status" | "error";
  text: string;
  requestImpact?: RequestImpact | null;
  nodeHistory?: NodeCompleteEvent[];
  questions?: string[];
  questionsIntro?: string;
}

export interface ChatDraft {
  id: string;
  text: string;
}

// Mirrors the backend's MessageRequest.message max_length (see sessions.py) — kept
// in sync manually since the two run in separate deploys; the server is the real
// enforcement point, this is just an honest client-side heads-up.
const MAX_MESSAGE_LENGTH = 50_000;
const COLLAPSE_MESSAGE_LENGTH = 1_200;
const MESSAGE_PREVIEW_LENGTH = 700;
const VISIBLE_INTENTS = new Set([
  "source_correction",
  "target_planning",
  "high_impact_replatform",
  "unscoped_capability",
  "review_explanation",
]);

// Discovery reads pasted text conversationally, same ingest_patches prompt as any
// typed message — this is NOT an automated parser for these formats. The PRD's
// Non-Goals explicitly exclude automated discovery from cloud accounts, IaC
// repos, or monitoring systems in v1; this just saves re-typing a config you
// already have in front of you (see DECISIONS.md).
const PASTE_ACCEPT = ".txt,.md,.yml,.yaml,.tf,.json,.env,text/plain";

const NODE_LABELS: Record<string, string> = {
  ingest: "Reading your message…",
  apply_patches: "Updating the architecture model…",
  gap_analysis: "Checking what's still unknown…",
  generate_questions: "Preparing follow-up questions…",
  elicit_context: "Understanding your migration goal…",
  compute_sequence: "Computing the dependency-ordered migration sequence…",
  per_component_planning: "Planning each component within its wave…",
  strategy: "Drafting the target architecture, cutover, and rollback strategy…",
  assemble_plan: "Assembling the draft plan…",
  rules_review: "Running the deterministic review rules…",
  llm_review: "Running the semantic review pass…",
  refine: "Refining the plan to resolve findings…",
  finalize_review: "Finalizing the review…",
};

const PLANNING_NODES = [
  "elicit_context",
  "compute_sequence",
  "per_component_planning",
  "strategy",
  "assemble_plan",
  "rules_review",
  "llm_review",
  "judge_review",
  "refine",
  "finalize_review",
];

function narrateNode(event: NodeCompleteEvent): string {
  return NODE_LABELS[event.node] ?? `Working (${event.node})…`;
}

function formatIntent(intent: string): string {
  return intent
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function RequestImpactCard({ impact }: { impact: RequestImpact }) {
  return (
    <div className="mt-3 rounded-lg border border-brand-400/20 bg-brand-400/[0.055] p-3 text-xs text-slate-300">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-brand-100">Decision reasoning</span>
        <span className="badge border-brand-400/30 bg-brand-400/10 text-brand-100">{formatIntent(impact.intent)}</span>
        <span className="text-slate-500">{impact.confidence} confidence</span>
      </div>
      <p className="mt-1.5 leading-5 text-slate-400">{impact.rationale}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {impact.requires_confirmation && (
          <span className="rounded-full border border-amber-400/25 bg-amber-400/10 px-2 py-0.5 text-amber-200">
            confirmation needed
          </span>
        )}
        <span
          className={`rounded-full border px-2 py-0.5 ${
            impact.should_mutate_source
              ? "border-rose-400/25 bg-rose-400/10 text-rose-200"
              : "border-emerald-400/25 bg-emerald-400/10 text-emerald-200"
          }`}
        >
          {impact.should_mutate_source ? "source change allowed" : "source protected"}
        </span>
        {impact.impact_dimensions.map((dimension) => (
          <span key={dimension} className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-slate-300">
            {dimension.replace(/_/g, " ")}
          </span>
        ))}
      </div>
    </div>
  );
}

// Claude-UI-style transparency: the backend already streams a node_complete
// event per graph step (see NODE_LABELS) — previously the frontend kept only
// the LATEST one to drive the ephemeral "Reading your message…" label and
// discarded the rest once the turn finished. This renders the full accumulated
// sequence for a completed turn, collapsed by default so it doesn't clutter
// the normal reading experience.
function InternalWorkDisclosure({ nodeHistory }: { nodeHistory: NodeCompleteEvent[] }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-2 border-t border-white/10 pt-2">
      <button
        type="button"
        className="flex items-center gap-1 text-[11px] font-medium text-slate-500 hover:text-slate-300"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={`transition-transform ${open ? "rotate-90" : ""}`}>›</span>
        Show internal work ({nodeHistory.length} step{nodeHistory.length === 1 ? "" : "s"})
      </button>
      {open && (
        <ol className="mt-1.5 space-y-1 border-l border-white/10 pl-3">
          {nodeHistory.map((event, i) => (
            <li key={`${event.node}-${i}`} className="text-[11px] leading-4 text-slate-500">
              {narrateNode(event)}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

// Tool-Based Generative UI, scoped down: rather than routing question
// generation through an LLM-invoked frontend tool (our LLM call layer uses
// direct provider structured-output APIs, not LangChain's bind_tools(), so
// there's no tool-calling loop to hook into) this renders the SAME
// already-structured `questions` data — no backend change needed — as an
// interactive checklist instead of plain bullet text, matching the
// step-selector reference style. The checkboxes are a personal
// read/considered tracker (local-only state, not submitted anywhere) since
// the backend has no per-question structured-answer endpoint — free-text
// reply below remains the one real way to answer, exactly as before.
function QuestionChecklist({ questions, intro }: { questions: string[]; intro?: string }) {
  const [checked, setChecked] = useState<boolean[]>(() => questions.map(() => false));
  const doneCount = checked.filter(Boolean).length;

  function toggle(i: number) {
    setChecked((prev) => prev.map((v, idx) => (idx === i ? !v : v)));
  }

  return (
    <div className="mt-2.5 rounded-lg border border-brand-400/20 bg-brand-400/[0.04] p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-brand-100">{intro ?? "Questions to consider"}</span>
        <span className="badge border-brand-400/30 bg-brand-400/10 text-brand-100 shrink-0">
          {doneCount}/{questions.length} reviewed
        </span>
      </div>
      <ul className="mt-2 space-y-1.5">
        {questions.map((q, i) => (
          <li key={i}>
            <button
              type="button"
              onClick={() => toggle(i)}
              className="flex w-full items-start gap-2 rounded-md border border-white/10 bg-white/[0.03] px-2.5 py-2 text-left text-xs text-slate-200 transition-colors hover:border-brand-400/30"
            >
              <span
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded ${
                  checked[i] ? "bg-grad-primary" : "border border-white/20"
                }`}
              >
                {checked[i] && <span className="text-[10px] leading-none text-white">✓</span>}
              </span>
              <span className={checked[i] ? "text-slate-400 line-through decoration-slate-600" : ""}>{q}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const [expanded, setExpanded] = useState(false);
  const isLongUserMessage = message.role === "user" && message.text.length > COLLAPSE_MESSAGE_LENGTH;
  const visibleImpact =
    message.requestImpact && VISIBLE_INTENTS.has(message.requestImpact.intent) ? message.requestImpact : null;
  const visibleText =
    isLongUserMessage && !expanded
      ? `${message.text.slice(0, MESSAGE_PREVIEW_LENGTH).trimEnd()}\n\n...`
      : message.text;

  if (message.role === "user") {
    return (
      <div className="flex justify-end animate-pop-in">
        <div className="max-w-[85%] rounded-xl rounded-tr-md bg-grad-primary px-3.5 py-2.5 text-sm text-white shadow-lg shadow-brand-900/30">
          <div className="whitespace-pre-line">{visibleText}</div>
          {isLongUserMessage && (
            <button
              type="button"
              className="mt-2 text-xs font-medium text-white/80 underline-offset-2 hover:text-white hover:underline"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? "Show less" : `Show full input (${message.text.length.toLocaleString()} characters)`}
            </button>
          )}
        </div>
      </div>
    );
  }

  if (message.role === "error") {
    return (
      <div role="alert" className="flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-300 animate-pop-in">
        <span className="mt-0.5">⚠️</span>
        <div className="whitespace-pre-line">{visibleText}</div>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-2.5 animate-pop-in">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white/[0.06] ring-1 ring-white/10 text-xs">
        🤖
      </span>
      <div className="max-w-[85%] whitespace-pre-line rounded-xl rounded-tl-md border border-white/10 bg-white/[0.04] px-3.5 py-2.5 text-sm text-slate-200">
        {visibleText}
        {message.questions && message.questions.length > 0 && (
          <QuestionChecklist questions={message.questions} intro={message.questionsIntro} />
        )}
        {visibleImpact && <RequestImpactCard impact={visibleImpact} />}
        {message.nodeHistory && message.nodeHistory.length > 0 && (
          <InternalWorkDisclosure nodeHistory={message.nodeHistory} />
        )}
      </div>
    </div>
  );
}

export function ChatPanel({
  sessionId,
  placeholder,
  workflowStatus,
  componentCount,
  draft,
  onTurnComplete,
}: {
  sessionId: string;
  placeholder: string;
  workflowStatus?: SessionStatus;
  componentCount?: number;
  draft?: ChatDraft | null;
  onTurnComplete: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [liveStatus, setLiveStatus] = useState("");
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [attachError, setAttachError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Restores the conversation after a refresh (or a return visit) — previously
  // `messages` started empty every time and there was nothing server-side to
  // rehydrate it from, so the whole history vanished on reload.
  useEffect(() => {
    let cancelled = false;
    setHistoryLoading(true);
    getConversation(sessionId)
      .then(({ turns }) => {
        if (cancelled) return;
        setMessages(turns.map((t) => ({ role: t.role, text: t.text })));
      })
      .catch(() => {
        // A failed history fetch shouldn't block sending new messages — the
        // conversation just starts this tab looking empty, same as before.
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, liveStatus]);

  useEffect(() => {
    if (!draft) return;
    setInput(draft.text);
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(draft.text.length, draft.text.length);
    });
  }, [draft]);

  useEffect(() => {
    if (!streaming) {
      setElapsedSeconds(0);
      return;
    }

    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);

    return () => window.clearInterval(timer);
  }, [streaming]);

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file later
    if (!file) return;

    setAttachError(null);
    try {
      const text = await file.text();
      if (!text.trim()) {
        setAttachError(`${file.name} appears to be empty.`);
        return;
      }
      const combined = input.trim() ? `${input.trim()}\n\n${text}` : text;
      if (combined.length > MAX_MESSAGE_LENGTH) {
        setAttachError(
          `${file.name} is too large to attach as-is (${combined.length.toLocaleString()} of ${MAX_MESSAGE_LENGTH.toLocaleString()} characters). Paste just the relevant section instead.`
        );
        return;
      }
      setInput(combined);
    } catch {
      setAttachError(`Could not read ${file.name} as text.`);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || streaming) return;

    setMessages((prev) => [...prev, { role: "user", text }]);
    setInput("");
    setStreaming(true);
    setActiveNode(null);
    setLiveStatus("Sending…");

    // FR-E6: a stable id for this turn so a client-side retry of a dropped
    // connection doesn't re-run the graph and double-apply patches server-side —
    // the backend rejects a second submission with the same id as a duplicate.
    const messageId = crypto.randomUUID();
    // Accumulates every node_complete event for this turn (previously only the
    // latest was kept, to drive the ephemeral progress label, then discarded) —
    // attached to the resulting agent message so it can be shown afterward.
    const nodeHistory: NodeCompleteEvent[] = [];

    try {
      for await (const evt of streamMessage(sessionId, text, messageId)) {
        if (evt.event === "node_complete") {
          const data = evt.data as NodeCompleteEvent;
          nodeHistory.push(data);
          setActiveNode(data.node);
          setLiveStatus(narrateNode(data));
        } else if (evt.event === "turn_complete") {
          const data = evt.data as TurnCompleteEvent;
          setLiveStatus("Turn complete.");
          if (data.error) {
            setMessages((prev) => [...prev, { role: "error", text: data.error as string }]);
          } else {
            const questions = data.clarifying_questions?.length ? data.clarifying_questions : data.questions;
            const questionsIntro = data.clarifying_questions?.length
              ? "I need to clarify a few things before continuing:"
              : undefined;
            setMessages((prev) => [
              ...prev,
              {
                role: "agent",
                text: data.narration || (questions?.length ? "" : "Understood."),
                requestImpact: data.request_impact,
                nodeHistory,
                questions,
                questionsIntro,
              },
            ]);
          }
        } else if (evt.event === "error") {
          const data = evt.data as { detail?: string; error?: string };
          setMessages((prev) => [
            ...prev,
            { role: "error", text: data.detail ?? data.error ?? "The run failed." },
          ]);
        }
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "error", text: err instanceof ApiError ? err.detail : "Connection to the agent was lost." },
      ]);
    } finally {
      setStreaming(false);
      setActiveNode(null);
      setLiveStatus("");
      onTurnComplete();
    }
  }

  const isPlanningRun = streaming && workflowStatus === "planning";
  const activePlanningIndex = activeNode ? PLANNING_NODES.indexOf(activeNode) : -1;
  const elapsedLabel =
    elapsedSeconds < 60
      ? `${elapsedSeconds}s`
      : `${Math.floor(elapsedSeconds / 60)}m ${String(elapsedSeconds % 60).padStart(2, "0")}s`;

  return (
    <div className="card-glow flex h-[calc(100vh-22rem)] min-h-[620px] flex-col !p-0 overflow-hidden">
      <div className="flex items-center gap-2 border-b border-white/10 bg-white/[0.02] px-4 py-3">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-grad-primary text-[11px] font-bold text-white">AI</span>
        <div>
          <h3 className="text-sm font-semibold text-slate-200">Conversation</h3>
          <p className="text-[11px] text-slate-500">Ask questions, review recommendations, or provide missing architecture context.</p>
        </div>
        {streaming && <span className="ml-auto h-2 w-2 rounded-full bg-emerald-400 animate-pulse-ring" />}
      </div>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4" aria-live="polite">
        {historyLoading ? (
          <div className="space-y-3">
            <div className="ml-auto h-9 w-2/3 max-w-[85%] animate-pulse rounded-xl rounded-tr-md bg-white/[0.06]" />
            <div className="h-14 w-3/4 max-w-[85%] animate-pulse rounded-xl rounded-tl-md bg-white/[0.04]" />
          </div>
        ) : messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <p className="max-w-xs text-sm text-slate-500">{placeholder}</p>
          </div>
        ) : null}
        {!historyLoading && messages.map((m, i) => (
          <ChatBubble key={i} message={m} />
        ))}
        {streaming && (
          <div className="flex items-start gap-2.5 animate-pop-in">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white/[0.06] ring-1 ring-white/10 text-[10px] font-bold text-slate-300">AI</span>
            <div className="max-w-[88%] rounded-xl rounded-tl-md border border-white/10 bg-white/[0.04] px-3.5 py-3">
              <div className="flex items-center gap-2">
                <span className="flex gap-1">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400 [animation-delay:-0.3s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400 [animation-delay:-0.15s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400" />
                </span>
                <span className="text-xs font-medium text-slate-300">{liveStatus}</span>
              </div>
              {isPlanningRun && (
                <div className="mt-3 rounded-lg border border-brand-400/20 bg-brand-400/[0.055] p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <span className="font-semibold text-brand-100">Generating migration plan</span>
                    <span className="text-brand-200/80">Elapsed {elapsedLabel}</span>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-slate-400">
                    Large architecture models can take a few minutes. This run is planning{" "}
                    {componentCount ?? "the selected"} component{componentCount === 1 ? "" : "s"} across sequencing,
                    target architecture, cutover, rollback, review, and refinements.
                  </p>
                  <div className="mt-3 space-y-1.5">
                    {PLANNING_NODES.map((node, index) => {
                      const isDone = activePlanningIndex > index;
                      const isActive = activePlanningIndex === index;
                      return (
                        <div key={node} className="flex items-center gap-2 text-[11px]">
                          <span
                            className={`h-1.5 w-1.5 rounded-full ${
                              isDone
                                ? "bg-emerald-400"
                                : isActive
                                  ? "bg-brand-400 animate-pulse"
                                  : "bg-white/20"
                            }`}
                          />
                          <span className={isActive ? "text-slate-200" : isDone ? "text-slate-400" : "text-slate-600"}>
                            {NODE_LABELS[node] ?? `Working (${node})`}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="mx-4">
        <InterruptApprovalCard />
      </div>

      {attachError && (
        <p role="alert" className="mx-4 mb-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-300">
          {attachError}
        </p>
      )}

      <form onSubmit={handleSubmit} className="flex gap-2 border-t border-white/10 bg-white/[0.02] p-3">
        <label htmlFor="chat-input" className="sr-only">
          Message
        </label>
        <input
          ref={fileInputRef}
          type="file"
          accept={PASTE_ACCEPT}
          className="sr-only"
          onChange={handleFileSelected}
          aria-label="Attach a config file or text document to paste into the conversation"
        />
        <button
          type="button"
          className="btn-secondary self-end !px-2.5"
          disabled={streaming}
          title="Attach a config file (e.g. docker-compose.yml, a Terraform summary, a README) — read conversationally, same as typing it"
          onClick={() => fileInputRef.current?.click()}
        >
          📎
        </button>
        <textarea
          id="chat-input"
          ref={textareaRef}
          className="input flex-1 resize-none"
          rows={2}
          value={input}
          disabled={streaming}
          maxLength={MAX_MESSAGE_LENGTH}
          placeholder="Describe your system, answer the questions above, or attach a config file…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSubmit(e);
            }
          }}
        />
        <button type="submit" className="btn-primary self-end" disabled={streaming || !input.trim()}>
          {streaming ? "Sending…" : "Send"}
        </button>
      </form>
      {input.length > MAX_MESSAGE_LENGTH * 0.9 && (
        <p className="px-4 pb-2 text-right text-xs text-slate-500">
          {input.length.toLocaleString()} / {MAX_MESSAGE_LENGTH.toLocaleString()} characters
        </p>
      )}
    </div>
  );
}
