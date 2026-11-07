import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { conditions: ["tamagne-source"] },
  ssr: { resolve: { conditions: ["tamagne-source"], externalConditions: ["tamagne-source"] } },
  test: {
    include: ["test/**/*.test.ts"],
    // Tests share one disposable database; run files sequentially.
    fileParallelism: false,
    testTimeout: 20_000,
  },
});
