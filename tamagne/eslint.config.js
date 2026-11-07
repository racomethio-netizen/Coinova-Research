import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

// Lints packages/* and apps/api. apps/web has its own config (Next.js rules).
export default tseslint.config(
  { ignores: ["**/dist/**", "**/node_modules/**", "**/generated/**", "apps/web/**", "**/.next/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: { globals: { ...globals.node } },
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "no-console": ["error", { allow: ["error", "warn"] }],
      eqeqeq: ["error", "always"],
      "@typescript-eslint/no-explicit-any": "error",
    },
  },
  {
    files: ["**/scripts/**", "**/test/**", "**/*.test.ts"],
    rules: { "no-console": "off" },
  },
);
