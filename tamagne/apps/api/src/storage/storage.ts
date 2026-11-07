import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";
import { HttpStatus } from "@nestjs/common";
import type { FieldCipher } from "@tamagne/core";
import { ApiError } from "../common/http.js";

/** Raw byte store. Production target: S3-compatible private storage hosted in Ethiopia (adapter pending provider choice). */
export interface ObjectStore {
  readonly name: string;
  readonly available: boolean;
  put(key: string, data: Buffer): Promise<void>;
  get(key: string): Promise<Buffer>;
  delete(key: string): Promise<void>;
}

const KEY_RE = /^[a-z0-9][a-z0-9/-]{0,200}$/;

function assertKey(key: string) {
  if (!KEY_RE.test(key) || key.includes("//") || key.split("/").some((p) => p === "" || p === ".." || p === ".")) {
    throw new Error("invalid storage key");
  }
}

export class DisabledObjectStore implements ObjectStore {
  readonly name = "disabled";
  readonly available = false;
  private fail(): never {
    throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE, "storage_unavailable", "Document upload is not available yet.");
  }
  async put(): Promise<void> {
    this.fail();
  }
  async get(): Promise<Buffer> {
    this.fail();
  }
  async delete(): Promise<void> {
    this.fail();
  }
}

/** Local filesystem (development/test only; config refuses it in production). Files are 0600. */
export class LocalObjectStore implements ObjectStore {
  readonly name = "local";
  readonly available = true;
  private readonly root: string;
  constructor(root: string) {
    this.root = resolve(root);
  }
  private path(key: string): string {
    assertKey(key);
    const p = resolve(this.root, ...key.split("/"));
    if (!p.startsWith(this.root + sep)) throw new Error("invalid storage key");
    return p;
  }
  async put(key: string, data: Buffer) {
    const p = this.path(key);
    await mkdir(dirname(p), { recursive: true, mode: 0o700 });
    await writeFile(p, data, { mode: 0o600, flag: "wx" }); // never overwrite
  }
  async get(key: string) {
    return readFile(this.path(key));
  }
  async delete(key: string) {
    await rm(this.path(key), { force: true });
  }
}

/**
 * Encrypts every object with AES-256-GCM before it reaches the store, bound to its key
 * (AAD), so storage operators and backups never hold readable documents.
 */
export class EncryptedObjectStore {
  constructor(readonly inner: ObjectStore, private readonly cipher: FieldCipher) {}
  get available() {
    return this.inner.available;
  }
  async put(key: string, data: Buffer) {
    const blob = this.cipher.encrypt(data.toString("base64"), `object:${key}`);
    await this.inner.put(key, blob);
  }
  async get(key: string): Promise<Buffer> {
    const blob = await this.inner.get(key);
    return Buffer.from(this.cipher.decrypt(blob, `object:${key}`), "base64");
  }
  delete(key: string) {
    return this.inner.delete(key);
  }
}

export const OBJECT_STORE = Symbol("OBJECT_STORE");
