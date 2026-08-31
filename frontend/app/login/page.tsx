"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      router.replace("/sessions");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not sign in. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
      {/* Floating orbs — purely decorative, behind the card */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-[8%] top-[15%] h-64 w-64 rounded-full bg-aurora-violet/30 blur-3xl animate-float-slow"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute right-[10%] bottom-[12%] h-72 w-72 rounded-full bg-aurora-cyan/25 blur-3xl animate-float-slow"
        style={{ animationDelay: "-3s" }}
      />

      <div className="w-full max-w-sm animate-fade-up">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-grad-primary shadow-glow-lg animate-pop-in">
            <svg viewBox="0 0 24 24" fill="none" className="h-7 w-7 text-white">
              <path
                d="M4 17V7a2 2 0 0 1 2-2h5l2 2h5a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinejoin="round"
              />
              <path d="M8 13.5 10.5 11 13 13.5 16 10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <h1 className="font-display text-2xl font-bold tracking-tight text-white">
            <span className="text-gradient">Aether</span> Migration Agent
          </h1>
          <p className="mt-2 text-sm text-slate-400">Sign in to continue your migration study</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="card-glow space-y-5 animate-fade-up"
          style={{ animationDelay: "80ms" }}
          aria-describedby={error ? "login-error" : undefined}
        >
          <div>
            <label htmlFor="email" className="label">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="username"
              className="input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="password" className="label">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              autoComplete="current-password"
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {error && (
            <p
              id="login-error"
              role="alert"
              className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300"
            >
              {error}
            </p>
          )}

          <button type="submit" className="btn-primary w-full text-base" disabled={submitting}>
            {submitting ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                Signing in…
              </>
            ) : (
              "Sign in"
            )}
          </button>
        </form>
      </div>
    </main>
  );
}
