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
import { PrivacyService } from "../privacy/privacy.service.js";
import { RekeyService } from "../security/rekey.service.js";

const job = process.argv[2];
const JOBS = ["expiry", "retention", "deletions", "rekey"];
if (!job || !JOBS.includes(job)) {
  console.error(`usage: run-job ${JOBS.join("|")}`);
  process.exit(2);
}
const app = await NestFactory.createApplicationContext(AppModule.forRoot(loadConfig()), { logger: ["error", "warn"] });
try {
  const result =
    job === "expiry"
      ? await app.get(OperationsService).runExpiryJob()
      : job === "retention"
        ? await app.get(PrivacyService).runRetentionJob()
        : job === "deletions"
          ? await app.get(PrivacyService).runDeletionJob()
          : await app.get(RekeyService).runRekeyJob();
  process.stdout.write(`${JSON.stringify({ job, ...result })}\n`);
} finally {
  await app.close();
}
