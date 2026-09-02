"use client";

// CopilotKit's CSS is served as a static asset (public/vendor/copilotkit-v2.css,
// linked from app/layout.tsx's <head>) instead of imported here — importing it
// through the normal JS graph routes it through this project's Tailwind
// PostCSS pipeline, which fails on CopilotKit's own `@layer base` usage
// (Tailwind requires `@layer` to appear alongside a `@tailwind` directive in
// the same processed file; this project's `@tailwind base` lives in
// globals.css, not CopilotKit's bundled stylesheet).
import { CopilotKit } from "@copilotkit/react-core/v2";

// Points at the Next.js API route (app/api/copilotkit/[[...slug]]/route.ts),
// NOT directly at the FastAPI backend — that route is the Node "Runtime"
// relay CopilotKit's hooks (useHumanInTheLoop, useFrontendTool) need; the
// FastAPI backend's own AG-UI endpoint (POST /sessions/ag-ui) speaks pure
// AG-UI and is what the relay proxies to. See AG-UI adoption plan.
export function CopilotProvider({ children }: { children: React.ReactNode }) {
  return <CopilotKit runtimeUrl="/api/copilotkit">{children}</CopilotKit>;
}
