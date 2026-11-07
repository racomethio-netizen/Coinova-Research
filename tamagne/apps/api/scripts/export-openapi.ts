// Writes the OpenAPI document to docs/api/openapi.json without a database connection.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { buildOpenApiDocument, createApp } from "../src/app.factory.js";
import { loadConfig } from "../src/config/env.js";

const key = Buffer.alloc(32, 1).toString("base64");
const config = loadConfig({
  APP_ENV: "test",
  DATABASE_URL: "postgresql://unused@127.0.0.1:1/unused",
  FIELD_ENCRYPTION_KEYS: `1:${key}`,
  FIELD_ENCRYPTION_ACTIVE_KEY_ID: "1",
  HMAC_PEPPER: key,
});
const app = await createApp(config);
await app.init();
const out = resolve(dirname(fileURLToPath(import.meta.url)), "../../../docs/api/openapi.json");
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, `${JSON.stringify(buildOpenApiDocument(app), null, 2)}\n`);
await app.close();
console.log(`wrote ${out}`);
