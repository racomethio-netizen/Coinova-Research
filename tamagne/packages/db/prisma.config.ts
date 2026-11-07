import { defineConfig } from "prisma/config";

// DATABASE_URL comes from the environment (never from a committed file).
// `prisma generate` does not need a database, so a placeholder is allowed there.
export default defineConfig({
  schema: "prisma/schema.prisma",
  migrations: { path: "prisma/migrations" },
  datasource: {
    url: process.env.DATABASE_URL ?? "postgresql://invalid:invalid@localhost:1/invalid",
  },
});
