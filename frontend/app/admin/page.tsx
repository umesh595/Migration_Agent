"use client";

import { useEffect, useState } from "react";

import { adminCreateUser, adminListUsers, adminResetPassword, adminSetActive, ApiError } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { AdminUser } from "@/lib/types";
import { NavBar } from "@/components/NavBar";

export default function AdminPage() {
  const { user, loading } = useRequireAuth();
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [creating, setCreating] = useState(false);
  const [busyUserId, setBusyUserId] = useState<string | null>(null);

  async function refresh() {
    try {
      setUsers(await adminListUsers());
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not load users.");
    }
  }

  useEffect(() => {
    if (user?.is_admin) refresh();
  }, [user]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError(null);
    setNotice(null);
    try {
      await adminCreateUser(email, password, isAdmin);
      setEmail("");
      setPassword("");
      setIsAdmin(false);
      setNotice(`Account created for ${email}.`);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not create the account.");
    } finally {
      setCreating(false);
    }
  }

  async function handleToggleActive(u: AdminUser) {
    setBusyUserId(u.id);
    setError(null);
    try {
      await adminSetActive(u.id, !u.is_active);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not update this account.");
    } finally {
      setBusyUserId(null);
    }
  }

  async function handleResetPassword(u: AdminUser) {
    setBusyUserId(u.id);
    setError(null);
    setNotice(null);
    try {
      const result = await adminResetPassword(u.id);
      setNotice(
        `New temporary password for ${result.email}: ${result.temporary_password} — relay this to them now, it will not be shown again.`
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not reset this account's password.");
    } finally {
      setBusyUserId(null);
    }
  }

  if (loading || !user) return null;

  if (!user.is_admin) {
    return (
      <div className="min-h-screen">
        <NavBar />
        <main className="mx-auto max-w-3xl px-4 py-8">
          <p role="alert" className="card text-sm text-rose-300">
            Admin privileges are required to view this page.
          </p>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <NavBar />
      <main className="mx-auto max-w-3xl px-4 py-10">
        <div className="animate-fade-up">
          <h1 className="font-display text-3xl font-bold tracking-tight text-white">
            User <span className="text-gradient">administration</span>
          </h1>
        </div>

        {error && (
          <p role="alert" className="card mt-4 border-rose-500/30 bg-rose-500/10 text-sm text-rose-300">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="card mt-4 border-emerald-500/30 bg-emerald-500/10 text-sm text-emerald-300">
            {notice}
          </p>
        )}

        <form onSubmit={handleCreate} className="card-glow mt-6 space-y-3 animate-fade-up" style={{ animationDelay: "60ms" }}>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
            <span className="text-base">➕</span> Provision a new account
          </h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="new-user-email" className="label">
                Email
              </label>
              <input
                id="new-user-email"
                type="email"
                required
                className="input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="new-user-password" className="label">
                Initial password (min 12 characters)
              </label>
              <input
                id="new-user-password"
                type="text"
                required
                minLength={12}
                className="input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              className="h-4 w-4 accent-brand-500"
              checked={isAdmin}
              onChange={(e) => setIsAdmin(e.target.checked)}
            />
            Grant admin privileges
          </label>
          <button type="submit" className="btn-primary" disabled={creating}>
            {creating ? "Creating…" : "Create account"}
          </button>
        </form>

        <div className="mt-8 animate-fade-up" style={{ animationDelay: "120ms" }}>
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-200">
            <span className="text-base">👥</span> All accounts
          </h2>
          <div className="panel overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-slate-400">
                    <th className="px-4 py-3 font-medium">Email</th>
                    <th className="px-4 py-3 font-medium">Role</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users?.map((u) => (
                    <tr key={u.id} className="border-b border-white/[0.06] transition-colors hover:bg-white/[0.02]">
                      <td className="px-4 py-3 text-slate-200">{u.email}</td>
                      <td className="px-4 py-3">
                        {u.is_admin ? (
                          <span className="badge border-violet-400/30 bg-violet-400/10 text-violet-300">Admin</span>
                        ) : (
                          <span className="text-slate-400">User</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`badge ${
                            u.is_active
                              ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                              : "border-slate-400/30 bg-slate-400/10 text-slate-400"
                          }`}
                        >
                          <span className={`h-1.5 w-1.5 rounded-full ${u.is_active ? "bg-emerald-400" : "bg-slate-400"}`} />
                          {u.is_active ? "Active" : "Disabled"}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex gap-2">
                          <button
                            type="button"
                            className={u.is_active ? "btn-danger !px-2.5 !py-1 !text-xs" : "btn-secondary !px-2.5 !py-1 !text-xs"}
                            disabled={busyUserId === u.id}
                            onClick={() => handleToggleActive(u)}
                          >
                            {u.is_active ? "Disable" : "Enable"}
                          </button>
                          <button
                            type="button"
                            className="btn-secondary !px-2.5 !py-1 !text-xs"
                            disabled={busyUserId === u.id}
                            onClick={() => handleResetPassword(u)}
                          >
                            Reset password
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {users?.length === 0 && <p className="px-4 py-6 text-sm text-slate-500">No accounts yet.</p>}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
