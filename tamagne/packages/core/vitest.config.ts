import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { conditions: ["tamagne-source"] },
  ssr: { resolve: { conditions: ["tamagne-source"], externalConditions: ["tamagne-source"] } },
  test: { include: ["src/**/*.test.ts"] },
});
