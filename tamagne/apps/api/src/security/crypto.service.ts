import { Inject, Injectable } from "@nestjs/common";
import { createFieldCipher, hmacSha256Hex, parseKeyRing, type FieldCipher } from "@tamagne/core";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";

/** Process-wide field cipher and keyed hashing, built from validated config. */
@Injectable()
export class CryptoService {
  readonly cipher: FieldCipher;
  readonly pepper: Buffer;

  constructor(@Inject(APP_CONFIG) config: AppConfig) {
    this.cipher = createFieldCipher({
      keys: parseKeyRing(config.fieldEncryption.keys),
      activeKeyId: config.fieldEncryption.activeKeyId,
    });
    this.pepper = config.hmacPepper;
  }

  /** Keyed hash with a domain prefix, so hashes of the same value differ per purpose. */
  hash(domain: "ip" | "phone" | "subject" | "session", value: string): string {
    return hmacSha256Hex(this.pepper, `${domain}:${value}`);
  }
}
