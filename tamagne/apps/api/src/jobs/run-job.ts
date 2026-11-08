/**
 * Scheduled jobs (run by cron / the platform scheduler):
 *   npm run job -w @tamagne/api -- expiry   (dev)
 *   node apps/api/dist/jobs/run-job.js expiry   (container)
 * Uses the same validated configuration as the API.
 */
import { NestFactory } from "@nestjs/core";
import { AppModule } from "../app.module.js";
import { OperationsService } from "../admin/operations.service.js";
import { loadConfig } from "../config/env.js";

const job = process.argv[2];
if (job !== "expiry") {
  console.error("usage: run-job expiry");
  process.exit(2);
}
const app = await NestFactory.createApplicationContext(AppModule.forRoot(loadConfig()), { logger: ["error", "warn"] });
try {
  const result = await app.get(OperationsService).runExpiryJob();
  process.stdout.write(`${JSON.stringify({ job, ...result })}\n`);
} finally {
  await app.close();
}
