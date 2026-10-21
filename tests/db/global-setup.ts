import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Client } from "pg";

/**
 * Recreate the test database from scratch: Supabase shim, then every
 * migration in order. Refuses to run against anything but a local host.
 */
export const TEST_DB_NAME = "cryptoscope_test";

export function adminUrl(db = "postgres") {
  const base = process.env.TEST_DATABASE_URL ?? `postgres://postgres@127.0.0.1:${process.env.TEST_DB_PORT ?? 54329}/postgres`;
  const u = new URL(base);
  if (!["127.0.0.1", "localhost", "::1"].includes(u.hostname)) {
    throw new Error(`Refusing to run destructive DB tests against non-local host ${u.hostname}`);
  }
  u.pathname = `/${db}`;
  return u.toString();
}

export default async function setup() {
  const admin = new Client({ connectionString: adminUrl() });
  await admin.connect();
  await admin.query(`drop database if exists ${TEST_DB_NAME} with (force)`);
  for (const role of ["anon", "authenticated", "service_role"]) {
    // Roles are cluster-wide; drop leftovers from a previous run.
    const exists = await admin.query("select 1 from pg_roles where rolname = $1", [role]);
    if (exists.rowCount) {
      await admin.query(`drop owned by ${role} cascade`).catch(() => undefined);
      await admin.query(`drop role ${role}`).catch(() => undefined);
    }
  }
  await admin.query(`create database ${TEST_DB_NAME}`);
  await admin.end();

  const db = new Client({ connectionString: adminUrl(TEST_DB_NAME) });
  await db.connect();
  const root = join(__dirname, "..", "..");
  await db.query(readFileSync(join(root, "tests/db/supabase-shim.sql"), "utf8"));
  const dir = join(root, "supabase/migrations");
  for (const file of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
    try {
      await db.query(readFileSync(join(dir, file), "utf8"));
    } catch (err) {
      throw new Error(`Migration ${file} failed: ${(err as Error).message}`);
    }
  }
  await db.end();
}
