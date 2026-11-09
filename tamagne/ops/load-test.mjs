#!/usr/bin/env node
/**
 * Load test against a DISPOSABLE database (never production): starts the built API and web
 * server locally, then measures
 *   1. public verification lookups (API), found and not found
 *   2. the server-rendered public verification page (web → API)
 *   3. concurrent audited writes (anonymous fraud reports), then checks that every write
 *      landed exactly once and that the audit hash chain is intact.
 *
 *   npm run build
 *   DATABASE_URL=postgresql://postgres@127.0.0.1:54339/tamagne_test REDIS_URL=redis://127.0.0.1:63799/0 \
 *     node ops/load-test.mjs [--duration 15]
 *
 * Each request carries a random X-Forwarded-For (TRUST_PROXY_HOPS=1) to simulate many clients,
 * so per-IP limits behave as in production rather than throttling the generator.
 * Writes a report to docs/ops/load-test-<date>.md.
 */
import { spawn } from "node:child_process";
import { randomBytes, randomInt } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { availableParallelism, cpus, totalmem } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import autocannon from "autocannon";
import pg from "pg";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const DATABASE_URL = process.env.DATABASE_URL;
const REDIS_URL = process.env.REDIS_URL;
if (!DATABASE_URL || !REDIS_URL) throw new Error("DATABASE_URL and REDIS_URL are required");
if (!/(_test|_load|_drill)(\?|$)/.test(DATABASE_URL)) throw new Error("refusing: the database name must end in _test, _load or _drill");
const durIdx = process.argv.indexOf("--duration");
const DURATION = durIdx > 0 ? Number(process.argv[durIdx + 1]) : 15;
const API_PORT = 4400;
const WEB_PORT = 3400;
const WEB_ORIGIN = `http://127.0.0.1:${WEB_PORT}`;
const API = `http://127.0.0.1:${API_PORT}`;

const key = () => randomBytes(32).toString("base64");
const randomIp = () => `10.${randomInt(256)}.${randomInt(256)}.${randomInt(1, 255)}`;

function start(cmd, args, env, readyUrl) {
  const child = spawn(cmd, args, { cwd: root, env: { ...process.env, ...env }, stdio: ["ignore", "ignore", "pipe"] });
  let stderr = "";
  child.stderr.on("data", (d) => (stderr += d));
  const ready = (async () => {
    for (let i = 0; i < 120; i++) {
      if (child.exitCode !== null) throw new Error(`${args[0]} exited: ${stderr.slice(-2000)}`);
      try {
        if ((await fetch(readyUrl)).ok) return;
      } catch {
        /* not up yet */
      }
      await new Promise((r) => setTimeout(r, 250));
    }
    throw new Error(`${args[0]} did not become ready`);
  })();
  return { child, ready };
}

function run(opts) {
  return new Promise((resolve, reject) => {
    const inst = autocannon({ duration: DURATION, ...opts }, (err, res) => (err ? reject(err) : resolve(res)));
    autocannon.track(inst, { renderProgressBar: false, renderResultsTable: false, renderLatencyTable: false });
  });
}

const summarize = (name, r) => ({
  name,
  connections: r.connections,
  requests: r.requests.total,
  rps: Math.round(r.requests.average),
  p50: r.latency.p50,
  p97_5: r.latency.p97_5,
  p99: r.latency.p99,
  max: r.latency.max,
  "2xx": r["2xx"],
  non2xx: r.non2xx,
  errors: r.errors + r.timeouts,
});

const db = new pg.Client({ connectionString: DATABASE_URL });
await db.connect();
const seller = (
  await db.query(`select "tamagneId" from seller_profiles where "isPublic" and "deletedAt" is null and "accountStatus" = 'ACTIVE' order by "createdAt" limit 1`)
).rows[0]?.tamagneId;
if (!seller) throw new Error("no public seller in this database; run the API tests first to create fixtures");

const apiEnv = {
  APP_ENV: "development",
  PORT: String(API_PORT),
  DATABASE_URL,
  REDIS_URL,
  PUBLIC_WEB_URL: WEB_ORIGIN,
  CORS_ORIGINS: WEB_ORIGIN,
  FIELD_ENCRYPTION_KEYS: `1:${key()}`,
  FIELD_ENCRYPTION_ACTIVE_KEY_ID: "1",
  HMAC_PEPPER: key(),
  OPENAPI_ENABLED: "false",
  SMS_PROVIDER: "disabled",
  STORAGE_DRIVER: "disabled",
  SCANNER: "disabled",
  TRUST_PROXY_HOPS: "1",
  VERIFY_LOOKUP_LIMIT_PER_MIN: "120",
  LOG_FORMAT: "json", // production setting; output is discarded
};
const api = start("node", ["--enable-source-maps", "apps/api/dist/main.js"], apiEnv, `${API}/api/v1/health/ready`);
const web = start(
  "node",
  ["apps/web/.next/standalone/apps/web/server.js"],
  { PORT: String(WEB_PORT), HOSTNAME: "127.0.0.1", API_INTERNAL_URL: API, NODE_ENV: "production" },
  `${WEB_ORIGIN}/en`,
);
const results = [];
let integrity;
try {
  await Promise.all([api.ready, web.ready]);
  const withIp = (req) => ({ ...req, headers: { ...req.headers, "x-forwarded-for": randomIp() } });

  // Warm-up (JIT, connection pools).
  await run({ url: `${API}/api/v1/verify/${seller}`, connections: 10, duration: 3, requests: [{ setupRequest: withIp }] });

  results.push(summarize("API lookup, existing seller", await run({ url: `${API}/api/v1/verify/${seller}`, connections: 50, requests: [{ setupRequest: withIp }] })));
  results.push(summarize("API lookup, unknown ID (404)", await run({ url: `${API}/api/v1/verify/TMG-ZZZZZZZZZZ`, connections: 50, requests: [{ setupRequest: withIp }] })));
  const sameIp = await run({ url: `${API}/api/v1/verify/${seller}`, connections: 10, duration: 5, headers: { "x-forwarded-for": "10.0.0.1" } });
  results.push({ ...summarize("API lookup, one IP (rate limit check)", sameIp), note: "limit 120/min/IP: expect ~120 × 2xx, rest 429" });
  results.push(summarize("Web page /en/verify/<id> (SSR)", await run({ url: `${WEB_ORIGIN}/en/verify/${seller}`, connections: 20, requests: [{ setupRequest: withIp }] })));

  // Audited writes.
  const csrfRes = await fetch(`${API}/api/v1/auth/csrf`);
  const csrfToken = (await csrfRes.json()).data.csrfToken;
  const cookie = csrfRes.headers.getSetCookie().map((c) => c.split(";")[0]).join("; ");
  const before = await db.query(`select (select count(*) from fraud_reports)::int as reports, (select count(*) from audit_logs where action = 'report.submitted')::int as audits`);
  const body = JSON.stringify({ seller, category: "OTHER", description: "Load-test fixture report; synthetic, no real person involved." });
  const writes = await run({
    url: `${API}/api/v1/reports`,
    connections: 25,
    method: "POST",
    headers: { "content-type": "application/json", origin: WEB_ORIGIN, cookie, "x-csrf-token": csrfToken },
    body,
    requests: [{ setupRequest: withIp }],
  });
  results.push(summarize("POST /reports (audited write)", writes));
  // The generator stops counting at the deadline; requests still in flight complete server-side.
  // Wait until the row count is stable before comparing.
  let last = -1;
  for (;;) {
    const n = (await db.query(`select count(*)::int as n from fraud_reports`)).rows[0].n;
    if (n === last) break;
    last = n;
    await new Promise((r) => setTimeout(r, 1000));
  }
  const after = await db.query(`select (select count(*) from fraud_reports)::int as reports, (select count(*) from audit_logs where action = 'report.submitted')::int as audits`);
  const chain = await db.query(`select public.audit_log_verify_chain() as broken`);
  const accepted = writes["2xx"];
  integrity = {
    accepted,
    reportsCreated: after.rows[0].reports - before.rows[0].reports,
    auditEntries: after.rows[0].audits - before.rows[0].audits,
    chainIntact: chain.rows[0].broken === null,
  };
  // Remove the synthetic reports (audit entries stay: the log is append-only by design).
  await db.query(`delete from fraud_reports where description = $1`, [JSON.parse(body).description]);
} finally {
  api.child.kill("SIGTERM");
  web.child.kill("SIGTERM");
  await db.end();
}

// Up to one in-flight request per connection may complete after the generator's deadline.
const inFlightOk = (n) => n >= integrity.accepted && n <= integrity.accepted + 25;
const ok =
  integrity.chainIntact &&
  integrity.auditEntries === integrity.reportsCreated &&
  inFlightOk(integrity.reportsCreated) &&
  results.every((r) => r.errors === 0);
const date = new Date().toISOString().slice(0, 10);
const cpu = cpus()[0]?.model ?? "unknown";
const md = `# Load test — ${date}

Result: **${ok ? "PASSED" : "FAILED"}** (integrity and zero transport errors; latency figures are
measurements, not pass/fail thresholds).

Environment: ${availableParallelism()} vCPU (${cpu}), ${Math.round(totalmem() / 2 ** 30)} GiB RAM, Node ${process.version}.
Load generator, API (1 process), web (1 process), PostgreSQL and Redis all share this one
machine, so these numbers are a **lower bound** for a single replica and say nothing about
production network latency. Repeat on production-like hardware before launch.
API: \`LOG_FORMAT=json\`, Redis-backed rate limits, ${DURATION}s per scenario after a 3s warm-up.
Clients: random \`X-Forwarded-For\` per request (many distinct IPs) unless noted.

| Scenario | Conn. | Requests | Req/s | p50 ms | p97.5 ms | p99 ms | max ms | 2xx | non-2xx | errors |
|---|---|---|---|---|---|---|---|---|---|---|
${results.map((r) => `| ${r.name} | ${r.connections} | ${r.requests} | ${r.rps} | ${r.p50} | ${r.p97_5} | ${r.p99} | ${r.max} | ${r["2xx"]} | ${r.non2xx} | ${r.errors} |`).join("\n")}

${results.filter((r) => r.note).map((r) => `- ${r.name}: ${r.note}.`).join("\n")}

## Audited writes under concurrency

| Check | Result |
|---|---|
| Reports accepted (202) | ${integrity.accepted} |
| Report rows created (accepted + up to 25 in flight at the deadline) | ${integrity.reportsCreated} ${inFlightOk(integrity.reportsCreated) ? "✅" : "❌"} |
| \`report.submitted\` audit entries = report rows | ${integrity.auditEntries} ${integrity.auditEntries === integrity.reportsCreated ? "✅" : "❌"} |
| \`audit_log_verify_chain()\` | ${integrity.chainIntact ? "intact ✅" : "BROKEN ❌"} |

Synthetic report rows were deleted afterwards; their audit entries remain (append-only log).
`;
const out = join(root, "docs", "ops", `load-test-${date}.md`);
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, md);
console.table(results);
console.log(integrity);
console.log(`${ok ? "LOAD TEST PASSED" : "LOAD TEST FAILED"} — report: ${out}`);
process.exit(ok ? 0 : 1);
