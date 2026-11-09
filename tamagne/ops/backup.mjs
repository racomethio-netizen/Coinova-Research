#!/usr/bin/env node
/**
 * Encrypted PostgreSQL backups (no third-party dependencies).
 *
 *   BACKUP_KEY=<base64 32 bytes> DATABASE_URL=... node ops/backup.mjs backup  --out tamagne-YYYYMMDD.tmb
 *   BACKUP_KEY=...               TARGET_URL=...   node ops/backup.mjs restore --in  tamagne-YYYYMMDD.tmb
 *
 * Format: "TMGBK1" | iv (12) | AES-256-GCM(pg_dump --format=custom) | tag (16).
 * A manifest (<file>.json) records the encrypted file's SHA-256 and per-table row counts; it
 * contains no personal data. Keep BACKUP_KEY in the KMS/HSM, separate from the backups.
 * Uploaded documents are already encrypted by the application; back up the object store separately.
 */
import { spawn } from "node:child_process";
import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { closeSync, createReadStream, createWriteStream, fstatSync, openSync, readSync, readFileSync, writeFileSync } from "node:fs";
import { pipeline } from "node:stream/promises";

const MAGIC = Buffer.from("TMGBK1");

function key() {
  const k = Buffer.from(process.env.BACKUP_KEY ?? "", "base64");
  if (k.length !== 32) throw new Error("BACKUP_KEY must be 32 bytes, base64-encoded");
  return k;
}

function run(cmd, args, opts = {}) {
  const p = spawn(cmd, args, { stdio: ["pipe", "pipe", "inherit"], ...opts });
  const done = new Promise((resolve, reject) => p.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} exited with ${code}`)))));
  return { p, done };
}

export async function rowCounts(url) {
  const sql = `select string_agg(format('%s=%s', relname, n), ',' order by relname) from (
    select c.relname, (xpath('/row/n/text()', query_to_xml(format('select count(*) as n from public.%I', c.relname), false, true, '')))[1]::text::bigint as n
    from pg_class c join pg_namespace s on s.oid = c.relnamespace where s.nspname = 'public' and c.relkind = 'r') t`;
  const chunks = [];
  const { p, done } = run("psql", [url, "-tAc", sql]);
  p.stdout.on("data", (c) => chunks.push(c));
  p.stdin.end();
  await done;
  return Object.fromEntries(Buffer.concat(chunks).toString().trim().split(",").filter(Boolean).map((kv) => { const [k, v] = kv.split("="); return [k, Number(v)]; }));
}

export async function backup(url, out) {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key(), iv);
  const file = createWriteStream(out, { mode: 0o600 });
  file.write(Buffer.concat([MAGIC, iv]));
  const { p, done } = run("pg_dump", ["--format=custom", "--no-owner", "--no-privileges", "--dbname", url]);
  p.stdin.end();
  await Promise.all([pipeline(p.stdout, cipher, file, { end: false }), done]);
  await new Promise((r) => file.end(cipher.getAuthTag(), r));
  const sha256 = createHash("sha256").update(readFileSync(out)).digest("hex");
  const manifest = { file: out.split("/").pop(), sha256, createdAt: new Date().toISOString(), format: "TMGBK1/pg_dump-custom", rowCounts: await rowCounts(url) };
  writeFileSync(`${out}.json`, JSON.stringify(manifest, null, 2));
  return manifest;
}

export async function restore(input, targetUrl) {
  const manifest = JSON.parse(readFileSync(`${input}.json`, "utf8"));
  const sha = createHash("sha256").update(readFileSync(input)).digest("hex");
  if (sha !== manifest.sha256) throw new Error("backup file does not match its manifest (corrupted or tampered)");
  const fd = openSync(input, "r");
  const size = fstatSync(fd).size;
  const head = Buffer.alloc(MAGIC.length + 12);
  readSync(fd, head, 0, head.length, 0);
  const tag = Buffer.alloc(16);
  readSync(fd, tag, 0, 16, size - 16);
  closeSync(fd);
  if (!head.subarray(0, MAGIC.length).equals(MAGIC)) throw new Error("not a TAMAGNE backup file");
  const decipher = createDecipheriv("aes-256-gcm", key(), head.subarray(MAGIC.length));
  decipher.setAuthTag(tag);
  const { p, done } = run("pg_restore", ["--no-owner", "--no-privileges", "--exit-on-error", "--dbname", targetUrl]);
  // GCM authenticates at the end: a tampered file makes the decipher throw and the restore fails.
  await Promise.all([pipeline(createReadStream(input, { start: head.length, end: size - 17 }), decipher, p.stdin), done]);
  return manifest;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [cmd, flag, path] = process.argv.slice(2);
  try {
    if (cmd === "backup" && flag === "--out") console.log(JSON.stringify(await backup(process.env.DATABASE_URL, path), null, 2));
    else if (cmd === "restore" && flag === "--in") await restore(path, process.env.TARGET_URL);
    else throw new Error("usage: backup --out <file> | restore --in <file>");
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }
}
