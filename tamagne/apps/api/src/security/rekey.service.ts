import { Inject, Injectable, Logger } from "@nestjs/common";
import { AuditService } from "../audit/audit.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { OBJECT_STORE, type EncryptedObjectStore } from "../storage/storage.js";
import { CryptoService } from "./crypto.service.js";

const BATCH = 500;

interface Row {
  id: string;
  blob: Uint8Array;
}

/**
 * Key rotation: re-encrypts every field and stored object that was written with a key
 * other than FIELD_ENCRYPTION_ACTIVE_KEY_ID. Run after adding a new key to the ring and
 * making it active; remove the old key only once a run reports `remaining: 0`.
 * Idempotent; each update is conditional on the ciphertext being unchanged, so a value
 * rewritten concurrently by the app is skipped (it is already on the active key).
 */
@Injectable()
export class RekeyService {
  private readonly logger = new Logger("rekey");

  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
    @Inject(OBJECT_STORE) private readonly store: EncryptedObjectStore,
    @Inject(AuditService) private readonly audit: AuditService,
  ) {}

  async runRekeyJob() {
    const db = this.prisma.client;
    const fields = {
      mfaFactors: await this.rekeyColumn(
        "mfa_factors.secretEnc",
        (cursor) => db.mfaFactor.findMany({ where: cursor ? { id: { gt: cursor } } : {}, select: { id: true, secretEnc: true }, orderBy: { id: "asc" }, take: BATCH }).then((r) => r.map((x) => ({ id: x.id, blob: x.secretEnc }))),
        (id, old, next) => db.mfaFactor.updateMany({ where: { id, secretEnc: { equals: old } }, data: { secretEnc: next } }),
      ),
      oidcVerifiers: await this.rekeyColumn(
        "oidc_auth_requests.codeVerifierEnc",
        (cursor) => db.oidcAuthRequest.findMany({ where: cursor ? { id: { gt: cursor } } : {}, select: { id: true, codeVerifierEnc: true }, orderBy: { id: "asc" }, take: BATCH }).then((r) => r.map((x) => ({ id: x.id, blob: x.codeVerifierEnc }))),
        (id, old, next) => db.oidcAuthRequest.updateMany({ where: { id, codeVerifierEnc: { equals: old } }, data: { codeVerifierEnc: next } }),
      ),
      identityResults: await this.rekeyColumn(
        "identity_verifications.verifiedNameEnc",
        (cursor) =>
          db.identityVerification
            .findMany({ where: { verifiedNameEnc: { not: null }, ...(cursor ? { id: { gt: cursor } } : {}) }, select: { id: true, verifiedNameEnc: true }, orderBy: { id: "asc" }, take: BATCH })
            .then((r) => r.map((x) => ({ id: x.id, blob: x.verifiedNameEnc! }))),
        (id, old, next) => db.identityVerification.updateMany({ where: { id, verifiedNameEnc: { equals: old } }, data: { verifiedNameEnc: next } }),
      ),
      reporterContacts: await this.rekeyColumn(
        "fraud_reports.reporterContactEnc",
        (cursor) =>
          db.fraudReport
            .findMany({ where: { reporterContactEnc: { not: null }, ...(cursor ? { id: { gt: cursor } } : {}) }, select: { id: true, reporterContactEnc: true }, orderBy: { id: "asc" }, take: BATCH })
            .then((r) => r.map((x) => ({ id: x.id, blob: x.reporterContactEnc! }))),
        (id, old, next) => db.fraudReport.updateMany({ where: { id, reporterContactEnc: { equals: old } }, data: { reporterContactEnc: next } }),
      ),
    };

    const objects = { rotated: 0, current: 0, failed: 0 };
    if (this.store.available) {
      const keys = [
        ...(await db.businessDocument.findMany({ where: { purgedAt: null }, select: { storageKey: true } })),
        ...(await db.identityReviewRequest.findMany({ where: { purgedAt: null }, select: { storageKey: true } })),
      ].map((r) => r.storageKey);
      for (const key of keys) {
        try {
          if (!(await this.store.reencrypt(key))) {
            objects.current++;
            continue;
          }
          objects.rotated++;
        } catch (e) {
          objects.failed++;
          this.logger.error(`object re-encryption failed: ${(e as Error).name}`);
        }
      }
    }

    // Audit metadata keys may not contain words like "name" or "secret", hence the neutral field names.
    const remaining = Object.values(fields).reduce((n, f) => n + f.failed, 0) + objects.failed;
    const flat = <K extends string>(k: K, v: { rotated: number; current: number; failed: number }) =>
      ({ [`${k}Rotated`]: v.rotated, [`${k}Current`]: v.current, [`${k}Failed`]: v.failed }) as Record<`${K}Rotated` | `${K}Current` | `${K}Failed`, number>;
    const result = {
      ...flat("mfaFactors", fields.mfaFactors),
      ...flat("oidcVerifiers", fields.oidcVerifiers),
      ...flat("identityResults", fields.identityResults),
      ...flat("reporterContacts", fields.reporterContacts),
      objectsRotated: objects.rotated,
      objectsCurrent: objects.current,
      objectsFailed: objects.failed,
      remaining,
    };
    await this.audit.recordSafe({ action: "job.rekey", actorType: "SYSTEM", metadata: result });
    return result;
  }

  private async rekeyColumn(
    column: string,
    page: (cursor: string | undefined) => Promise<Row[]>,
    update: (id: string, old: Uint8Array<ArrayBuffer>, next: Uint8Array<ArrayBuffer>) => Promise<{ count: number }>,
  ) {
    const stats = { rotated: 0, current: 0, failed: 0 };
    let cursor: string | undefined;
    for (;;) {
      const rows = await page(cursor);
      if (!rows.length) break;
      for (const { id, blob } of rows) {
        const buf = Buffer.from(blob);
        if (!this.crypto.cipher.needsRotation(buf)) {
          stats.current++;
          continue;
        }
        try {
          const aad = `${column}:${id}`;
          const next = this.crypto.cipher.encrypt(this.crypto.cipher.decrypt(buf, aad), aad);
          const { count } = await update(id, new Uint8Array(buf), new Uint8Array(next));
          // count 0: the row changed meanwhile (rewritten with the active key, or deleted).
          if (count === 1) stats.rotated++;
        } catch (e) {
          stats.failed++;
          this.logger.error(`re-encryption failed in ${column} for ${id}: ${(e as Error).name}`);
        }
      }
      cursor = rows.at(-1)!.id;
    }
    return stats;
  }
}
