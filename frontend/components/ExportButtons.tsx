"use client";

import { useState } from "react";

import { ApiError, downloadExport } from "@/lib/api";

const FORMATS: { key: "pdf" | "docx"; label: string; icon: string; gradient: string }[] = [
  { key: "pdf", label: "Download PDF", icon: "📕", gradient: "bg-grad-cool" },
  { key: "docx", label: "Download DOCX", icon: "📄", gradient: "bg-grad-primary" },
];

export function ExportButtons({ sessionId }: { sessionId: string }) {
  const [busy, setBusy] = useState<"pdf" | "docx" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<"pdf" | "docx" | null>(null);

  async function handle(format: "pdf" | "docx") {
    setBusy(format);
    setError(null);
    try {
      await downloadExport(sessionId, format);
      setDone(format);
      setTimeout(() => setDone(null), 2200);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Export failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {FORMATS.map((f) => (
          <button
            key={f.key}
            type="button"
            disabled={busy !== null}
            onClick={() => handle(f.key)}
            className="group relative flex items-center gap-3 rounded-xl border border-white/10 bg-white/3 p-3.5 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-white/20 hover:bg-white/6 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-lg ${f.gradient} shadow-md transition-transform group-hover:scale-105`}
            >
              {busy === f.key ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              ) : done === f.key ? (
                "✅"
              ) : (
                f.icon
              )}
            </span>
            <div>
              <p className="text-sm font-medium text-slate-100">
                {busy === f.key ? "Preparing…" : done === f.key ? "Downloaded!" : f.label}
              </p>
              <p className="text-xs text-slate-500">The full 10-deliverable package</p>
            </div>
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" className="mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          {error}
        </p>
      )}
    </div>
  );
}
