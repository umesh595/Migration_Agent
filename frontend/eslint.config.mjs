// Deliberately does NOT go through eslint-config-next's `next/core-web-vitals`
// FlatCompat-wrapped bundle: that bundle legacy-wraps eslint-plugin-react/jsx-a11y,
// and doing so crashes ESLint 9's flat-config validator with a circular-JSON error
// while formatting an unrelated schema warning (upstream tooling bug, not this
// app's code — reproduces identically across eslint 9.27.0 and 9.39.5). The
// package's own natively-flat exports (`@next/eslint-plugin-next`'s `core-web-vitals`
// config, `typescript-eslint`'s `recommended`) cover the rules that actually matter
// here — Next.js App Router conventions and TypeScript correctness — without going
// through that broken bridge.
import nextPlugin from "@next/eslint-plugin-next";
import reactHooksPlugin from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

const eslintConfig = [
  { ignores: [".next/**", "node_modules/**"] },
  ...tseslint.configs.recommended,
  {
    plugins: { "@next/next": nextPlugin, "react-hooks": reactHooksPlugin },
    rules: {
      ...nextPlugin.configs["core-web-vitals"].rules,
      ...reactHooksPlugin.configs.recommended.rules,
    },
  },
  {
    rules: {
      // Server/client boundary code (API client, streaming parser) intentionally
      // handles loosely-typed JSON payloads from the network.
      "@typescript-eslint/no-explicit-any": "off",
      // Flags the standard "fetch on mount, setState with the result" effect
      // pattern used throughout this app's data loading (session state, user
      // listings, auth bootstrap) as an error. That pattern is the documented way
      // to synchronize with an external system (the API) in React — not a bug
      // this rule should block on. Aimed at React Compiler-era codebases; this one
      // doesn't use the compiler.
      "react-hooks/set-state-in-effect": "off",
    },
  },
];

export default eslintConfig;
