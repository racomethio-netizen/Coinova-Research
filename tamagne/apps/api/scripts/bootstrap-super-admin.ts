/**
 * Grants SUPER_ADMIN to an existing, phone-verified account — ONLY when no live
 * SUPER_ADMIN exists. Every later role change goes through the admin API (Phase 6),
 * so it is attributable to a named administrator.
 *
 *   DATABASE_URL=... HMAC_PEPPER=... npm run staff:bootstrap -w @tamagne/api -- --phone 0911223344
 *
 * The person must then sign in and enrol TOTP before any admin access works.
 */
import { normalizePhone } from "@tamagne/core";
import { createPrismaClient } from "@tamagne/db";

const idx = process.argv.indexOf("--phone");
const raw = idx > 0 ? process.argv[idx + 1] : undefined;
const phone = raw ? normalizePhone(raw, { allowTestRange: process.env.APP_ENV !== "production" }) : null;
if (!phone) {
  console.error("usage: bootstrap-super-admin --phone <Ethiopian mobile number>");
  process.exit(2);
}

const db = createPrismaClient();
try {
  await db.$transaction(async (tx) => {
    await tx.$executeRaw`select pg_advisory_xact_lock(hashtext('tamagne.bootstrap_super_admin'))`;
    const existing = await tx.staffRoleAssignment.count({ where: { role: "SUPER_ADMIN", revokedAt: null } });
    if (existing > 0) throw new Error("A SUPER_ADMIN already exists. Use the admin console to grant roles.");
    const user = await tx.user.findUnique({ where: { phoneE164: phone } });
    if (!user || user.status !== "ACTIVE" || !user.phoneVerifiedAt) {
      throw new Error("No active, phone-verified account for that number. Register through the app first.");
    }
    const grant = await tx.staffRoleAssignment.create({ data: { userId: user.id, role: "SUPER_ADMIN" } });
    await tx.auditLog.create({
      data: { actorType: "SYSTEM", action: "staff.role_granted", targetType: "user", targetId: user.id, metadata: { role: "SUPER_ADMIN", via: "bootstrap", grantId: grant.id } },
    });
  });
  console.log("SUPER_ADMIN granted. Sign in and set up two-factor authentication.");
} catch (e) {
  console.error((e as Error).message);
  process.exitCode = 1;
} finally {
  await db.$disconnect();
}
