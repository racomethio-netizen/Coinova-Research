import { mkdtempSync, readFileSync, readdirSync, statSync } from "node:fs";
import { createServer, type Server } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createFieldCipher } from "@tamagne/core";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { ClamAvScanner, EicarOnlyScanner, parseClamReply } from "../src/scanning/scanner.js";
import { EncryptedObjectStore, LocalObjectStore } from "../src/storage/storage.js";

/**
 * Fake clamd implementing the INSTREAM framing exactly: "zINSTREAM\0", then
 * [uint32 BE length][bytes] chunks, ended by a zero length. Replies "stream: OK\0"
 * or "stream: <sig> FOUND\0". Lets us verify the protocol without ClamAV installed.
 */
function fakeClamd(): Promise<{ server: Server; port: number; received: Buffer[] }> {
  const received: Buffer[] = [];
  const server = createServer((sock) => {
    let buf = Buffer.alloc(0);
    let commandSeen = false;
    const body: Buffer[] = [];
    sock.on("data", (d) => {
      buf = Buffer.concat([buf, d]);
      if (!commandSeen) {
        const idx = buf.indexOf(0);
        if (idx < 0) return;
        if (buf.subarray(0, idx).toString() !== "zINSTREAM") return sock.end("UNKNOWN COMMAND\0");
        commandSeen = true;
        buf = buf.subarray(idx + 1);
      }
      while (buf.length >= 4) {
        const len = buf.readUInt32BE(0);
        if (len === 0) {
          const data = Buffer.concat(body);
          received.push(data);
          sock.end(data.includes("EICAR-STANDARD-ANTIVIRUS-TEST-FILE") ? "stream: Eicar-Test-Signature FOUND\0" : "stream: OK\0");
          return;
        }
        if (buf.length < 4 + len) return;
        body.push(buf.subarray(4, 4 + len));
        buf = buf.subarray(4 + len);
      }
    });
  });
  return new Promise((r) => server.listen(0, "127.0.0.1", () => r({ server, port: (server.address() as { port: number }).port, received })));
}

describe("ClamAV INSTREAM client", () => {
  let clam: Awaited<ReturnType<typeof fakeClamd>>;
  beforeAll(async () => {
    clam = await fakeClamd();
  });
  afterAll(() => clam.server.close());

  it("streams the whole file in chunks and reports clean files", async () => {
    const data = Buffer.alloc(200_000, 7); // spans several 64 KiB chunks
    const res = await new ClamAvScanner("127.0.0.1", clam.port).scan(data);
    expect(res).toEqual({ status: "CLEAN", engine: "clamav" });
    expect(clam.received.at(-1)!.equals(data)).toBe(true);
  });

  it("reports infections with the signature name", async () => {
    const res = await new ClamAvScanner("127.0.0.1", clam.port).scan(Buffer.from(`%PDF-1.4 ${EicarOnlyScanner.EICAR}`));
    expect(res).toEqual({ status: "INFECTED", engine: "clamav", signature: "Eicar-Test-Signature" });
  });

  it("fails closed when the daemon is unreachable or answers nonsense", async () => {
    expect((await new ClamAvScanner("127.0.0.1", 1, 2000).scan(Buffer.from("x"))).status).toBe("FAILED");
    expect(parseClamReply("stream: Can't allocate memory ERROR\0").status).toBe("FAILED");
    expect(parseClamReply("").status).toBe("FAILED");
  });
});

describe("encrypted local storage", () => {
  const dir = mkdtempSync(join(tmpdir(), "tamagne-store-"));
  const cipher = createFieldCipher({ keys: new Map([[1, Buffer.alloc(32, 5)]]), activeKeyId: 1 });
  const store = new EncryptedObjectStore(new LocalObjectStore(dir), cipher);

  it("stores ciphertext only, with owner-only permissions, and round-trips", async () => {
    const doc = Buffer.from("%PDF-1.7 Fixture Trading PLC business licence (synthetic)");
    await store.put("business-documents/b1/d1", doc);
    const onDisk = readFileSync(join(dir, "business-documents", "b1", "d1"));
    expect(onDisk.includes(Buffer.from("Fixture Trading"))).toBe(false);
    expect(statSync(join(dir, "business-documents", "b1", "d1")).mode & 0o777).toBe(0o600);
    expect((await store.get("business-documents/b1/d1")).equals(doc)).toBe(true);
  });

  it("ciphertext moved to another key does not decrypt", async () => {
    await store.put("business-documents/b1/d2", Buffer.from("a"));
    const raw = readFileSync(join(dir, "business-documents", "b1", "d2"));
    await store.inner.put("business-documents/b1/d3", raw);
    await expect(store.get("business-documents/b1/d3")).rejects.toThrow();
  });

  it("refuses path traversal and overwrites", async () => {
    for (const key of ["../escape", "a/../../b", "/abs", "A/upper", "a//b", "a/./b"]) {
      await expect(store.put(key, Buffer.from("x")), key).rejects.toThrow(/invalid storage key/);
    }
    await expect(store.put("business-documents/b1/d1", Buffer.from("x"))).rejects.toThrow();
    expect(readdirSync(dir)).toEqual(["business-documents"]);
  });
});

/**
 * Against a real clamd (opt-in): CLAMAV_TEST_HOST=127.0.0.1 [CLAMAV_TEST_PORT=3310].
 * e.g. docker run -d -p 127.0.0.1:3310:3310 clamav/clamav:1.4
 */
const clamHost = process.env.CLAMAV_TEST_HOST;
describe.skipIf(!clamHost)("real ClamAV daemon", () => {
  const scanner = new ClamAvScanner(clamHost ?? "", Number(process.env.CLAMAV_TEST_PORT ?? 3310));
  it("detects the EICAR test file and passes a clean PDF", async () => {
    const eicar = Buffer.from("X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*");
    expect(await scanner.scan(eicar)).toMatchObject({ status: "INFECTED", engine: "clamav", signature: expect.stringMatching(/eicar/i) });
    expect(await scanner.scan(Buffer.from("%PDF-1.7\n% clean synthetic fixture\n%%EOF\n"))).toMatchObject({ status: "CLEAN", engine: "clamav" });
  });
  it("scans a document at the 8 MB upload limit", async () => {
    expect((await scanner.scan(Buffer.alloc(8 * 1024 * 1024, 0x41))).status).toBe("CLEAN");
  }, 30_000);
});

describe("object replace (key rotation)", () => {
  it("put never overwrites; replace swaps atomically and requires an existing object", async () => {
    const store = new LocalObjectStore(mkdtempSync(join(tmpdir(), "tamagne-replace-")));
    await store.put("a/b", Buffer.from("one"));
    await expect(store.put("a/b", Buffer.from("two"))).rejects.toThrow();
    await store.replace("a/b", Buffer.from("two"));
    expect((await store.get("a/b")).toString()).toBe("two");
    await expect(store.replace("a/missing", Buffer.from("x"))).rejects.toThrow();
  });
});
