"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, disconnectAws, getConversation, streamMessage } from "@/lib/api";
import type { GeneratedQuestion, NodeCompleteEvent, RequestImpact, SessionStatus, TurnCompleteEvent } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { InterruptApprovalCard } from "@/components/InterruptApprovalCard";
import { StartDiscoveryChoice } from "@/components/StartDiscoveryChoice";
import { cn } from "@/lib/utils";

interface ChatMessage {
  role: "user" | "agent" | "status" | "error";
  text: string;
  requestImpact?: RequestImpact | null;
  nodeHistory?: NodeCompleteEvent[];
  questions?: string[];
  questionDetails?: GeneratedQuestion[];
  questionsIntro?: string;
  modelVersion?: number | null;
  tokensUsed?: number;
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
// typed message — this is NOT an automated parser for these formats; it just
// saves re-typing a config you already have in front of you. Cloud-account and
// document import (StartDiscoveryChoice) are separate, explicit, user-initiated
// actions — see DECISIONS.md's "PRD-bump override" entry.
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
    <div className="mt-3 rounded-lg border border-atlas-teal/20 bg-atlas-teal-soft p-3 text-xs text-foreground">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-atlas-teal">Decision reasoning</span>
        <Badge variant="teal">{formatIntent(impact.intent)}</Badge>
        <span className="text-muted-foreground">{impact.confidence} confidence</span>
      </div>
      <p className="mt-1.5 leading-5 text-muted-foreground">{impact.rationale}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {impact.requires_confirmation && <Badge variant="amber">confirmation needed</Badge>}
        <Badge variant={impact.should_mutate_source ? "coral" : "teal"}>
          {impact.should_mutate_source ? "source change allowed" : "source protected"}
        </Badge>
        {impact.impact_dimensions.map((dimension) => (
          <Badge key={dimension} variant="secondary">
            {dimension.replace(/_/g, " ")}
          </Badge>
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
    <div className="mt-2 border-t border-border pt-2">
      <button
        type="button"
        className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={cn("transition-transform", open && "rotate-90")}>›</span>
        Show internal work ({nodeHistory.length} step{nodeHistory.length === 1 ? "" : "s"})
      </button>
      {open && (
        <ol className="mt-1.5 space-y-1 border-l border-border pl-3">
          {nodeHistory.map((event, i) => (
            <li key={`${event.node}-${i}`} className="text-[11px] leading-4 text-muted-foreground">
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
// already-structured `questions` data as an interactive batch-answer form
// instead of plain bullet text. Hypothesis Cards (`details[i].hypothesis`)
// and answer options (`details[i].answer_options`) are real backend output
// (generate_questions_node). Picking options is multi-select per question —
// several plausible answers can genuinely both apply — plus an optional note
// and an explicit "I don't know" skip, all staged locally and sent as ONE
// composed message through the existing turn pipeline once ready, matching
// how a person would actually answer a short multi-part questionnaire rather
// than one round-trip per question. The free-text box below the transcript
// remains available the whole time for anything the offered options don't
// cover — this form is additive, never the only way to answer.
type QuestionAnswerState = { picks: string[]; note: string; skipped: boolean };

function composeQuestionAnswer(state: QuestionAnswerState): string {
  if (state.skipped && state.picks.length === 0 && !state.note.trim()) {
    return "Not sure — proceed with your best assumption and note it as a risk.";
  }
  const parts = [...state.picks];
  if (state.note.trim()) parts.push(state.note.trim());
  return parts.join(" · ");
}

function QuestionChecklist({
  questions,
  details,
  intro,
  onSendBatch,
  sending,
}: {
  questions: string[];
  details?: GeneratedQuestion[];
  intro?: string;
  onSendBatch: (composedMessage: string) => void;
  sending: boolean;
}) {
  const [answers, setAnswers] = useState<Record<number, QuestionAnswerState>>({});

  function answerFor(i: number): QuestionAnswerState {
    return answers[i] ?? { picks: [], note: "", skipped: false };
  }

  function togglePick(i: number, option: string) {
    setAnswers((prev) => {
      const current = answerFor(i);
      const picks = current.picks.includes(option)
        ? current.picks.filter((p) => p !== option)
        : [...current.picks, option];
      return { ...prev, [i]: { ...current, picks, skipped: false } };
    });
  }

  function setNote(i: number, note: string) {
    setAnswers((prev) => ({ ...prev, [i]: { ...answerFor(i), note } }));
  }

  function toggleSkip(i: number) {
    setAnswers((prev) => {
      const current = answerFor(i);
      return { ...prev, [i]: { ...current, skipped: !current.skipped } };
    });
  }

  const isAnswered = (i: number) => {
    const a = answerFor(i);
    return a.skipped || a.picks.length > 0 || a.note.trim().length > 0;
  };
  const answeredCount = questions.reduce((n, _q, i) => n + (isAnswered(i) ? 1 : 0), 0);
  const pickCount = questions.reduce((n, _q, i) => n + answerFor(i).picks.length, 0);

  function handleSend() {
    if (sending || answeredCount === 0) return;
    const lines = questions
      .map((q, i) => (isAnswered(i) ? `${q} — ${composeQuestionAnswer(answerFor(i))}` : null))
      .filter((line): line is string => line !== null);
    if (!lines.length) return;
    onSendBatch(lines.join("\n"));
    setAnswers({});
  }

  return (
    <div className="mt-2.5 rounded-lg border border-atlas-teal/20 bg-atlas-teal-soft p-3">
      <span className="text-xs font-semibold text-atlas-teal">{intro ?? "Questions to consider"}</span>
      <ul className="mt-2.5 space-y-2">
        {questions.map((q, i) => {
          const detail = details?.[i];
          const a = answerFor(i);
          const answered = isAnswered(i);
          return (
            <li
              key={i}
              className={cn(
                "rounded-md border px-2.5 py-2.5 transition-colors",
                answered ? "border-atlas-amber/30 bg-atlas-amber-soft" : "border-border bg-background/50"
              )}
            >
              <span className="block text-xs leading-5 text-foreground">{q}</span>
              {detail?.hypothesis && (
                <p className="mt-1.5 text-[11px] italic leading-4 text-atlas-teal/80">💡 {detail.hypothesis}</p>
              )}
              {detail && detail.answer_options.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {detail.answer_options.map((option, optionIndex) => (
                    <button
                      key={optionIndex}
                      type="button"
                      aria-pressed={a.picks.includes(option)}
                      onClick={() => togglePick(i, option)}
                      className="chip-toggle"
                    >
                      {a.picks.includes(option) && <span className="text-[10px]">✓</span>}
                      {option}
                    </button>
                  ))}
                </div>
              )}
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <input
                  type="text"
                  value={a.note}
                  onChange={(e) => setNote(i, e.target.value)}
                  placeholder="Add detail in your own words (optional)"
                  className="input min-w-[180px] flex-1 !py-1.5 !text-xs"
                />
                <button
                  type="button"
                  onClick={() => toggleSkip(i)}
                  className={cn(
                    "shrink-0 rounded-md border px-2 py-1.5 text-[11px] font-medium transition",
                    a.skipped
                      ? "border-atlas-amber/40 bg-atlas-amber-soft text-atlas-amber"
                      : "border-border bg-background/50 text-muted-foreground hover:border-atlas-teal/40"
                  )}
                >
                  {a.skipped ? "Skipped ✓" : "I don't know"}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-2.5">
        <span className="text-[11px] text-muted-foreground">
          {answeredCount === 0
            ? "No answers staged yet"
            : `${answeredCount}/${questions.length} answered${pickCount ? ` · ${pickCount} option${pickCount === 1 ? "" : "s"} selected` : ""}`}
        </span>
        <Button
          type="button"
          size="sm"
          disabled={answeredCount === 0 || sending}
          onClick={handleSend}
          className="ml-auto bg-atlas-teal text-xs text-white hover:bg-atlas-teal/90"
        >
          Send answers
        </Button>
      </div>
    </div>
  );
}

function ChatBubble({
  message,
  onSendBatch,
  sending,
}: {
  message: ChatMessage;
  onSendBatch: (composedMessage: string) => void;
  sending: boolean;
}) {
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
        <div className="max-w-[85%] rounded-xl rounded-tr-md bg-atlas-teal px-3.5 py-2.5 text-sm text-white shadow-sm">
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
      <div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive animate-pop-in">
        <span className="mt-0.5">⚠️</span>
        <div className="whitespace-pre-line">{visibleText}</div>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-2.5 animate-pop-in">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted ring-1 ring-border text-xs">
        🤖
      </span>
      <div className="max-w-[85%] whitespace-pre-line rounded-xl rounded-tl-md border border-border bg-card px-3.5 py-2.5 text-sm text-foreground">
        {visibleText}
        {message.questions && message.questions.length > 0 && (
          <QuestionChecklist
            questions={message.questions}
            details={message.questionDetails}
            intro={message.questionsIntro}
            onSendBatch={onSendBatch}
            sending={sending}
          />
        )}
        {visibleImpact && <RequestImpactCard impact={visibleImpact} />}
        {message.nodeHistory && message.nodeHistory.length > 0 && (
          <InternalWorkDisclosure nodeHistory={message.nodeHistory} />
        )}
        {(message.modelVersion != null || (message.tokensUsed ?? 0) > 0) && (
          <p className="mt-2 text-[10px] text-muted-foreground/70">
            {message.modelVersion != null && <>model v{message.modelVersion}</>}
            {message.modelVersion != null && (message.tokensUsed ?? 0) > 0 && " · "}
            {(message.tokensUsed ?? 0) > 0 && <>{message.tokensUsed!.toLocaleString()} tokens</>}
          </p>
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
  const [awsConnection, setAwsConnection] = useState<{ resourceCount: number } | null>(null);
  const [disconnectingAws, setDisconnectingAws] = useState(false);
  const [startChoiceDismissed, setStartChoiceDismissed] = useState(false);
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

  async function sendMessageText(text: string) {
    if (!text || streaming) return;

    setMessages((prev) => [...prev, { role: "user", text }]);
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
            // question_details (Hypothesis Cards / answer options) only ever
            // corresponds to data.questions (Discovery's generate_questions_node
            // output) — never to clarifying_questions (Planning's simpler
            // free-text intake), so only attach it in that case.
            const questionDetails = data.clarifying_questions?.length ? undefined : data.question_details;
            setMessages((prev) => [
              ...prev,
              {
                role: "agent",
                text: data.narration || (questions?.length ? "" : "Understood."),
                requestImpact: data.request_impact,
                nodeHistory,
                questions,
                questionDetails,
                questionsIntro,
                modelVersion: data.model_version,
                tokensUsed: data.tokens_used,
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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    await sendMessageText(text);
  }

  /** Injects a system-style note (e.g. "document imported", "AWS connected")
   * into the transcript without going through the streaming turn pipeline —
   * used by StartDiscoveryChoice, whose import/connect calls are plain REST
   * requests, not a graph turn. */
  function pushAgentNote(text: string, questions?: string[]) {
    setMessages((prev) => [...prev, { role: "agent", text, questions }]);
  }

  async function handleDisconnectAws() {
    if (disconnectingAws) return;
    setDisconnectingAws(true);
    try {
      await disconnectAws(sessionId);
    } catch {
      // Best-effort: even if the request fails, drop the local badge — the
      // in-process cache also expires the cached inventory on its own TTL.
    } finally {
      setAwsConnection(null);
      setDisconnectingAws(false);
      pushAgentNote("Disconnected your AWS account. I won't cross-reference live infrastructure anymore this session.");
    }
  }

  const isPlanningRun = streaming && workflowStatus === "planning";
  const activePlanningIndex = activeNode ? PLANNING_NODES.indexOf(activeNode) : -1;
  const elapsedLabel =
    elapsedSeconds < 60
      ? `${elapsedSeconds}s`
      : `${Math.floor(elapsedSeconds / 60)}m ${String(elapsedSeconds % 60).padStart(2, "0")}s`;

  return (
    <div className="flex h-[calc(100vh-22rem)] min-h-[620px] flex-col overflow-hidden rounded-xl border border-border bg-card blueprint-reveal">
      <div className="flex items-center gap-2 border-b border-border bg-muted/40 px-4 py-3">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-atlas-teal text-[11px] font-bold text-white">AI</span>
        <div>
          <h3 className="font-display text-sm font-semibold text-foreground">Conversation</h3>
          <p className="text-[11px] text-muted-foreground">Ask questions, review recommendations, or provide missing architecture context.</p>
        </div>
        {awsConnection && (
          <button
            type="button"
            className="ml-auto flex items-center gap-1.5 rounded-full border border-atlas-teal/25 bg-atlas-teal-soft px-2.5 py-1 text-[11px] font-medium text-atlas-teal transition hover:border-atlas-coral/30 hover:bg-atlas-coral-soft hover:text-atlas-coral disabled:opacity-60"
            disabled={disconnectingAws}
            title="Click to disconnect this AWS account"
            onClick={handleDisconnectAws}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-atlas-teal" />
            AWS connected · {awsConnection.resourceCount} resources
          </button>
        )}
        {streaming && <span className={cn("h-2 w-2 rounded-full bg-atlas-teal agent-pulse", !awsConnection && "ml-auto")} />}
      </div>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4" aria-live="polite">
        {historyLoading ? (
          <div className="space-y-3">
            <div className="ml-auto h-9 w-2/3 max-w-[85%] animate-pulse rounded-xl rounded-tr-md bg-muted" />
            <div className="h-14 w-3/4 max-w-[85%] animate-pulse rounded-xl rounded-tl-md bg-muted" />
          </div>
        ) : messages.length === 0 && workflowStatus === "discovery" && !startChoiceDismissed ? (
          <StartDiscoveryChoice
            sessionId={sessionId}
            placeholder={placeholder}
            onSendText={sendMessageText}
            onDismiss={() => {
              setStartChoiceDismissed(true);
              window.requestAnimationFrame(() => textareaRef.current?.focus());
            }}
            onDocumentImported={(result) => {
              pushAgentNote(
                result.narration || "Imported your document — I didn't find anything to add questions about yet.",
                result.questions.length ? result.questions : undefined
              );
              onTurnComplete();
            }}
            onAwsConnected={(result) => {
              setAwsConnection({ resourceCount: result.resource_count });
              pushAgentNote(
                `Connected your AWS account — found ${result.resource_count} resource${result.resource_count === 1 ? "" : "s"}. I'll cross-reference it automatically as we go, so I won't ask you things a live scan can already answer.`
              );
            }}
          />
        ) : messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <p className="max-w-xs text-sm text-muted-foreground">{placeholder}</p>
          </div>
        ) : null}
        {!historyLoading && messages.map((m, i) => (
          <ChatBubble key={i} message={m} onSendBatch={sendMessageText} sending={streaming} />
        ))}
        {streaming && (
          <div className="flex items-start gap-2.5 animate-pop-in">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted ring-1 ring-border text-[10px] font-bold text-muted-foreground">AI</span>
            <div className="max-w-[88%] rounded-xl rounded-tl-md border border-border bg-muted/40 px-3.5 py-3">
              <div className="flex items-center gap-2">
                <span className="flex gap-1">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-atlas-teal [animation-delay:-0.3s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-atlas-teal [animation-delay:-0.15s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-atlas-teal" />
                </span>
                <span className="text-xs font-medium text-foreground">{liveStatus}</span>
              </div>
              {isPlanningRun && (
                <div className="mt-3 rounded-lg border border-atlas-teal/20 bg-atlas-teal-soft p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <span className="font-semibold text-atlas-teal">Generating migration plan</span>
                    <span className="text-atlas-teal/80">Elapsed {elapsedLabel}</span>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
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
                            className={cn(
                              "h-1.5 w-1.5 rounded-full",
                              isDone ? "bg-atlas-teal" : isActive ? "bg-atlas-amber animate-pulse" : "bg-border"
                            )}
                          />
                          <span
                            className={
                              isActive ? "text-foreground" : isDone ? "text-muted-foreground" : "text-muted-foreground/50"
                            }
                          >
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
        <p role="alert" className="mx-4 mb-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-1.5 text-xs text-destructive">
          {attachError}
        </p>
      )}

      <form onSubmit={handleSubmit} className="flex gap-2 border-t border-border bg-muted/40 p-3">
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
        <Button
          type="button"
          variant="outline"
          className="self-end px-2.5"
          disabled={streaming}
          title="Attach a config file (e.g. docker-compose.yml, a Terraform summary, a README) — read conversationally, same as typing it"
          onClick={() => fileInputRef.current?.click()}
        >
          📎
        </Button>
        <textarea
          id="chat-input"
          ref={textareaRef}
          className="flex-1 resize-none rounded-md border border-input bg-background px-3.5 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
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
        <Button type="submit" className="self-end bg-atlas-teal text-white hover:bg-atlas-teal/90" disabled={streaming || !input.trim()}>
          {streaming ? "Sending…" : "Send"}
        </Button>
      </form>
      {input.length > MAX_MESSAGE_LENGTH * 0.9 && (
        <p className="px-4 pb-2 text-right text-xs text-muted-foreground">
          {input.length.toLocaleString()} / {MAX_MESSAGE_LENGTH.toLocaleString()} characters
        </p>
      )}
    </div>
  );
}
