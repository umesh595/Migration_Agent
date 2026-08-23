import type { Metadata } from "next";
import { Outfit, Plus_Jakarta_Sans } from "next/font/google";

import { AuthProvider } from "@/lib/auth";

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
        <div aria-hidden className="bg-aurora" />
        <div aria-hidden className="bg-grid" />
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
