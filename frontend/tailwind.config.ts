import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef4ff",
          100: "#d9e6ff",
          500: "#3457d5",
          600: "#2946b0",
          700: "#20388a",
        },
      },
    },
  },
  plugins: [],
};

export default config;
