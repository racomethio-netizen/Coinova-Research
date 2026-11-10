/**
 * HTTP load test with pass/fail thresholds (NEXUS Phase 10). No dependencies.
 *
 *   npx tsx scripts/load-test.ts --base http://127.0.0.1:3100 --duration 30 --concurrency 20 \
 *     --routes /,/markets,/api/health,/api/markets?perPage=25 --p95-ms 800 --max-error-rate 0.01
 *
 * Only GET requests to public routes; never point it at a site you do not operate.
 * It refuses non-local targets unless --allow-remote is given (staging, with owner approval;
 * remember that Render and Supabase may rate-limit or bill for the traffic).
 * Prints a JSON report and exits 1 when a threshold is exceeded.
 */

interface Options {
  base: string;
  routes: string[];
  durationS: number;
  concurrency: number;
  p95Ms: number;
  maxErrorRate: number;
  timeoutMs: number;
  allowRemote: boolean;
}

function parseArgs(argv: string[]): Options {
  const get = (name: string, fallback: string) => {
    const i = argv.indexOf(`--${name}`);
    return i >= 0 && argv[i + 1] ? argv[i + 1]! : fallback;
  };
  const num = (name: string, fallback: number, min: number, max: number) => {
    const v = Number(get(name, String(fallback)));
    if (!Number.isFinite(v) || v < min || v > max) throw new Error(`--${name} must be between ${min} and ${max}`);
    return v;
  };
  return {
    base: get("base", "http://127.0.0.1:3100").replace(/\/$/, ""),
    routes: get("routes", "/,/markets,/pricing,/api/health,/api/markets?perPage=25").split(",").filter(Boolean),
    durationS: num("duration", 30, 1, 3600),
    concurrency: num("concurrency", 20, 1, 500),
    p95Ms: num("p95-ms", 800, 1, 60_000),
    maxErrorRate: num("max-error-rate", 0.01, 0, 1),
    timeoutMs: num("timeout-ms", 10_000, 100, 120_000),
    allowRemote: argv.includes("--allow-remote"),
  };
}

export function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return Number.NaN;
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[idx]!;
}

interface RouteStats {
  latencies: number[];
  errors: number;
  rateLimited: number;
  statuses: Record<string, number>;
}

async function main() {
  const o = parseArgs(process.argv.slice(2));
  const host = new URL(o.base).hostname;
  if (!["127.0.0.1", "localhost", "::1"].includes(host) && !o.allowRemote) {
    throw new Error(`Refusing to load-test ${host}: pass --allow-remote only for a staging site you operate.`);
  }
  const stats = new Map<string, RouteStats>(o.routes.map((r) => [r, { latencies: [], errors: 0, rateLimited: 0, statuses: {} }]));
  const deadline = Date.now() + o.durationS * 1000;
  let next = 0;

  async function worker() {
    while (Date.now() < deadline) {
      const route = o.routes[next++ % o.routes.length]!;
      const s = stats.get(route)!;
      const started = performance.now();
      let status = "error";
      try {
        const res = await fetch(o.base + route, { redirect: "manual", signal: AbortSignal.timeout(o.timeoutMs), headers: { "user-agent": "nexus-load-test" } });
        await res.arrayBuffer();
        status = String(res.status);
        // 429 is the app's per-client rate limit doing its job (one IP here); reported, not an error.
        if (res.status === 429) s.rateLimited++;
        else if (res.status >= 400) s.errors++;
      } catch (e) {
        status = e instanceof Error && e.name === "TimeoutError" ? "timeout" : "network_error";
        s.errors++;
      }
      s.latencies.push(performance.now() - started);
      s.statuses[status] = (s.statuses[status] ?? 0) + 1;
    }
  }

  const t0 = performance.now();
  await Promise.all(Array.from({ length: o.concurrency }, worker));
  const elapsedS = (performance.now() - t0) / 1000;

  const all = [...stats.values()].flatMap((s) => s.latencies).sort((a, b) => a - b);
  const errors = [...stats.values()].reduce((n, s) => n + s.errors, 0);
  const round = (v: number) => Math.round(v * 10) / 10;
  const report = {
    base: o.base,
    durationS: round(elapsedS),
    concurrency: o.concurrency,
    requests: all.length,
    rps: round(all.length / elapsedS),
    errorRate: all.length ? errors / all.length : 1,
    rateLimited: [...stats.values()].reduce((n, s) => n + s.rateLimited, 0),
    latencyMs: { p50: round(percentile(all, 50)), p95: round(percentile(all, 95)), p99: round(percentile(all, 99)), max: round(all.at(-1) ?? Number.NaN) },
    routes: Object.fromEntries(
      [...stats.entries()].map(([r, s]) => {
        const sorted = [...s.latencies].sort((a, b) => a - b);
        return [r, { requests: sorted.length, errors: s.errors, rateLimited: s.rateLimited, statuses: s.statuses, p50: round(percentile(sorted, 50)), p95: round(percentile(sorted, 95)) }];
      }),
    ),
    thresholds: { p95Ms: o.p95Ms, maxErrorRate: o.maxErrorRate },
  };
  const failures: string[] = [];
  if (report.errorRate > o.maxErrorRate) failures.push(`error rate ${(report.errorRate * 100).toFixed(2)}% > ${(o.maxErrorRate * 100).toFixed(2)}%`);
  if (!(report.latencyMs.p95 <= o.p95Ms)) failures.push(`p95 ${report.latencyMs.p95} ms > ${o.p95Ms} ms`);
  console.log(JSON.stringify({ ...report, passed: failures.length === 0, failures }, null, 2));
  process.exit(failures.length ? 1 : 0);
}

if (process.argv[1]?.endsWith("load-test.ts")) {
  main().catch((e) => {
    console.error(e instanceof Error ? e.message : e);
    process.exit(2);
  });
}
