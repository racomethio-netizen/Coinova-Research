/**
 * Display-only indicators for charts. Same definition as the engine
 * (engine/src/nexus_engine/signals/indicators.py): EMA seeded with the SMA of
 * the first n values; undefined (null) during warm-up. Never used for decisions.
 */
export function ema(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = values.map(() => null);
  if (n < 1 || values.length < n) return out;
  const alpha = 2 / (n + 1);
  let prev = values.slice(0, n).reduce((a, b) => a + b, 0) / n;
  out[n - 1] = prev;
  for (let i = n; i < values.length; i++) {
    prev = alpha * values[i]! + (1 - alpha) * prev;
    out[i] = prev;
  }
  return out;
}
