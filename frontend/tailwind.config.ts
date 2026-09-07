import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ["var(--font-display)", "ui-sans-serif", "system-ui"],
        sans: ["var(--font-body)", "ui-sans-serif", "system-ui"],
      },
      colors: {
        brand: {
          50: "#eef9ff",
          100: "#d9f0fb",
          200: "#b7e2f3",
          300: "#83cce8",
          400: "#43abd1",
          500: "#1f8fb8",
          600: "#167396",
          700: "#155d79",
          800: "#174d64",
          900: "#163f53",
        },
        aurora: {
          violet: "#2563eb",
          indigo: "#155d79",
          cyan: "#06b6d4",
          pink: "#14b8a6",
          amber: "#d97706",
        },
        // A second, hotter accent scale — deliberately distinct from the cool
        // `brand` teal, reserved for high-energy moments (staged/selected
        // states, live streaming, "something changed") so it reads as a signal,
        // not just more decoration.
        pulse: {
          50: "#faf5ff",
          100: "#f3e8ff",
          200: "#e9d5ff",
          300: "#d8b4fe",
          400: "#c084fc",
          500: "#a855f7",
          600: "#9333ea",
          700: "#7e22ce",
          800: "#6b21a8",
          900: "#581c87",
        },
        ink: {
          950: "#05060f",
          900: "#0a0b18",
          850: "#0d0f20",
          800: "#111327",
          700: "#171933",
          600: "#1f2140",
          500: "#2a2c52",
        },
      },
      backgroundImage: {
        "grad-primary": "linear-gradient(135deg, #155d79 0%, #1f8fb8 58%, #14b8a6 100%)",
        "grad-cool": "linear-gradient(135deg, #0f766e 0%, #1f8fb8 100%)",
        "grad-warm": "linear-gradient(135deg, #d97706 0%, #0f766e 100%)",
        "grad-card": "linear-gradient(160deg, rgba(255,255,255,0.06), rgba(255,255,255,0.015))",
        "grad-mesh":
          "linear-gradient(180deg, rgba(7,16,24,0.98) 0%, rgba(5,9,14,1) 100%)," +
          "radial-gradient(at 14% 8%, rgba(31,143,184,0.16) 0px, transparent 42%)," +
          "radial-gradient(at 86% 4%, rgba(20,184,166,0.12) 0px, transparent 38%)",
        "grad-pulse": "linear-gradient(135deg, #a855f7 0%, #ec4899 55%, #06b6d4 100%)",
        "grad-pulse-soft": "linear-gradient(135deg, rgba(168,85,247,0.16), rgba(236,72,153,0.1), rgba(6,182,212,0.14))",
      },
      boxShadow: {
        glow: "0 0 32px -10px rgba(31,143,184,0.45)",
        "glow-lg": "0 0 64px -18px rgba(20,184,166,0.35)",
        "glow-pulse": "0 0 32px -8px rgba(168,85,247,0.55)",
        panel: "0 1px 0 0 rgba(255,255,255,0.06) inset, 0 20px 50px -20px rgba(0,0,0,0.6)",
        "panel-sm": "0 1px 0 0 rgba(255,255,255,0.06) inset, 0 10px 30px -12px rgba(0,0,0,0.5)",
      },
      animation: {
        "aurora-drift": "aurora-drift 22s ease-in-out infinite",
        "float-slow": "float-slow 8s ease-in-out infinite",
        shimmer: "shimmer 2.5s linear infinite",
        "fade-up": "fade-up 0.5s cubic-bezier(0.16, 1, 0.3, 1) both",
        "pop-in": "pop-in 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) both",
        "pulse-ring": "pulse-ring 2.2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
      keyframes: {
        "aurora-drift": {
          "0%, 100%": { transform: "translate3d(0,0,0) scale(1)" },
          "50%": { transform: "translate3d(-2%,3%,0) scale(1.08)" },
        },
        "float-slow": {
          "0%, 100%": { transform: "translateY(0px)" },
          "50%": { transform: "translateY(-10px)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(14px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "pop-in": {
          from: { opacity: "0", transform: "scale(0.94)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        "pulse-ring": {
          "0%": { boxShadow: "0 0 0 0 rgba(31,143,184,0.5)" },
          "70%": { boxShadow: "0 0 0 10px rgba(31,143,184,0)" },
          "100%": { boxShadow: "0 0 0 0 rgba(31,143,184,0)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
