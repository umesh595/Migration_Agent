"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, changePassword, logoutEverywhere } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { NavBar } from "@/components/NavBar";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";

export default function AccountPage() {
  const { user, loading } = useRequireAuth();
  const router = useRouter();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  async function handleLogoutEverywhere() {
    setRevoking(true);
    setRevokeError(null);
    try {
      await logoutEverywhere();
      router.replace("/login");
    } catch (err) {
      setRevokeError(err instanceof ApiError ? err.detail : "Could not revoke sessions.");
    } finally {
      setRevoking(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setNotice("Password changed.");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not change your password.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading || !user) return null;

  return (
    <div className="min-h-screen bg-atlas-mist dark:bg-background">
      <NavBar />
      <main className="mx-auto max-w-md px-4 py-10">
        <div className="blueprint-reveal">
          <Avatar className="mx-auto mb-3 h-16 w-16 shadow-glow">
            <AvatarFallback className="bg-atlas-teal text-2xl font-bold text-white">
              {user.email[0]?.toUpperCase()}
            </AvatarFallback>
          </Avatar>
          <h1 className="text-center font-display text-2xl font-bold text-foreground">Your account</h1>
          <p className="mt-1 text-center text-sm text-muted-foreground">{user.email}</p>
        </div>

        <Card className="mt-7 blueprint-reveal" style={{ animationDelay: "60ms" }}>
          <form onSubmit={handleSubmit} className="space-y-4 p-6">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <span className="text-base">🔒</span> Change password
            </h2>
            <div>
              <Label htmlFor="current-password" className="mb-1.5 block">
                Current password
              </Label>
              <input
                id="current-password"
                type="password"
                required
                autoComplete="current-password"
                className="w-full rounded-md border border-input bg-background px-3.5 py-2.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="new-password" className="mb-1.5 block">
                New password (min 12 characters)
              </Label>
              <input
                id="new-password"
                type="password"
                required
                minLength={12}
                autoComplete="new-password"
                className="w-full rounded-md border border-input bg-background px-3.5 py-2.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
            </div>

            {error && (
              <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </p>
            )}
            {notice && (
              <p role="status" className="rounded-lg border border-atlas-teal/30 bg-atlas-teal-soft px-3 py-2 text-sm text-atlas-teal">
                {notice}
              </p>
            )}

            <Button type="submit" className="w-full bg-atlas-teal text-white hover:bg-atlas-teal/90" disabled={submitting}>
              {submitting ? "Changing…" : "Change password"}
            </Button>
          </form>
        </Card>

        <Card className="mt-6 blueprint-reveal" style={{ animationDelay: "120ms" }}>
          <div className="space-y-3 p-6">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <span className="text-base">🛡️</span> Sessions
            </h2>
            <p className="text-xs text-muted-foreground">
              If you suspect a device or a copied access/refresh token is no longer under your control, revoke
              every outstanding session immediately — this signs you out everywhere, including this device.
            </p>
            {revokeError && (
              <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {revokeError}
              </p>
            )}
            <Button type="button" variant="destructive" className="w-full" disabled={revoking} onClick={handleLogoutEverywhere}>
              {revoking ? "Revoking…" : "Sign out of all sessions"}
            </Button>
          </div>
        </Card>
      </main>
    </div>
  );
}
