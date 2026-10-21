import "server-only";
import { createServerClient } from "@supabase/ssr";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { cookies } from "next/headers";
import { getEnv, isSupabaseConfigured } from "@/lib/env";

/**
 * Per-request Supabase client bound to the user's session cookies.
 * All queries run as the user and are subject to Row Level Security.
 * Returns null when Supabase is not configured (local mock mode).
 */
export async function createSupabaseServerClient(): Promise<SupabaseClient | null> {
  const env = getEnv();
  if (!isSupabaseConfigured(env)) return null;
  const store = await cookies();
  return createServerClient(env.SUPABASE_URL!, env.SUPABASE_ANON_KEY!, {
    cookies: {
      getAll: () => store.getAll(),
      setAll: (list) => {
        try {
          for (const { name, value, options } of list) store.set(name, value, options);
        } catch {
          // Called from a Server Component where cookies are read-only; the
          // proxy refreshes the session, so this is safe to ignore.
        }
      },
    },
  });
}

let serviceClient: SupabaseClient | null | undefined;

/**
 * SERVICE-ROLE client: bypasses RLS. Use only in trusted server code paths
 * (scheduled jobs, verified webhooks, admin actions after a server-side
 * admin check). Never pass its results to the client unfiltered.
 */
export function getServiceClient(): SupabaseClient | null {
  if (serviceClient !== undefined) return serviceClient;
  const env = getEnv();
  serviceClient =
    env.SUPABASE_URL && env.SUPABASE_SERVICE_ROLE_KEY
      ? createClient(env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY, {
          auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
        })
      : null;
  return serviceClient;
}

export function requireServiceClient(): SupabaseClient {
  const c = getServiceClient();
  if (!c) throw new Error("SUPABASE_SERVICE_ROLE_KEY is not configured.");
  return c;
}
