"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ApiError, createSession, listSessions } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { SessionSummary } from "@/lib/types";
import { NavBar } from "@/components/NavBar";
import { StatusBadge } from "@/components/StatusBadge";

export default function SessionsPage() {
  const { user, loading } = useRequireAuth();
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!user) return;
    listSessions()
      .then(setSessions)
      .catch((err) => setError(err instanceof ApiError ? err.detail : "Could not load sessions."));
  }, [user]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const session = await createSession(newName.trim() || "Untitled migration");
      router.push(`/sessions/${session.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not create a session.");
      setCreating(false);
    }
  }

  if (loading || !user) return null;

  return (
    <div className="min-h-screen">
      <NavBar />
      <main className="mx-auto max-w-4xl px-4 py-10">
        <div className="animate-fade-up">
          <h1 className="font-display text-3xl font-bold tracking-tight text-white">
            Migration <span className="text-gradient">studies</span>
          </h1>
          <p className="mt-2 text-sm text-slate-400">
            Every study you start is durable — close the tab any time and resume it from here later.
          </p>
        </div>

        <form
          onSubmit={handleCreate}
          className="card-glow mt-7 flex flex-col items-stretch gap-3 animate-fade-up sm:flex-row sm:items-end"
          style={{ animationDelay: "60ms" }}
        >
          <div className="flex-1">
            <label htmlFor="new-session-name" className="label">
              New migration study name
            </label>
            <input
              id="new-session-name"
              className="input"
              placeholder="e.g. Storefront platform — AWS to GCP"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
          </div>
          <button type="submit" className="btn-primary whitespace-nowrap" disabled={creating}>
            {creating ? "Creating…" : "+ Start new study"}
          </button>
        </form>

        {error && (
          <p
            role="alert"
            className="mt-4 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300"
          >
            {error}
          </p>
        )}

        <ul className="mt-7 space-y-3">
          {sessions === null && (
            <>
              {[0, 1].map((i) => (
                <li key={i} className="card h-[68px] shimmer" />
              ))}
            </>
          )}
          {sessions?.length === 0 && (
            <li className="card flex flex-col items-center gap-2 py-10 text-center">
              <span className="text-3xl">✨</span>
              <p className="text-sm text-slate-400">No studies yet — start one above to begin.</p>
            </li>
          )}
          {sessions?.map((s, i) => (
            <li key={s.id} className="animate-fade-up" style={{ animationDelay: `${Math.min(i, 6) * 40}ms` }}>
              <Link
                href={`/sessions/${s.id}`}
                className="card group flex items-center justify-between gap-4 transition-all duration-200 hover:-translate-y-0.5 hover:border-brand-400/40 hover:shadow-glow"
              >
                <div className="flex items-center gap-3.5">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/[0.05] text-lg ring-1 ring-white/10 transition-colors group-hover:bg-brand-500/10">
                    🗂️
                  </span>
                  <div>
                    <p className="font-medium text-slate-100 transition-colors group-hover:text-white">{s.name}</p>
                    <p className="text-xs text-slate-500">{s.token_usage.toLocaleString()} tokens used</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <StatusBadge status={s.status} />
                  <span className="text-slate-500 transition-transform group-hover:translate-x-0.5 group-hover:text-slate-300">
                    →
                  </span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
