"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/ThemeToggle";

export function NavBar() {
  const { user, logout } = useAuth();
  const router = useRouter();

  return (
    <header className="glass-nav sticky top-0 z-40">
      <nav aria-label="Primary" className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3.5">
        <Link href="/sessions" className="group flex items-center gap-2.5">
          <span className="relative flex h-8 w-8 items-center justify-center rounded-lg bg-atlas-teal shadow-glow transition-transform group-hover:scale-105">
            <svg viewBox="0 0 24 24" fill="none" className="h-4.5 w-4.5 text-white">
              <path
                d="M4 17V7a2 2 0 0 1 2-2h5l2 2h5a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinejoin="round"
              />
            </svg>
            <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-atlas-coral agent-pulse" />
          </span>
          <span className="font-display text-[15px] font-bold tracking-tight text-foreground">
            <span className="text-gradient">Aether</span>
          </span>
        </Link>
        <div className="flex items-center gap-1.5 text-sm">
          <ThemeToggle />
          {user?.is_admin && (
            <Link
              href="/admin"
              className="rounded-lg px-3 py-1.5 font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              Admin
            </Link>
          )}
          {user && (
            <Link
              href="/account"
              className="rounded-lg px-3 py-1.5 font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              {user.email}
            </Link>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="ml-1 text-xs"
            onClick={() => {
              logout();
              router.replace("/login");
            }}
          >
            Sign out
          </Button>
        </div>
      </nav>
    </header>
  );
}
