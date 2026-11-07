import { PrismaPg } from "@prisma/adapter-pg";
import { PrismaClient } from "./generated/prisma/client.js";

export * from "./generated/prisma/client.js";

export interface CreatePrismaClientOptions {
  /** Defaults to process.env.DATABASE_URL. */
  connectionString?: string;
  /** Pool size per process. */
  max?: number;
}

/**
 * Creates a Prisma client over node-postgres. One per process; the caller owns
 * `$disconnect()`. TLS is configured through the connection string
 * (`sslmode=verify-full` in production; see docs/DEPLOYMENT.md).
 */
export function createPrismaClient(options: CreatePrismaClientOptions = {}): PrismaClient {
  const connectionString = options.connectionString ?? process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error("DATABASE_URL is not set");
  }
  const adapter = new PrismaPg({ connectionString, max: options.max ?? 10 });
  return new PrismaClient({ adapter });
}
