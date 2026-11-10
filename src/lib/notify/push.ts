/**
 * Expo push delivery (https://docs.expo.dev/push-notifications/sending-notifications/).
 * Only titles and bodies we generated are sent. Tokens Expo reports as
 * `DeviceNotRegistered` are returned so the caller can revoke them.
 */
export const EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send";

export interface PushResult {
  ok: boolean;
  error?: string;
  permanent?: boolean;
  deadTokens: string[];
  sent: number;
}

type Ticket = { status: "ok"; id: string } | { status: "error"; message?: string; details?: { error?: string } };

export async function sendExpoPush(
  tokens: string[],
  title: string,
  body: string,
  opts: { accessToken?: string; fetchImpl?: typeof fetch; timeoutMs?: number } = {},
): Promise<PushResult> {
  if (tokens.length === 0) return { ok: false, error: "no registered devices", permanent: true, deadTokens: [], sent: 0 };
  const f = opts.fetchImpl ?? fetch;
  let res: Response;
  try {
    res = await f(EXPO_PUSH_URL, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json", ...(opts.accessToken ? { authorization: `Bearer ${opts.accessToken}` } : {}) },
      body: JSON.stringify(tokens.map((to) => ({ to, title: title.slice(0, 120), body: body.slice(0, 500), sound: "default" }))),
      signal: AbortSignal.timeout(opts.timeoutMs ?? 10_000),
    });
  } catch {
    return { ok: false, error: "push service unreachable", deadTokens: [], sent: 0 };
  }
  if (res.status === 429 || res.status >= 500) return { ok: false, error: `push service HTTP ${res.status}`, deadTokens: [], sent: 0 };
  if (!res.ok) return { ok: false, error: `push service HTTP ${res.status}`, permanent: true, deadTokens: [], sent: 0 };
  const json = (await res.json().catch(() => null)) as { data?: Ticket[] } | null;
  const tickets = Array.isArray(json?.data) ? json.data : [];
  const dead = tickets.flatMap((t, i) => (t.status === "error" && t.details?.error === "DeviceNotRegistered" ? [tokens[i]!] : []));
  const sent = tickets.filter((t) => t.status === "ok").length;
  return sent > 0 ? { ok: true, deadTokens: dead, sent } : { ok: false, error: "no device accepted the notification", permanent: dead.length === tokens.length, deadTokens: dead, sent };
}
