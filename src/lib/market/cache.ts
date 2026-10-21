/**
 * TTL cache with request coalescing and stale-if-error semantics.
 *
 * - Fresh hit (age < ttl): returned with stale=false.
 * - Miss or expired: one loader call per key (concurrent callers share it).
 * - Loader fails and a previous value exists younger than `maxStaleMs`: the
 *   old value is returned with stale=true so the UI can label it.
 * - Loader fails with no usable value: the error propagates. We never invent data.
 */

interface Entry<T> {
  value: T;
  fetchedAt: number;
}

export interface CachedValue<T> {
  value: T;
  fetchedAt: number;
  stale: boolean;
}

export class StaleWhileErrorCache {
  private readonly entries = new Map<string, Entry<unknown>>();
  private readonly inflight = new Map<string, Promise<Entry<unknown>>>();

  constructor(
    private readonly maxEntries = 2_000,
    private readonly now: () => number = () => Date.now(),
  ) {}

  async get<T>(key: string, ttlMs: number, maxStaleMs: number, loader: () => Promise<T>): Promise<CachedValue<T>> {
    const existing = this.entries.get(key) as Entry<T> | undefined;
    const t = this.now();
    if (existing && t - existing.fetchedAt < ttlMs) {
      return { value: existing.value, fetchedAt: existing.fetchedAt, stale: false };
    }

    let p = this.inflight.get(key) as Promise<Entry<T>> | undefined;
    if (!p) {
      p = loader().then((value) => {
        const entry = { value, fetchedAt: this.now() };
        this.set(key, entry);
        return entry;
      });
      this.inflight.set(key, p as Promise<Entry<unknown>>);
      p.finally(() => this.inflight.delete(key)).catch(() => undefined);
    }

    try {
      const entry = await p;
      return { value: entry.value, fetchedAt: entry.fetchedAt, stale: false };
    } catch (err) {
      if (existing && this.now() - existing.fetchedAt < maxStaleMs) {
        return { value: existing.value, fetchedAt: existing.fetchedAt, stale: true };
      }
      throw err;
    }
  }

  private set(key: string, entry: Entry<unknown>) {
    if (this.entries.size >= this.maxEntries && !this.entries.has(key)) {
      const oldest = this.entries.keys().next().value;
      if (oldest !== undefined) this.entries.delete(oldest);
    }
    this.entries.delete(key);
    this.entries.set(key, entry);
  }

  clear() {
    this.entries.clear();
    this.inflight.clear();
  }
}
