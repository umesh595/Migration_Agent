"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth";

export function NavBar() {
  const { user, logout } = useAuth();
  const router = useRouter();

  return (
    <header className="border-b border-slate-200 bg-white">
      <nav
        aria-label="Primary"
        className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3"
      >
        <Link href="/sessions" className="font-semibold text-slate-900">
          Migration Agent
        </Link>
        <div className="flex items-center gap-4 text-sm">
          {user?.is_admin && (
            <Link href="/admin" className="text-slate-600 hover:text-brand-600">
              Admin
            </Link>
          )}
          {user && (
            <Link href="/account" className="text-slate-500 hover:text-brand-600">
              {user.email}
            </Link>
          )}
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              logout();
              router.replace("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </nav>
    </header>
  );
}
