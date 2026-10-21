/**
 * Trigger a background job through the app's authenticated cron endpoint.
 *
 *   APP_URL=https://your-app.example CRON_SECRET=... npm run job -- refresh-markets
 *
 * Jobs run inside the web service (which holds the database credentials);
 * this script only makes the authenticated HTTP call, so it can run from a
 * Render Cron Job, CI, or a developer machine.
 */
const job = process.argv[2];
const base = process.env.APP_URL ?? process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";
const secret = process.env.CRON_SECRET;

async function main() {
  if (!job || !/^[a-z][a-z0-9-]+$/.test(job)) {
    console.error("usage: npm run job -- <job-name>");
    process.exit(2);
  }
  if (!secret) {
    console.error("CRON_SECRET is required");
    process.exit(2);
  }
  const res = await fetch(new URL(`/api/cron/${job}`, base), { method: "POST", headers: { Authorization: `Bearer ${secret}` } });
  const body = await res.text();
  console.log(body);
  if (!res.ok) process.exit(1);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
