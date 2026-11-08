import { randomBytes, randomInt } from "node:crypto";
import { generateQrToken, generateTamagneId } from "@tamagne/core";
import { createPrismaClient, type PrismaClient } from "../src/index.js";

/**
 * Synthetic fixtures only. Phone numbers use the unassigned E.164 country code
 * +999 so they can never belong to a real person. Names are obviously fake.
 */
export function testDatabaseUrl(): string {
  const url = process.env.TEST_DATABASE_URL ?? "postgresql://postgres@127.0.0.1:54339/tamagne_test";
  if (!/@(127\.0\.0\.1|localhost|postgres)(:\d+)?\/tamagne_test$/.test(url)) {
    throw new Error("Refusing to run database tests against a non-test database");
  }
  return url;
}

export function makeClient(): PrismaClient {
  return createPrismaClient({ connectionString: testDatabaseUrl(), max: 12 });
}

export const fakePhone = () => `+999${String(randomInt(0, 1e9)).padStart(9, "0")}`;
export const hex64 = () => randomBytes(32).toString("hex");

export async function createUser(db: PrismaClient, data: { status?: "ACTIVE" } = {}) {
  return db.user.create({ data: { phoneE164: fakePhone(), status: data.status ?? "ACTIVE" } });
}

export async function createSeller(db: PrismaClient, opts: { accountStatus?: "ACTIVE" | "UNDER_REVIEW" } = {}) {
  const user = await createUser(db);
  const seller = await db.sellerProfile.create({
    data: {
      userId: user.id,
      tamagneId: generateTamagneId(),
      displayName: "Test Seller (Fixture)",
      sellerType: "TELEGRAM_SHOP",
      accountStatus: opts.accountStatus ?? "ACTIVE",
    },
  });
  return { user, seller };
}

export async function createStaff(db: PrismaClient, role: "VERIFICATION_OFFICER" | "SUPER_ADMIN" = "VERIFICATION_OFFICER") {
  const user = await createUser(db);
  await db.staffRoleAssignment.create({ data: { userId: user.id, role } });
  return user;
}

export async function grantConsent(db: PrismaClient, userId: string, purpose: "FAYDA_IDENTITY_VERIFICATION" | "PUBLIC_PROFILE_DISPLAY" = "FAYDA_IDENTITY_VERIFICATION") {
  return db.consentRecord.create({ data: { userId, purpose, policyVersion: "2026-11-01", locale: "EN" } });
}

export async function createAuthRequest(db: PrismaClient, userId: string, sellerProfileId: string, environment: "SANDBOX" | "PRODUCTION" = "SANDBOX") {
  const consent =
    (await db.consentRecord.findFirst({ where: { userId, purpose: "FAYDA_IDENTITY_VERIFICATION", status: "GRANTED" } })) ??
    (await grantConsent(db, userId));
  return db.oidcAuthRequest.create({
    data: {
      userId,
      sellerProfileId,
      environment,
      stateHash: hex64(),
      nonceHash: hex64(),
      codeVerifierEnc: Buffer.from("ciphertext-placeholder"),
      redirectUri: "http://localhost:3000/api/v1/fayda/callback",
      consentRecordId: consent.id,
      expiresAt: new Date(Date.now() + 10 * 60_000),
    },
  });
}

/** A VERIFIED identity result as the (future) adapter would record it after full validation. */
export async function createVerifiedIdentity(
  db: PrismaClient,
  userId: string,
  sellerProfileId: string,
  opts: { environment?: "SANDBOX" | "PRODUCTION"; subjectHash?: string } = {},
) {
  const environment = opts.environment ?? "SANDBOX";
  const req = await createAuthRequest(db, userId, sellerProfileId, environment);
  return db.identityVerification.create({
    data: {
      sellerProfileId,
      authRequestId: req.id,
      environment,
      status: "VERIFIED",
      subjectHash: opts.subjectHash ?? hex64(),
      issuer: "https://esignet.example.test",
      audience: "tamagne-test-client",
      idTokenJti: randomBytes(12).toString("hex"),
      signatureVerified: true,
      verifiedAt: new Date(),
      expiresAt: new Date(Date.now() + 365 * 86_400_000),
    },
  });
}

/** Badge validity that ends before the fixture identity verification expires. */
export const oneYear = () => ({ issuedAt: new Date(), expiresAt: new Date(Date.now() + 300 * 86_400_000) });

export { generateQrToken };

/** Asserts a Prisma/driver error message contains `fragment` (DB errors surface as messages). */
export async function rejects(p: Promise<unknown>, fragment: string | RegExp): Promise<void> {
  let error: unknown;
  try {
    await p;
  } catch (e) {
    error = e;
  }
  if (!error) throw new Error(`expected rejection matching ${String(fragment)}`);
  const msg = error instanceof Error ? error.message : String(error);
  const ok = typeof fragment === "string" ? msg.includes(fragment) : fragment.test(msg);
  if (!ok) throw new Error(`rejection did not match ${String(fragment)}: ${msg}`);
}
