import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";
import globals from "globals";

export default tseslint.config(
  { ignores: ["dist", "node_modules", ".vite"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": "off",
    },
  },
  {
    // Node-side tooling (e.g. bench-preview-engine.mjs) — not part of the
    // browser bundle, runs under plain Node via `npm run bench:engine`.
    files: ["scripts/**/*.mjs"],
    languageOptions: { globals: globals.node },
  },
);
