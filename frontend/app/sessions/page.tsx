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
      <main className="mx-auto max-w-4xl px-4 py-8">
        <h1 className="text-2xl font-semibold text-slate-900">Migration studies</h1>
        <p className="mt-1 text-sm text-slate-500">
          Every study you start is durable — close the tab any time and resume it from here later.
        </p>

        <form onSubmit={handleCreate} className="card mt-6 flex items-end gap-3">
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
          <button type="submit" className="btn-primary" disabled={creating}>
            {creating ? "Creating…" : "Start new study"}
          </button>
        </form>

        {error && (
          <p role="alert" className="mt-4 text-sm text-red-600">
            {error}
          </p>
        )}

        <ul className="mt-6 space-y-2">
          {sessions === null && <li className="text-sm text-slate-400">Loading…</li>}
          {sessions?.length === 0 && (
            <li className="text-sm text-slate-400">No studies yet — start one above.</li>
          )}
          {sessions?.map((s) => (
            <li key={s.id}>
              <Link
                href={`/sessions/${s.id}`}
                className="card flex items-center justify-between hover:border-brand-500"
              >
                <div>
                  <p className="font-medium text-slate-900">{s.name}</p>
                  <p className="text-xs text-slate-400">{s.token_usage.toLocaleString()} tokens used</p>
                </div>
                <StatusBadge status={s.status} />
              </Link>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
