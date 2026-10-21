/**
 * Controlled administrative role assignment.
 *
 *   SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... npm run admin:grant -- user@example.com [admin|editor] [--revoke]
 *
 * This is the ONLY supported way to grant roles: it requires the service-role
 * key (held by operators, never the app's users) and writes an audit entry.
 * There is no API or UI that lets a user change roles.
 */
import { createClient } from "@supabase/supabase-js";

async function main() {
  const [email, roleArg = "admin", flag] = process.argv.slice(2);
  const role = roleArg === "editor" ? "editor" : roleArg === "admin" ? "admin" : null;
  const revoke = flag === "--revoke";
  if (!email || !role) {
    console.error("usage: npm run admin:grant -- <email> [admin|editor] [--revoke]");
    process.exit(2);
  }
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    console.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required");
    process.exit(2);
  }
  const db = createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });

  let userId: string | undefined;
  for (let page = 1; page <= 100 && !userId; page++) {
    const { data, error } = await db.auth.admin.listUsers({ page, perPage: 200 });
    if (error) throw error;
    userId = data.users.find((u) => u.email?.toLowerCase() === email.toLowerCase())?.id;
    if (data.users.length < 200) break;
  }
  if (!userId) {
    console.error(`No user found with email ${email}`);
    process.exit(1);
  }

  if (revoke) {
    const { error } = await db.from("user_roles").delete().eq("user_id", userId).eq("role", role);
    if (error) throw error;
  } else {
    const { error } = await db.from("user_roles").upsert({ user_id: userId, role, note: "granted via scripts/grant-admin.ts" }, { onConflict: "user_id,role" });
    if (error) throw error;
  }
  await db.from("admin_audit_logs").insert({ actor_id: null, action: revoke ? "role.revoke" : "role.grant", target_type: "user", target_id: userId, details: { role, via: "cli" } });
  console.log(`${revoke ? "Revoked" : "Granted"} role "${role}" ${revoke ? "from" : "to"} ${email} (${userId}).`);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
