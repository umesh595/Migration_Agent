import type { Metadata } from "next";
import { Outfit, Plus_Jakarta_Sans } from "next/font/google";

import { AuthProvider } from "@/lib/auth";
import { CopilotProvider } from "@/components/CopilotProvider";

import "./globals.css";

const display = Outfit({
  subsets: ["latin"],
  variable: "--font-display",
  weight: ["500", "600", "700", "800"],
});

const body = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-body",
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Aether — Migration Agent",
  description: "Conversational, dependency-aware cloud migration planning.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable}`}>
      <body>
        {/* Served as a static asset, not a JS import — see CopilotProvider.tsx's
            comment for why (Tailwind's PostCSS pipeline can't process CopilotKit's
            own `@layer base` usage). `precedence` is React 19's resource-hoisting
            prop — without it a <link> rendered outside <head> causes a hydration
            mismatch (confirmed live); with it, React hoists this into <head>
            itself, wherever in the tree it's rendered. */}
        <link rel="stylesheet" href="/vendor/copilotkit-v2.css" precedence="default" />
        <div aria-hidden className="bg-aurora" />
        <div aria-hidden className="bg-grid" />
        <AuthProvider>
          <CopilotProvider>{children}</CopilotProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
