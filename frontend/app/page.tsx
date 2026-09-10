"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth";

export default function HomePage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    router.replace(user ? "/sessions" : "/login");
  }, [loading, user, router]);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-atlas-mist dark:bg-background">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-atlas-teal shadow-glow agent-pulse">
        <svg viewBox="0 0 24 24" fill="none" className="h-6 w-6 text-white">
          <path
            d="M4 17V7a2 2 0 0 1 2-2h5l2 2h5a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <p className="text-sm text-muted-foreground">Loading…</p>
    </main>
  );
}
