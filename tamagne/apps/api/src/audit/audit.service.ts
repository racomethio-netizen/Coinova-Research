import { Inject, Injectable, Logger } from "@nestjs/common";
import type { Prisma } from "@tamagne/db";
import { PrismaService } from "../database/prisma.service.js";

type Tx = Prisma.TransactionClient;

export interface AuditEntry {
  action: string;
  actorType: "USER" | "STAFF" | "SYSTEM" | "API_CLIENT";
  actorUserId?: string | null;
  targetType?: string;
  targetId?: string;
  requestId?: string;
  ipHash?: string;
  /** IDs, codes and field names only. Never personal data, tokens or codes. */
  metadata?: Record<string, string | number | boolean | null | string[]>;
}

const FORBIDDEN_KEYS = /phone|email|code|token|secret|password|name|otp|jwt|claim/i;

/** Writes to the append-only, hash-chained audit log. */
@Injectable()
export class AuditService {
  private readonly logger = new Logger("audit");
  constructor(@Inject(PrismaService) private readonly prisma: PrismaService) {}

  async record(entry: AuditEntry, tx?: Tx): Promise<void> {
    const metadata = entry.metadata ?? {};
    for (const k of Object.keys(metadata)) {
      // Defence in depth: refuse metadata keys that suggest personal data or secrets.
      if (FORBIDDEN_KEYS.test(k) && !k.endsWith("Fields")) throw new Error(`audit metadata key not allowed: ${k}`);
    }
    await (tx ?? this.prisma.client).auditLog.create({
      data: {
        action: entry.action,
        actorType: entry.actorType,
        actorUserId: entry.actorUserId ?? null,
        targetType: entry.targetType ?? null,
        targetId: entry.targetId ?? null,
        requestId: entry.requestId ?? null,
        ipHash: entry.ipHash ?? null,
        metadata,
      },
    });
  }

  /** For security events where a failure to audit must not break the response. */
  async recordSafe(entry: AuditEntry): Promise<void> {
    try {
      await this.record(entry);
    } catch (e) {
      this.logger.error(`audit write failed for ${entry.action}: ${(e as Error).name}`);
    }
  }
}
