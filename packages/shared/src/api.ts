import { z } from "zod";

/** Response envelope used by every API route (mirrors src/lib/http/api.ts). */
export const ApiErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
  details: z.unknown().optional(),
});

export const envelope = <T extends z.ZodType>(data: T) =>
  z.discriminatedUnion("ok", [z.object({ ok: z.literal(true), data }), z.object({ ok: z.literal(false), error: ApiErrorSchema })]);

export type ApiError = z.infer<typeof ApiErrorSchema>;
