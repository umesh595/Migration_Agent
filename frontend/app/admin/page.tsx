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
          <p role="alert" className="card text-sm text-red-700">
            Admin privileges are required to view this page.
          </p>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <NavBar />
      <main className="mx-auto max-w-3xl px-4 py-8">
        <h1 className="text-2xl font-semibold text-slate-900">User administration</h1>
        <p className="mt-1 text-sm text-slate-500">
          There is no self-service sign-up (FR-A5) — every account is created, disabled, or reset here.
        </p>

        {error && (
          <p role="alert" className="card mt-4 border-red-200 bg-red-50 text-sm text-red-700">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="card mt-4 border-green-200 bg-green-50 text-sm text-green-800">
            {notice}
          </p>
        )}

        <form onSubmit={handleCreate} className="card mt-4 space-y-3">
          <h2 className="text-sm font-semibold text-slate-700">Provision a new account</h2>
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
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
            Grant admin privileges
          </label>
          <button type="submit" className="btn-primary" disabled={creating}>
            {creating ? "Creating…" : "Create account"}
          </button>
        </form>

        <div className="mt-6">
          <h2 className="mb-2 text-sm font-semibold text-slate-700">All accounts</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-slate-500">
                  <th className="pb-2 pr-4">Email</th>
                  <th className="pb-2 pr-4">Role</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users?.map((u) => (
                  <tr key={u.id} className="border-t border-slate-100">
                    <td className="py-2 pr-4">{u.email}</td>
                    <td className="py-2 pr-4">{u.is_admin ? "Admin" : "User"}</td>
                    <td className="py-2 pr-4">
                      <span className={`badge ${u.is_active ? "bg-green-100 text-green-800" : "bg-slate-200 text-slate-600"}`}>
                        {u.is_active ? "Active" : "Disabled"}
                      </span>
                    </td>
                    <td className="py-2">
                      <div className="flex gap-2">
                        <button
                          type="button"
                          className={u.is_active ? "btn-danger text-xs" : "btn-secondary text-xs"}
                          disabled={busyUserId === u.id}
                          onClick={() => handleToggleActive(u)}
                        >
                          {u.is_active ? "Disable" : "Enable"}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary text-xs"
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
            {users?.length === 0 && <p className="mt-2 text-sm text-slate-400">No accounts yet.</p>}
          </div>
        </div>
      </main>
    </div>
  );
}
