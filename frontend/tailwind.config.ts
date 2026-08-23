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
          50: "#eef2ff",
          100: "#e0e7ff",
          200: "#c7d2fe",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
          800: "#3730a3",
          900: "#312e81",
        },
        aurora: {
          violet: "#7c3aed",
          indigo: "#4f46e5",
          cyan: "#06b6d4",
          pink: "#ec4899",
          amber: "#f59e0b",
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
        "grad-primary": "linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%)",
        "grad-cool": "linear-gradient(135deg, #06b6d4 0%, #6366f1 100%)",
        "grad-warm": "linear-gradient(135deg, #f59e0b 0%, #ec4899 100%)",
        "grad-card": "linear-gradient(160deg, rgba(255,255,255,0.06), rgba(255,255,255,0.015))",
        "grad-mesh":
          "radial-gradient(at 20% 20%, rgba(124,58,237,0.35) 0px, transparent 55%)," +
          "radial-gradient(at 80% 0%, rgba(6,182,212,0.28) 0px, transparent 50%)," +
          "radial-gradient(at 90% 80%, rgba(236,72,153,0.25) 0px, transparent 50%)," +
          "radial-gradient(at 10% 90%, rgba(79,70,229,0.3) 0px, transparent 50%)",
      },
      boxShadow: {
        glow: "0 0 40px -6px rgba(99,102,241,0.55)",
        "glow-lg": "0 0 80px -10px rgba(124,58,237,0.45)",
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
          "0%": { boxShadow: "0 0 0 0 rgba(99,102,241,0.55)" },
          "70%": { boxShadow: "0 0 0 10px rgba(99,102,241,0)" },
          "100%": { boxShadow: "0 0 0 0 rgba(99,102,241,0)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
