import "server-only";
import { cookies, headers } from "next/headers";

const API = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:4000";

export interface Viewer {
  userId: string;
  locale: "en" | "am";
  phone: string | null;
  tamagneId: string | null;
  roles: string[];
  mfa: { enrolled: boolean; verified: boolean } | null;
}

export type PublicStatus = "VERIFIED" | "NOT_VERIFIED" | "EXPIRED" | "REVOKED" | "SUSPENDED";
export interface Category {
  status: PublicStatus;
  verifiedAt: string | null;
  expiresAt: string | null;
  /** A sandbox Fayda result on a non-production deployment. */
  testMode?: true;
}
export interface SellerMe {
  tamagneId: string;
  displayName: string;
  displayNameAm: string | null;
  sellerType: string;
  accountStatus: "ACTIVE" | "UNDER_REVIEW" | "SUSPENDED" | "DEACTIVATED";
  isPublic: boolean;
  region: string | null;
  city: string | null;
  verification: {
    phone: Category;
    faydaIdentity: Category;
    identityManualReview: Category;
    business: Category;
    enhancedReview: Category;
  };
}

/** Server-side GET to the API, forwarding only the session cookie. Never cached. */
async function apiGet<T>(path: string): Promise<{ status: number; data: T | null }> {
  const jar = await cookies();
  const session = jar.get("__Host-tamagne_session") ?? jar.get("tamagne_session");
  const res = await fetch(`${API}/api/v1${path}`, {
    headers: session ? { cookie: `${session.name}=${session.value}` } : {},
    cache: "no-store",
  });
  if (!res.ok) return { status: res.status, data: null };
  const body = (await res.json()) as { data: T };
  return { status: res.status, data: body.data };
}

export async function getViewer(): Promise<Viewer | null> {
  return (await apiGet<Viewer | null>("/auth/session")).data;
}

export async function getMySeller(): Promise<SellerMe | null> {
  return (await apiGet<SellerMe>("/sellers/me")).data;
}

export interface PublicSeller {
  tamagneId: string;
  displayName: string;
  displayNameAm: string | null;
  accountStatus: SellerMe["accountStatus"];
  verification: SellerMe["verification"];
  matchedBy: "tamagne_id" | "qr_token";
  checkedAt: string;
}

export type LookupOutcome =
  | { kind: "found"; seller: PublicSeller }
  | { kind: "not_found" }
  | { kind: "qr_revoked" }
  | { kind: "rate_limited" }
  | { kind: "unavailable" };

/**
 * Public lookup, rendered server-side. Forwards the visitor's X-Forwarded-For (set by
 * our load balancer) so the API's per-IP rate limit applies to the buyer, not to this server.
 */
export async function lookupSeller(id: string): Promise<LookupOutcome> {
  const h = await headers();
  const xff = h.get("x-forwarded-for");
  let res: Response;
  try {
    res = await fetch(`${API}/api/v1/verify/${encodeURIComponent(id)}`, {
      headers: xff ? { "x-forwarded-for": xff } : {},
      cache: "no-store",
    });
  } catch {
    return { kind: "unavailable" };
  }
  if (res.ok) return { kind: "found", seller: ((await res.json()) as { data: PublicSeller }).data };
  if (res.status === 404) return { kind: "not_found" };
  if (res.status === 410) return { kind: "qr_revoked" };
  if (res.status === 429) return { kind: "rate_limited" };
  return { kind: "unavailable" };
}

export interface MyQr {
  url: string;
  svg: string;
  createdAt: string;
}

export async function getMyQr(): Promise<MyQr | null> {
  return (await apiGet<MyQr>("/sellers/me/qr")).data;
}

export interface MyBusiness {
  id: string;
  legalName: string;
  tradeName: string | null;
  legalForm: "SOLE_PROPRIETORSHIP" | "PRIVATE_LIMITED_COMPANY" | "SHARE_COMPANY" | "PARTNERSHIP" | "COOPERATIVE" | "OTHER";
  registrationNumber: string | null;
  tinNumber: string | null;
  region: string | null;
  city: string | null;
  status: "DRAFT" | "SUBMITTED" | "VERIFIED" | "REJECTED" | "SUSPENDED";
  decisionReason: string | null;
  requiredDocumentTypes: string[];
  documents: {
    id: string;
    type: "BUSINESS_LICENSE" | "COMMERCIAL_REGISTRATION" | "TIN_CERTIFICATE" | "VAT_CERTIFICATE" | "MEMORANDUM_OF_ASSOCIATION" | "OTHER";
    mimeType: string;
    sizeBytes: number;
    scanStatus: "PENDING" | "CLEAN" | "INFECTED" | "FAILED";
    reviewStatus: "PENDING" | "APPROVED" | "REJECTED" | "NEEDS_RESUBMISSION";
    rejectionReason: string | null;
    uploadedAt: string;
  }[];
}

export async function getMyBusiness(): Promise<MyBusiness | null> {
  return (await apiGet<MyBusiness | null>("/businesses/me")).data;
}

export async function hasConsent(purpose: string): Promise<boolean> {
  const list = (await apiGet<{ purpose: string; status: string }[]>("/consents")).data ?? [];
  return list.some((c) => c.purpose === purpose && c.status === "GRANTED");
}

export interface FaydaStatus {
  enabled: boolean;
  mode: "disabled" | "sandbox" | "production";
}

export async function getFaydaStatus(): Promise<FaydaStatus> {
  return (await apiGet<FaydaStatus>("/fayda/status")).data ?? { enabled: false, mode: "disabled" };
}

export interface ManualReview {
  id: string;
  documentType: "FAYDA_ID_CARD" | "KEBELE_ID" | "PASSPORT" | "DRIVING_LICENSE";
  status: "SUBMITTED" | "APPROVED" | "REJECTED" | "WITHDRAWN";
  decisionReason: string | null;
}

export async function getManualReview(): Promise<ManualReview | null> {
  return (await apiGet<ManualReview | null>("/identity/manual")).data;
}

export type AdminResult<T> = { ok: true; data: T } | { ok: false; status: number; code: string; message: string };

/** Server-side GET for admin pages; returns the API's error code instead of throwing. */
export async function adminGet<T>(path: string): Promise<AdminResult<T>> {
  const jar = await cookies();
  const session = jar.get("__Host-tamagne_session") ?? jar.get("tamagne_session");
  const res = await fetch(`${API}/api/v1${path}`, { headers: session ? { cookie: `${session.name}=${session.value}` } : {}, cache: "no-store" });
  const body = (await res.json().catch(() => ({}))) as { data?: T; error?: { code: string; message: string } };
  if (!res.ok) return { ok: false, status: res.status, code: body.error?.code ?? "error", message: body.error?.message ?? "Request failed" };
  return { ok: true, data: body.data as T };
}
