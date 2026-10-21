import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { Client } from "pg";
import { adminUrl, TEST_DB_NAME } from "../db/global-setup";
import { ANON_KEY, PROXY_PORT, SERVICE_KEY, signJwt } from "./jwt";

export const SUPABASE_URL = `http://127.0.0.1:${PROXY_PORT}`;

export function serviceClient(): SupabaseClient {
  return createClient(SUPABASE_URL, SERVICE_KEY, { auth: { persistSession: false, autoRefreshToken: false } });
}

/** A supabase-js client authenticated as `userId` (subject to RLS). */
export function userClient(userId: string): SupabaseClient {
  return createClient(SUPABASE_URL, ANON_KEY, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: { headers: { Authorization: `Bearer ${signJwt({ sub: userId, role: "authenticated" })}` } },
  });
}

export async function pg(): Promise<Client> {
  const c = new Client({ connectionString: adminUrl(TEST_DB_NAME) });
  await c.connect();
  return c;
}

export async function newUser(db: Client, email = `u-${Math.random().toString(36).slice(2)}@test.local`): Promise<string> {
  return (await db.query("insert into auth.users (email) values ($1) returning id", [email])).rows[0].id;
}
