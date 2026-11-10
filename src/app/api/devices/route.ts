import { DeviceRegistrationSchema } from "@nexus/shared";
import { z } from "zod";
import { mapDbError } from "@/lib/db/errors";
import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";

export const dynamic = "force-dynamic";

/** Register this device for push notifications (mobile app, bearer auth). Idempotent per token. */
export const POST = authed("POST /api/devices", { perMinute: 10 }, async ({ req, viewer }) => {
  const body = await parseJsonBody(req, DeviceRegistrationSchema);
  // Re-registering refreshes the caller's own row. A token still registered to another account (RLS hides
  // it, so the delete affects nothing) is refused as a conflict; the app unregisters on sign-out.
  await viewer.supabase.from("device_push_tokens").delete().eq("token", body.token);
  const { error } = await viewer.supabase.from("device_push_tokens").insert({ token: body.token, platform: body.platform });
  if (error) throw mapDbError(error, "register device");
  return ok({ registered: true }, { status: 201 });
});

/** Unregister (sign-out or notifications turned off). */
export const DELETE = authed("DELETE /api/devices", { perMinute: 10 }, async ({ req, viewer }) => {
  const body = await parseJsonBody(req, z.object({ token: DeviceRegistrationSchema.shape.token }).strict());
  const { error } = await viewer.supabase.from("device_push_tokens").delete().eq("token", body.token);
  if (error) throw mapDbError(error, "unregister device");
  return ok({ removed: true });
});
