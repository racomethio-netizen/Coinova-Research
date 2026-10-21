import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import http from "node:http";
import { Client } from "pg";
import dbSetup, { adminUrl, TEST_DB_NAME } from "../db/global-setup";
import { POSTGREST_PORT, PROXY_PORT, TEST_JWT_SECRET } from "./jwt";

/**
 * Integration harness: real PostgreSQL + real migrations + PostgREST, with a
 * tiny proxy so supabase-js (which calls `${url}/rest/v1/...`) talks to it.
 * The proxy also emulates GET /auth/v1/admin/users/:id from auth.users,
 * which is the only Auth admin call exercised by these tests.
 *
 * Requires: scripts/db-test.sh up, and a PostgREST binary at $POSTGREST_BIN
 * (default .tmp/postgrest). See docs/TESTING.md.
 */
let pgrst: ChildProcess | undefined;
let proxy: http.Server | undefined;

async function waitFor(url: string, ms = 15_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try {
      const r = await fetch(url);
      if (r.status < 500) return;
    } catch {
      /* not yet */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

export default async function setup() {
  const bin = process.env.POSTGREST_BIN ?? ".tmp/postgrest";
  if (!existsSync(bin)) throw new Error(`PostgREST binary not found at ${bin}. See docs/TESTING.md#integration-tests.`);

  await dbSetup();
  const db = new Client({ connectionString: adminUrl(TEST_DB_NAME) });
  await db.connect();
  await db.query(`do $$ begin if not exists (select 1 from pg_roles where rolname = 'authenticator') then create role authenticator login noinherit; end if; end $$;`);
  await db.query("grant anon, authenticated, service_role to authenticator");
  await db.end();

  const dbUri = adminUrl(TEST_DB_NAME).replace("postgres@", "authenticator@");
  pgrst = spawn(bin, [], {
    env: {
      PGRST_DB_URI: dbUri,
      PGRST_DB_SCHEMAS: "public",
      PGRST_DB_ANON_ROLE: "anon",
      PGRST_JWT_SECRET: TEST_JWT_SECRET,
      PGRST_SERVER_PORT: String(POSTGREST_PORT),
      PGRST_SERVER_HOST: "127.0.0.1",
      PGRST_DB_POOL: "5",
      PGRST_LOG_LEVEL: "error",
      NODE_ENV: "test",
    },
    stdio: ["ignore", "inherit", "inherit"],
  });

  const lookup = new Client({ connectionString: adminUrl(TEST_DB_NAME) });
  await lookup.connect();
  proxy = http.createServer(async (req, res) => {
    const url = req.url ?? "/";
    const adminUser = /^\/auth\/v1\/admin\/users\/([0-9a-f-]{36})/.exec(url);
    if (adminUser) {
      const { rows } = await lookup.query("select id, email, created_at from auth.users where id = $1", [adminUser[1]]);
      res.writeHead(rows.length ? 200 : 404, { "content-type": "application/json" });
      res.end(JSON.stringify(rows[0] ? { id: rows[0].id, email: rows[0].email, created_at: rows[0].created_at, aud: "authenticated", app_metadata: {}, user_metadata: {} } : { msg: "not found" }));
      return;
    }
    if (!url.startsWith("/rest/v1")) {
      res.writeHead(404).end();
      return;
    }
    const upstream = http.request(
      { host: "127.0.0.1", port: POSTGREST_PORT, path: url.replace(/^\/rest\/v1/, "") || "/", method: req.method, headers: { ...req.headers, host: `127.0.0.1:${POSTGREST_PORT}` } },
      (up) => {
        res.writeHead(up.statusCode ?? 502, up.headers);
        up.pipe(res);
      },
    );
    upstream.on("error", () => res.writeHead(502).end());
    req.pipe(upstream);
  });
  await new Promise<void>((r) => proxy!.listen(PROXY_PORT, "127.0.0.1", r));
  await waitFor(`http://127.0.0.1:${POSTGREST_PORT}/`);

  return async () => {
    pgrst?.kill("SIGTERM");
    await new Promise<void>((r) => proxy?.close(() => r()));
    await lookup.end();
  };
}
