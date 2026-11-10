import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit tests cover pure modules only (no React Native imports).
export default defineConfig({
  resolve: {
    alias: {
      "@nexus/shared": fileURLToPath(new URL("../packages/shared/src/index.ts", import.meta.url)),
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      // The shared package must use this app's copies (CI installs only mobile/).
      zod: fileURLToPath(new URL("./node_modules/zod", import.meta.url)),
      "decimal.js": fileURLToPath(new URL("./node_modules/decimal.js", import.meta.url)),
    },
  },
  test: { include: ["src/**/*.test.ts"], environment: "node" },
});
