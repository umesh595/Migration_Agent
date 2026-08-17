"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, streamMessage } from "@/lib/api";
import type { NodeCompleteEvent, TurnCompleteEvent } from "@/lib/types";

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

  return (
    <div
      className={
        message.role === "user"
          ? "ml-8 rounded-lg bg-brand-50 p-2 text-sm text-slate-800"
          : message.role === "error"
            ? "rounded-lg bg-red-50 p-2 text-sm text-red-700"
            : "mr-8 whitespace-pre-line rounded-lg bg-slate-100 p-2 text-sm text-slate-800"
      }
      role={message.role === "error" ? "alert" : undefined}
    >
      <div className="whitespace-pre-line">{visibleText}</div>
      {isLongUserMessage && (
        <button
          type="button"
          className="mt-2 text-xs font-medium text-brand-700 hover:text-brand-800"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Show less" : `Show full input (${message.text.length.toLocaleString()} characters)`}
        </button>
      )}
    </div>
  );
}

export function ChatPanel({
  sessionId,
  placeholder,
  disabled,
  disabledReason,
  onTurnComplete,
}: {
  sessionId: string;
  placeholder: string;
  disabled: boolean;
  disabledReason?: string;
  onTurnComplete: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [liveStatus, setLiveStatus] = useState("");
  const [attachError, setAttachError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, liveStatus]);

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
    setLiveStatus("Sending…");

    try {
      for await (const evt of streamMessage(sessionId, text)) {
        if (evt.event === "node_complete") {
          const data = evt.data as NodeCompleteEvent;
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
      setLiveStatus("");
      onTurnComplete();
    }
  }

  return (
    <div className="card flex h-[560px] flex-col">
      <h3 className="mb-2 text-sm font-semibold text-slate-700">Conversation</h3>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto pr-1" aria-live="polite">
        {messages.length === 0 && (
          <p className="text-sm text-slate-400">{placeholder}</p>
        )}
        {messages.map((m, i) => (
          <ChatBubble key={i} message={m} />
        ))}
        {streaming && <p className="text-xs text-slate-400">{liveStatus}</p>}
      </div>

      {attachError && (
        <p role="alert" className="mt-2 text-xs text-red-600">
          {attachError}
        </p>
      )}

      <form onSubmit={handleSubmit} className="mt-3 flex gap-2">
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
          className="btn-secondary self-end"
          disabled={disabled || streaming}
          title="Attach a config file (e.g. docker-compose.yml, a Terraform summary, a README) — read conversationally, same as typing it"
          onClick={() => fileInputRef.current?.click()}
        >
          Attach
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
        <p className="mt-1 text-right text-xs text-slate-400">
          {input.length.toLocaleString()} / {MAX_MESSAGE_LENGTH.toLocaleString()} characters
        </p>
      )}
    </div>
  );
}
