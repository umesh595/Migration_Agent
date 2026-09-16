"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";

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
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-atlas-mist blueprint-grid px-4 dark:bg-background">
      <div className="w-full max-w-sm blueprint-reveal">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-atlas-teal shadow-glow-lg">
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
          <h1 className="font-display text-2xl font-bold tracking-tight text-foreground">
            <span className="text-gradient">Aether</span> Migration Agent
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">Sign in to continue your migration study</p>
        </div>

        <Card>
          <form
            onSubmit={handleSubmit}
            className="space-y-5 p-6"
            aria-describedby={error ? "login-error" : undefined}
          >
            <div>
              <Label htmlFor="email" className="mb-1.5 block">
                Email
              </Label>
              <input
                id="email"
                type="email"
                required
                autoComplete="username"
                className="w-full rounded-md border border-input bg-background px-3.5 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="password" className="mb-1.5 block">
                Password
              </Label>
              <input
                id="password"
                type="password"
                required
                autoComplete="current-password"
                className="w-full rounded-md border border-input bg-background px-3.5 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>

            {error && (
              <p
                id="login-error"
                role="alert"
                className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {error}
              </p>
            )}

            <Button
              type="submit"
              className="w-full bg-atlas-teal text-base text-white hover:bg-atlas-teal/90"
              disabled={submitting}
            >
              {submitting ? (
                <>
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  Signing in…
                </>
              ) : (
                "Sign in"
              )}
            </Button>
          </form>
        </Card>
      </div>
    </main>
  );
}
