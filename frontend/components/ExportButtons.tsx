"use client";

import { useState } from "react";

import { ApiError, downloadExport } from "@/lib/api";

export function ExportButtons({ sessionId }: { sessionId: string }) {
  const [busy, setBusy] = useState<"markdown" | "docx" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handle(format: "markdown" | "docx") {
    setBusy(format);
    setError(null);
    try {
      await downloadExport(sessionId, format);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Export failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <div className="flex gap-2">
        <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => handle("markdown")}>
          {busy === "markdown" ? "Preparing…" : "Download Markdown"}
        </button>
        <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => handle("docx")}>
          {busy === "docx" ? "Preparing…" : "Download DOCX"}
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}
