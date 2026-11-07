import { connect } from "node:net";

export interface ScanResult {
  status: "CLEAN" | "INFECTED" | "FAILED";
  engine: string;
  /** Signature name when infected (safe to log; contains no user data). */
  signature?: string;
}

export interface MalwareScanner {
  readonly name: string;
  scan(data: Buffer): Promise<ScanResult>;
}

export const MALWARE_SCANNER = Symbol("MALWARE_SCANNER");

/** No scanner configured: everything is FAILED, so nothing can be approved. */
export class DisabledScanner implements MalwareScanner {
  readonly name = "disabled";
  async scan(): Promise<ScanResult> {
    return { status: "FAILED", engine: "disabled" };
  }
}

/**
 * Development/test only (config refuses it elsewhere): detects the standard EICAR test
 * string and nothing else. It is NOT malware protection.
 */
export class EicarOnlyScanner implements MalwareScanner {
  readonly name = "eicar-only";
  static readonly EICAR = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*";
  async scan(data: Buffer): Promise<ScanResult> {
    return data.includes(EicarOnlyScanner.EICAR)
      ? { status: "INFECTED", engine: "eicar-only", signature: "Eicar-Test-Signature" }
      : { status: "CLEAN", engine: "eicar-only" };
  }
}

/** ClamAV daemon over TCP using the INSTREAM protocol (zINSTREAM, length-prefixed chunks). */
export class ClamAvScanner implements MalwareScanner {
  readonly name = "clamav";
  constructor(
    private readonly host: string,
    private readonly port: number,
    private readonly timeoutMs = 30_000,
  ) {}

  scan(data: Buffer): Promise<ScanResult> {
    return new Promise((resolvePromise) => {
      const socket = connect({ host: this.host, port: this.port });
      const chunks: Buffer[] = [];
      let settled = false;
      const done = (r: ScanResult) => {
        if (settled) return;
        settled = true;
        socket.destroy();
        resolvePromise(r);
      };
      socket.setTimeout(this.timeoutMs, () => done({ status: "FAILED", engine: "clamav" }));
      socket.on("error", () => done({ status: "FAILED", engine: "clamav" }));
      socket.on("data", (c) => chunks.push(c));
      socket.on("end", () => done(parseClamReply(Buffer.concat(chunks).toString("utf8"))));
      socket.on("connect", () => {
        socket.write("zINSTREAM\0");
        const CHUNK = 64 * 1024;
        for (let off = 0; off < data.length; off += CHUNK) {
          const part = data.subarray(off, off + CHUNK);
          const len = Buffer.alloc(4);
          len.writeUInt32BE(part.length);
          socket.write(len);
          socket.write(part);
        }
        socket.write(Buffer.alloc(4)); // zero-length chunk ends the stream
      });
    });
  }
}

export function parseClamReply(reply: string): ScanResult {
  const text = reply.replace(/\0/g, "").trim();
  if (/^stream: OK$/.test(text)) return { status: "CLEAN", engine: "clamav" };
  const found = /^stream: (.+) FOUND$/.exec(text);
  if (found) return { status: "INFECTED", engine: "clamav", signature: found[1]!.slice(0, 120) };
  return { status: "FAILED", engine: "clamav" };
}
