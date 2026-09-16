"use client";

import { useEffect, useState } from "react";

import { adminCreateUser, adminListUsers, adminResetPassword, adminSetActive, ApiError } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { AdminUser } from "@/lib/types";
import { NavBar } from "@/components/NavBar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

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
      <div className="min-h-screen bg-atlas-mist dark:bg-background">
        <NavBar />
        <main className="mx-auto max-w-3xl px-4 py-8">
          <p role="alert" className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            Admin privileges are required to view this page.
          </p>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-atlas-mist dark:bg-background">
      <NavBar />
      <main className="mx-auto max-w-3xl px-4 py-10">
        <div className="blueprint-reveal">
          <h1 className="font-display text-3xl font-bold tracking-tight text-foreground">
            User <span className="text-gradient">administration</span>
          </h1>
        </div>

        {error && (
          <p role="alert" className="mt-4 rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="mt-4 rounded-xl border border-atlas-teal/30 bg-atlas-teal-soft p-4 text-sm text-atlas-teal">
            {notice}
          </p>
        )}

        <Card className="mt-6 blueprint-reveal" style={{ animationDelay: "60ms" }}>
          <form onSubmit={handleCreate} className="space-y-3 p-6">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <span className="text-base">➕</span> Provision a new account
            </h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="new-user-email" className="mb-1.5 block">
                  Email
                </Label>
                <input
                  id="new-user-email"
                  type="email"
                  required
                  className="w-full rounded-md border border-input bg-background px-3.5 py-2.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="new-user-password" className="mb-1.5 block">
                  Initial password (min 12 characters)
                </Label>
                <input
                  id="new-user-password"
                  type="text"
                  required
                  minLength={12}
                  className="w-full rounded-md border border-input bg-background px-3.5 py-2.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm text-foreground">
              <Checkbox checked={isAdmin} onCheckedChange={(checked) => setIsAdmin(checked === true)} />
              Grant admin privileges
            </label>
            <Button type="submit" className="bg-atlas-teal text-white hover:bg-atlas-teal/90" disabled={creating}>
              {creating ? "Creating…" : "Create account"}
            </Button>
          </form>
        </Card>

        <div className="mt-8 blueprint-reveal" style={{ animationDelay: "120ms" }}>
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
            <span className="text-base">👥</span> All accounts
          </h2>
          <Card className="overflow-hidden p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users?.map((u) => (
                  <TableRow key={u.id}>
                    <TableCell className="text-foreground">{u.email}</TableCell>
                    <TableCell>
                      {u.is_admin ? (
                        <Badge variant="secondary">Admin</Badge>
                      ) : (
                        <span className="text-muted-foreground">User</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant={u.is_active ? "teal" : "outline"} className="gap-1.5">
                        <span className={cn("h-1.5 w-1.5 rounded-full", u.is_active ? "bg-atlas-teal" : "bg-muted-foreground")} />
                        {u.is_active ? "Active" : "Disabled"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-2">
                        <Button
                          type="button"
                          variant={u.is_active ? "destructive" : "outline"}
                          size="sm"
                          className="text-xs"
                          disabled={busyUserId === u.id}
                          onClick={() => handleToggleActive(u)}
                        >
                          {u.is_active ? "Disable" : "Enable"}
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="text-xs"
                          disabled={busyUserId === u.id}
                          onClick={() => handleResetPassword(u)}
                        >
                          Reset password
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {users?.length === 0 && <p className="px-4 py-6 text-sm text-muted-foreground">No accounts yet.</p>}
          </Card>
        </div>
      </main>
    </div>
  );
}
