"use client";

import { useState } from "react";

import { ApiError, changePassword } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { NavBar } from "@/components/NavBar";

export default function AccountPage() {
  const { user, loading } = useRequireAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

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
    <div className="min-h-screen">
      <NavBar />
      <main className="mx-auto max-w-md px-4 py-8">
        <h1 className="text-xl font-semibold text-slate-900">Your account</h1>
        <p className="mt-1 text-sm text-slate-500">{user.email}</p>

        <form onSubmit={handleSubmit} className="card mt-6 space-y-4">
          <h2 className="text-sm font-semibold text-slate-700">Change password</h2>
          <div>
            <label htmlFor="current-password" className="label">
              Current password
            </label>
            <input
              id="current-password"
              type="password"
              required
              autoComplete="current-password"
              className="input"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="new-password" className="label">
              New password (min 12 characters)
            </label>
            <input
              id="new-password"
              type="password"
              required
              minLength={12}
              autoComplete="new-password"
              className="input"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </div>

          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
          {notice && (
            <p role="status" className="text-sm text-green-700">
              {notice}
            </p>
          )}

          <button type="submit" className="btn-primary w-full" disabled={submitting}>
            {submitting ? "Changing…" : "Change password"}
          </button>
        </form>
      </main>
    </div>
  );
}
