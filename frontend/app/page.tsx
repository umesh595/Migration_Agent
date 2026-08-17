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
    <main className="flex min-h-screen items-center justify-center">
      <p className="text-slate-500">Loading…</p>
    </main>
  );
}
