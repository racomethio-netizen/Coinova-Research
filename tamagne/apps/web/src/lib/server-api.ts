import "server-only";
import { cookies } from "next/headers";

const API = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:4000";

export interface Viewer {
  userId: string;
  locale: "en" | "am";
  phone: string | null;
  tamagneId: string | null;
  roles: string[];
}

export type PublicStatus = "VERIFIED" | "NOT_VERIFIED" | "EXPIRED" | "REVOKED" | "SUSPENDED";
export interface Category {
  status: PublicStatus;
  verifiedAt: string | null;
  expiresAt: string | null;
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
