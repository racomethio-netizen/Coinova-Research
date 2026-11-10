/**
 * Runs once when a server instance starts, before it handles requests.
 * Validating the environment here makes misconfiguration (including an
 * attempt to enable LIVE_EXECUTION_ENABLED) fail the boot instead of the
 * first request.
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { getEnv } = await import("@/lib/env");
  getEnv();
}
