"use client";

import { useState } from "react";

import { ApiError, downloadExport } from "@/lib/api";
import { cn } from "@/lib/utils";

const FORMATS: { key: "pdf" | "docx"; label: string; icon: string; accent: string }[] = [
  { key: "pdf", label: "Download PDF", icon: "📕", accent: "bg-atlas-coral-soft" },
  { key: "docx", label: "Download DOCX", icon: "📄", accent: "bg-atlas-teal-soft" },
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
            className="group relative flex items-center gap-3 rounded-xl border border-border bg-card p-3.5 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-atlas-teal/40 hover:shadow-glow disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span
              className={cn(
                "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-lg shadow-sm transition-transform group-hover:scale-105",
                f.accent
              )}
            >
              {busy === f.key ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-atlas-teal/30 border-t-atlas-teal" />
              ) : done === f.key ? (
                "✅"
              ) : (
                f.icon
              )}
            </span>
            <div>
              <p className="text-sm font-medium text-foreground">
                {busy === f.key ? "Preparing…" : done === f.key ? "Downloaded!" : f.label}
              </p>
              <p className="text-xs text-muted-foreground">The full 10-deliverable package</p>
            </div>
          </button>
        ))}
      </div>
      {error && (
        <p
          role="alert"
          className="mt-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </p>
      )}
    </div>
  );
}
