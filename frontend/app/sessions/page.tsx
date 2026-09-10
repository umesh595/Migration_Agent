"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ApiError, createSession, listSessions } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { SessionSummary } from "@/lib/types";
import { NavBar } from "@/components/NavBar";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

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
    <div className="min-h-screen bg-atlas-mist dark:bg-background">
      <NavBar />
      <main className="mx-auto max-w-4xl px-4 py-10">
        <div className="blueprint-reveal">
          <h1 className="font-display text-3xl font-bold tracking-tight text-foreground">
            Migration <span className="text-gradient">studies</span>
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Every study you start is durable — close the tab any time and resume it from here later.
          </p>
        </div>

        <Card className="mt-7 blueprint-reveal" style={{ animationDelay: "60ms" }}>
          <form onSubmit={handleCreate} className="flex flex-col items-stretch gap-3 p-6 sm:flex-row sm:items-end">
            <div className="flex-1">
              <Label htmlFor="new-session-name" className="mb-1.5 block">
                New migration study name
              </Label>
              <input
                id="new-session-name"
                className="w-full rounded-md border border-input bg-background px-3.5 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                placeholder="e.g. Storefront platform — AWS to GCP"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
            </div>
            <Button type="submit" className="whitespace-nowrap bg-atlas-teal text-white hover:bg-atlas-teal/90" disabled={creating}>
              {creating ? "Creating…" : "+ Start new study"}
            </Button>
          </form>
        </Card>

        {error && (
          <p
            role="alert"
            className="mt-4 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {error}
          </p>
        )}

        <ul className="mt-7 space-y-3">
          {sessions === null && (
            <>
              {[0, 1].map((i) => (
                <li key={i}>
                  <Skeleton className="h-[68px] rounded-xl" />
                </li>
              ))}
            </>
          )}
          {sessions?.length === 0 && (
            <li>
              <Card className="flex flex-col items-center gap-2 py-10 text-center">
                <span className="text-3xl">✨</span>
                <p className="text-sm text-muted-foreground">No studies yet — start one above to begin.</p>
              </Card>
            </li>
          )}
          {sessions?.map((s, i) => (
            <li key={s.id} className="blueprint-reveal" style={{ animationDelay: `${Math.min(i, 6) * 40}ms` }}>
              <Link href={`/sessions/${s.id}`} className="block">
                <Card className="group flex items-center justify-between gap-4 p-4 transition-all duration-200 hover:-translate-y-0.5 hover:border-atlas-teal/40 hover:shadow-glow">
                  <div className="flex items-center gap-3.5">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-muted text-lg ring-1 ring-border transition-colors group-hover:bg-atlas-teal-soft">
                      🗂️
                    </span>
                    <div>
                      <p className="font-medium text-foreground">{s.name}</p>
                      <p className="text-xs text-muted-foreground">{s.token_usage.toLocaleString()} tokens used</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <StatusBadge status={s.status} />
                    <span className="text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-foreground">
                      →
                    </span>
                  </div>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
