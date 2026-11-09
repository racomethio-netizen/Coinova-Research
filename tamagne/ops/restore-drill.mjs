#!/usr/bin/env node
/**
 * Restore drill: back up the source database, restore into a fresh scratch database,
 * and prove the restore is usable. Writes a report to docs/ops/.
 *
 *   SOURCE_URL=postgresql://.../tamagne  DRILL_ADMIN_URL=postgresql://.../postgres \
 *   BACKUP_KEY=<base64 32 bytes> node ops/restore-drill.mjs
 *
 * Checks: manifest integrity, identical per-table row counts, all migrations applied,
 * audit hash chain intact, tamper detection on the backup file, and the API reporting
 * ready against the restored database. The scratch database name must end in "_drill".
 */
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { backup, restore, rowCounts } from "./backup.mjs";

const source = process.env.SOURCE_URL;
const admin = process.env.DRILL_ADMIN_URL;
const drillDb = process.env.DRILL_DB ?? "tamagne_restore_drill";
if (!source || !admin || !/_drill$/.test(drillDb)) {
  console.error("SOURCE_URL, DRILL_ADMIN_URL required; DRILL_DB must end with _drill");
  process.exit(2);
}
const target = admin.replace(/\/[^/?]+(\?|$)/, `/${drillDb}$1`);
const psql = (url, sql) => spawnSync("psql", [url, "-tAqc", sql], { encoding: "utf8" });
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
};

const dir = mkdtempSync(join(tmpdir(), "tamagne-drill-"));
const file = join(dir, `tamagne-${new Date().toISOString().slice(0, 10)}.tmb`);
const t0 = Date.now();
const manifest = await backup(source, file);
const backupMs = Date.now() - t0;
check("Backup created and encrypted", readFileSync(file).subarray(0, 6).toString() === "TMGBK1", `${(readFileSync(file).length / 1024).toFixed(0)} KiB in ${backupMs} ms`);
check("Backup contains no plaintext SQL", !readFileSync(file).includes(Buffer.from("CREATE TABLE")));

psql(admin, `drop database if exists ${drillDb}`);
psql(admin, `create database ${drillDb}`);
const t1 = Date.now();
await restore(file, target);
const restoreMs = Date.now() - t1;
check("Restore completed", true, `${restoreMs} ms`);

const restored = await rowCounts(target);
const diffs = Object.keys({ ...manifest.rowCounts, ...restored }).filter((t) => manifest.rowCounts[t] !== restored[t]);
const total = Object.values(restored).reduce((a, b) => a + b, 0);
check("Row counts identical in every table", diffs.length === 0, diffs.length ? diffs.join(", ") : `${Object.keys(restored).length} tables, ${total} rows`);

const mig = psql(target, "select count(*) filter (where finished_at is not null and rolled_back_at is null) || '/' || count(*) from _prisma_migrations").stdout.trim();
const files = spawnSync("ls", [new URL("../packages/db/prisma/migrations", import.meta.url).pathname], { encoding: "utf8" }).stdout.split("\n").filter((f) => /^\d{14}_/.test(f)).length;
check("All migrations recorded as applied", mig === `${files}/${files}`, `${mig} (repository has ${files})`);

const chain = psql(target, "select coalesce(public.audit_log_verify_chain()::text, 'intact')").stdout.trim();
check("Audit hash chain intact after restore", chain === "intact", chain);

const guards = psql(target, "select count(*) from pg_trigger where not tgisinternal and tgname in ('seller_badge_guard','audit_logs_append_only','consent_guard')").stdout.trim();
check("Integrity triggers restored", guards === "3", `${guards}/3`);

// Tampering with the backup must be detected.
const tampered = Buffer.from(readFileSync(file));
tampered[Math.floor(tampered.length / 2)] ^= 1;
writeFileSync(`${file}.tampered`, tampered);
writeFileSync(`${file}.tampered.json`, JSON.stringify({ ...manifest, sha256: (await import("node:crypto")).createHash("sha256").update(tampered).digest("hex") }));
psql(admin, `drop database if exists ${drillDb}_t`);
psql(admin, `create database ${drillDb}_t`);
let tamperDetected = false;
try {
  await restore(`${file}.tampered`, admin.replace(/\/[^/?]+(\?|$)/, `/${drillDb}_t$1`));
} catch {
  tamperDetected = true;
}
psql(admin, `drop database if exists ${drillDb}_t`);
check("Tampered backup is rejected (GCM authentication)", tamperDetected);

// The application starts and reports ready against the restored database.
const key = Buffer.alloc(32, 7).toString("base64");
const api = spawn("node", [new URL("../apps/api/dist/main.js", import.meta.url).pathname], {
  env: { ...process.env, APP_ENV: "test", PORT: "4199", DATABASE_URL: target, FIELD_ENCRYPTION_KEYS: `1:${key}`, FIELD_ENCRYPTION_ACTIVE_KEY_ID: "1", HMAC_PEPPER: key },
  stdio: "ignore",
});
let ready = false;
for (let i = 0; i < 40 && !ready; i++) {
  await new Promise((r) => setTimeout(r, 250));
  try {
    const res = await fetch("http://127.0.0.1:4199/api/v1/health/ready");
    ready = res.ok && (await res.json()).data.checks.database === "ok";
  } catch {}
}
api.kill();
check("API ready against the restored database", ready);

psql(admin, `drop database if exists ${drillDb}`);
const ok = results.every((r) => r.ok);
const date = new Date().toISOString();
const report = `# Restore drill — ${date.slice(0, 10)}

Result: **${ok ? "PASSED" : "FAILED"}** (${date})

| Check | Result | Detail |
|-------|--------|--------|
${results.map((r) => `| ${r.name} | ${r.ok ? "✅" : "❌"} | ${r.detail} |`).join("\n")}

Source: \`${source.replace(/\/\/[^@]*@/, "//***@")}\` · scratch database \`${drillDb}\` (dropped afterwards).
Backup ${backupMs} ms, restore ${restoreMs} ms. Recovery time for this data size: ~${Math.ceil((backupMs + restoreMs) / 1000)} s
(scale with production size; measure again on production-like hardware).
`;
writeFileSync(new URL(`../docs/ops/restore-drill-${date.slice(0, 10)}.md`, import.meta.url), report);
console.log(ok ? "\nDRILL PASSED" : "\nDRILL FAILED");
process.exit(ok ? 0 : 1);
