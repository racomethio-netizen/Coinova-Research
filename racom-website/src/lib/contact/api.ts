/** Response envelope of POST /api/contact, shared by the route handler and the form. */
export type ContactApiResponse =
  | { ok: true; delivery: "email" | "local"; reference: string }
  | { ok: false; error: { code: string; message: string; fields?: Record<string, string> } };
