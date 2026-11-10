import { describe, expect, it } from "vitest";
import { CHUNK, chunkedStorage, type KV } from "./chunked-storage";

function memory(): KV & { map: Map<string, string> } {
  const map = new Map<string, string>();
  return { map, getItem: async (k) => map.get(k) ?? null, setItem: async (k, v) => void map.set(k, v), removeItem: async (k) => void map.delete(k) };
}

describe("chunked secure storage", () => {
  it("round-trips values larger than the per-item limit", async () => {
    const kv = memory();
    const s = chunkedStorage(kv);
    const big = "x".repeat(CHUNK * 3 + 17);
    await s.setItem("sb-auth-token", big);
    expect(await s.getItem("sb-auth-token")).toBe(big);
    expect([...kv.map.values()].every((v) => v.length <= CHUNK)).toBe(true);
  });

  it("shrinking a value removes stale chunks, and removal clears everything", async () => {
    const kv = memory();
    const s = chunkedStorage(kv);
    await s.setItem("k", "a".repeat(CHUNK * 4));
    await s.setItem("k", "short");
    expect(await s.getItem("k")).toBe("short");
    expect(kv.map.size).toBe(2);
    await s.removeItem("k");
    expect(kv.map.size).toBe(0);
    expect(await s.getItem("k")).toBeNull();
  });

  it("treats a partially written value as absent (signed out), never as corrupt data", async () => {
    const kv = memory();
    const s = chunkedStorage(kv);
    await s.setItem("k", "b".repeat(CHUNK * 2));
    kv.map.delete("k.1");
    expect(await s.getItem("k")).toBeNull();
  });

  it("sanitizes keys for the secure store's allowed characters", async () => {
    const kv = memory();
    await chunkedStorage(kv).setItem("sb-x:auth/token", "v");
    expect([...kv.map.keys()].every((k) => /^[A-Za-z0-9._-]+$/.test(k))).toBe(true);
  });
});
