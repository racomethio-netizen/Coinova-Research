import { z } from "zod";
import { services } from "@/content/services";

// The CSP forbids eval; jitless mode stops Zod probing `new Function` (which logs a CSP violation).
z.config({ jitless: true });

/** Shared inquiry schema: used by the form (client) and the route handler (server). */

export const serviceOptions = [
  ...services.map((s) => ({ value: s.slug, label: s.title })),
  { value: "other", label: "Something else / not sure yet" },
] as const;

export const budgetOptions = [
  { value: "under-5k", label: "Under $5,000" },
  { value: "5k-15k", label: "$5,000 – $15,000" },
  { value: "15k-50k", label: "$15,000 – $50,000" },
  { value: "50k-plus", label: "More than $50,000" },
  { value: "not-sure", label: "Not sure yet" },
] as const;

export const timelineOptions = [
  { value: "asap", label: "As soon as possible" },
  { value: "1-3-months", label: "Within 1–3 months" },
  { value: "3-6-months", label: "Within 3–6 months" },
  { value: "flexible", label: "Flexible / exploring" },
] as const;

const values = <T extends readonly { value: string }[]>(opts: T) =>
  opts.map((o) => o.value) as [T[number]["value"], ...T[number]["value"][]];

// Single-line text: no control characters (prevents header/log injection).
const singleLine = /^[^\u0000-\u001f\u007f]*$/;

const optionalText = (max: number, label: string) =>
  z
    .string()
    .trim()
    .max(max, `${label} must be ${max} characters or fewer.`)
    .regex(singleLine, `${label} must be a single line.`)
    .optional()
    .transform((v) => (v ? v : undefined));

const optionalChoice = <T extends [string, ...string[]]>(choices: T) =>
  z
    .union([z.enum(choices), z.literal("")])
    .optional()
    .transform((v) => (v ? v : undefined));

export const inquirySchema = z.object({
  name: z
    .string()
    .trim()
    .min(2, "Please enter your name.")
    .max(100, "Name must be 100 characters or fewer.")
    .regex(singleLine, "Name must be a single line."),
  email: z
    .string()
    .trim()
    .min(1, "Please enter your work email.")
    .max(254, "Email address is too long.")
    .pipe(z.email("Please enter a valid email address.")),
  company: optionalText(120, "Company"),
  location: optionalText(120, "Location"),
  service: z.enum(values(serviceOptions), { error: "Please choose the service you need." }),
  message: z
    .string()
    .trim()
    .min(30, "Please describe your project in at least 30 characters.")
    .max(5000, "Please keep the description under 5,000 characters."),
  budget: optionalChoice(values(budgetOptions)),
  timeline: optionalChoice(values(timelineOptions)),
  consent: z.literal(true, { error: "Please confirm so we can use these details to reply." }),
});

/** Anti-spam fields sent alongside the inquiry; never stored or emailed. */
export const antiSpamSchema = z.object({
  /** Honeypot: hidden from people, must stay empty. */
  website: z.string().max(200).optional(),
  /** Epoch ms when the form was rendered. */
  startedAt: z.number().int().nonnegative(),
});

export const inquiryRequestSchema = inquirySchema.and(antiSpamSchema);

export type InquiryInput = z.input<typeof inquirySchema>;
export type Inquiry = z.output<typeof inquirySchema>;

export function optionLabel(options: readonly { value: string; label: string }[], value: string | undefined) {
  if (!value) return undefined;
  return options.find((o) => o.value === value)?.label ?? value;
}
