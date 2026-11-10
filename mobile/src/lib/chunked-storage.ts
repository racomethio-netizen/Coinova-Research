/**
 * Session storage on top of a small key-value store (expo-secure-store limits
 * values to ~2 KB, and Supabase sessions are larger). Values are split into
 * chunks under `${key}.N` with a `${key}.count` index. Pure: the backing store
 * is injected, so this is unit-tested without a device.
 */
export interface KV {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  removeItem(key: string): Promise<void>;
}

export const CHUNK = 1800;
const safeKey = (k: string) => k.replace(/[^A-Za-z0-9._-]/g, "_");

export function chunkedStorage(kv: KV): KV {
  return {
    async getItem(key) {
      const k = safeKey(key);
      const count = Number(await kv.getItem(`${k}.count`));
      if (!Number.isInteger(count) || count <= 0) return null;
      const parts: string[] = [];
      for (let i = 0; i < count; i++) {
        const part = await kv.getItem(`${k}.${i}`);
        if (part === null) return null; // incomplete write: treat as signed out
        parts.push(part);
      }
      return parts.join("");
    },
    async setItem(key, value) {
      const k = safeKey(key);
      const old = Number(await kv.getItem(`${k}.count`)) || 0;
      const n = Math.max(1, Math.ceil(value.length / CHUNK));
      for (let i = 0; i < n; i++) await kv.setItem(`${k}.${i}`, value.slice(i * CHUNK, (i + 1) * CHUNK));
      await kv.setItem(`${k}.count`, String(n));
      for (let i = n; i < old; i++) await kv.removeItem(`${k}.${i}`);
    },
    async removeItem(key) {
      const k = safeKey(key);
      const count = Number(await kv.getItem(`${k}.count`)) || 0;
      await kv.removeItem(`${k}.count`);
      for (let i = 0; i < count; i++) await kv.removeItem(`${k}.${i}`);
    },
  };
}
