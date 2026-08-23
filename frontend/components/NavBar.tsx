"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth";

export function NavBar() {
  const { user, logout } = useAuth();
  const router = useRouter();

  return (
    <header className="glass-nav sticky top-0 z-40">
      <nav aria-label="Primary" className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3.5">
        <Link href="/sessions" className="group flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-grad-primary shadow-glow transition-transform group-hover:scale-105">
            <svg viewBox="0 0 24 24" fill="none" className="h-4.5 w-4.5 text-white">
              <path
                d="M4 17V7a2 2 0 0 1 2-2h5l2 2h5a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <span className="font-display text-[15px] font-bold tracking-tight text-white">
            <span className="text-gradient">Aether</span>
          </span>
        </Link>
        <div className="flex items-center gap-1.5 text-sm">
          {user?.is_admin && (
            <Link
              href="/admin"
              className="rounded-lg px-3 py-1.5 font-medium text-slate-300 transition-colors hover:bg-white/[0.06] hover:text-white"
            >
              Admin
            </Link>
          )}
          {user && (
            <Link
              href="/account"
              className="rounded-lg px-3 py-1.5 font-medium text-slate-400 transition-colors hover:bg-white/[0.06] hover:text-white"
            >
              {user.email}
            </Link>
          )}
          <button
            type="button"
            className="btn-secondary ml-1 !py-1.5 !text-xs"
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
