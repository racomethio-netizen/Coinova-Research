/**
 * Scheduled jobs (run by cron / the platform scheduler):
 *   npm run job -w @tamagne/api -- expiry
 * Uses the same validated configuration as the API.
 */
import { NestFactory } from "@nestjs/core";
import { AppModule } from "../src/app.module.js";
import { OperationsService } from "../src/admin/operations.service.js";
import { loadConfig } from "../src/config/env.js";

const job = process.argv[2];
if (job !== "expiry") {
  console.error("usage: run-job expiry");
  process.exit(2);
}
const app = await NestFactory.createApplicationContext(AppModule.forRoot(loadConfig()), { logger: ["error", "warn"] });
try {
  const result = await app.get(OperationsService).runExpiryJob();
  console.log(JSON.stringify({ job, ...result }));
} finally {
  await app.close();
}
