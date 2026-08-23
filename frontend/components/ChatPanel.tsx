"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, streamMessage } from "@/lib/api";
import type { NodeCompleteEvent, SessionStatus, TurnCompleteEvent } from "@/lib/types";

interface ChatMessage {
  role: "user" | "agent" | "status" | "error";
  text: string;
}

// Mirrors the backend's MessageRequest.message max_length (see sessions.py) — kept
// in sync manually since the two run in separate deploys; the server is the real
// enforcement point, this is just an honest client-side heads-up.
const MAX_MESSAGE_LENGTH = 50_000;
const COLLAPSE_MESSAGE_LENGTH = 1_200;
const MESSAGE_PREVIEW_LENGTH = 700;

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

function ChatBubble({ message }: { message: ChatMessage }) {
  const [expanded, setExpanded] = useState(false);
  const isLongUserMessage = message.role === "user" && message.text.length > COLLAPSE_MESSAGE_LENGTH;
  const visibleText =
    isLongUserMessage && !expanded
      ? `${message.text.slice(0, MESSAGE_PREVIEW_LENGTH).trimEnd()}\n\n...`
      : message.text;

  if (message.role === "user") {
    return (
      <div className="flex justify-end animate-pop-in">
        <div className="max-w-[85%] rounded-2xl rounded-tr-md bg-grad-primary px-3.5 py-2.5 text-sm text-white shadow-lg shadow-brand-900/30">
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
      <div className="max-w-[85%] whitespace-pre-line rounded-2xl rounded-tl-md border border-white/10 bg-white/[0.04] px-3.5 py-2.5 text-sm text-slate-200">
        {visibleText}
      </div>
    </div>
  );
}

export function ChatPanel({
  sessionId,
  placeholder,
  disabled,
  disabledReason,
  workflowStatus,
  componentCount,
  onTurnComplete,
}: {
  sessionId: string;
  placeholder: string;
  disabled: boolean;
  disabledReason?: string;
  workflowStatus?: SessionStatus;
  componentCount?: number;
  onTurnComplete: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [liveStatus, setLiveStatus] = useState("");
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [attachError, setAttachError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, liveStatus]);

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

    try {
      for await (const evt of streamMessage(sessionId, text, messageId)) {
        if (evt.event === "node_complete") {
          const data = evt.data as NodeCompleteEvent;
          setActiveNode(data.node);
          setLiveStatus(narrateNode(data));
        } else if (evt.event === "turn_complete") {
          const data = evt.data as TurnCompleteEvent;
          setLiveStatus("Turn complete.");
          if (data.error) {
            setMessages((prev) => [...prev, { role: "error", text: data.error as string }]);
          } else {
            const parts: string[] = [];
            if (data.narration) parts.push(data.narration);
            if (data.clarifying_questions?.length) {
              parts.push(
                "I need to clarify a few things before continuing:\n" +
                  data.clarifying_questions.map((q) => `• ${q}`).join("\n")
              );
            } else if (data.questions?.length) {
              parts.push(data.questions.map((q) => `• ${q}`).join("\n"));
            }
            setMessages((prev) => [...prev, { role: "agent", text: parts.join("\n\n") || "Understood." }]);
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
    <div className="card-glow flex h-[560px] flex-col !p-0 overflow-hidden">
      <div className="flex items-center gap-2 border-b border-white/10 bg-white/[0.02] px-4 py-3">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-grad-primary text-[11px]">💬</span>
        <h3 className="text-sm font-semibold text-slate-200">Conversation</h3>
        {streaming && <span className="ml-auto h-2 w-2 rounded-full bg-emerald-400 animate-pulse-ring" />}
      </div>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4" aria-live="polite">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <span className="text-2xl opacity-60">✨</span>
            <p className="max-w-xs text-sm text-slate-500">{placeholder}</p>
          </div>
        )}
        {messages.map((m, i) => (
          <ChatBubble key={i} message={m} />
        ))}
        {streaming && (
          <div className="flex items-start gap-2.5 animate-pop-in">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white/[0.06] ring-1 ring-white/10 text-xs">
              🤖
            </span>
            <div className="max-w-[88%] rounded-2xl rounded-tl-md border border-white/10 bg-white/[0.04] px-3.5 py-3">
              <div className="flex items-center gap-2">
                <span className="flex gap-1">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400 [animation-delay:-0.3s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400 [animation-delay:-0.15s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400" />
                </span>
                <span className="text-xs font-medium text-slate-300">{liveStatus}</span>
              </div>
              {isPlanningRun && (
                <div className="mt-3 rounded-xl border border-sky-400/20 bg-sky-500/[0.06] p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <span className="font-semibold text-sky-200">Generating migration plan</span>
                    <span className="text-sky-300/80">Elapsed {elapsedLabel}</span>
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
          disabled={disabled || streaming}
          title="Attach a config file (e.g. docker-compose.yml, a Terraform summary, a README) — read conversationally, same as typing it"
          onClick={() => fileInputRef.current?.click()}
        >
          📎
        </button>
        <textarea
          id="chat-input"
          className="input flex-1 resize-none"
          rows={2}
          value={input}
          disabled={disabled || streaming}
          maxLength={MAX_MESSAGE_LENGTH}
          placeholder={
            disabled
              ? disabledReason
              : "Describe your system, answer the questions above, or attach a config file…"
          }
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSubmit(e);
            }
          }}
        />
        <button type="submit" className="btn-primary self-end" disabled={disabled || streaming || !input.trim()}>
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
