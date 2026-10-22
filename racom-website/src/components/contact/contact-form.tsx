"use client";

import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import {
  budgetOptions,
  type Inquiry,
  type InquiryInput,
  inquirySchema,
  serviceOptions,
  timelineOptions,
} from "@/lib/contact/schema";
import type { ContactApiResponse } from "@/lib/contact/api";
import { buttonClasses } from "@/components/ui/button";
import { cn } from "@/lib/cn";

type Status =
  | { state: "idle" }
  | { state: "success"; delivery: "email" | "local"; reference: string }
  | { state: "error"; message: string };

const fieldBase =
  "mt-2 block w-full rounded-xl border bg-midnight px-4 py-3 text-[0.95rem] text-ink placeholder:text-subtle transition-colors focus:border-violet-soft focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan";

function fieldClass(invalid: boolean) {
  return cn(fieldBase, invalid ? "border-danger/70" : "border-line-strong hover:border-white/25");
}

function Label({
  htmlFor,
  children,
  optional,
}: {
  htmlFor: string;
  children: React.ReactNode;
  optional?: boolean;
}) {
  return (
    <label htmlFor={htmlFor} className="text-sm font-medium text-ink">
      {children}
      {optional ? (
        <span className="ml-1.5 font-normal text-subtle">(optional)</span>
      ) : (
        <span className="sr-only"> (required)</span>
      )}
    </label>
  );
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} className="mt-2 flex items-center gap-1.5 text-sm text-danger">
      <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
      {message}
    </p>
  );
}

export function ContactForm({
  defaultService,
  defaultMessage,
  contactEmail,
}: {
  defaultService?: string;
  defaultMessage?: string;
  contactEmail: string | null;
}) {
  const [status, setStatus] = useState<Status>({ state: "idle" });
  const [startedAt] = useState(() => Date.now());
  const [honeypot, setHoneypot] = useState("");

  const {
    register,
    handleSubmit,
    setError,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<InquiryInput, unknown, Inquiry>({
    resolver: zodResolver(inquirySchema),
    mode: "onTouched",
    defaultValues: {
      name: "",
      email: "",
      company: "",
      location: "",
      service: (serviceOptions.some((o) => o.value === defaultService)
        ? defaultService
        : "") as InquiryInput["service"],
      message: defaultMessage ?? "",
      budget: "",
      timeline: "",
    },
  });

  async function onSubmit(values: Inquiry) {
    setStatus({ state: "idle" });
    let data: ContactApiResponse | null = null;
    try {
      const res = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...values, website: honeypot, startedAt }),
      });
      data = (await res.json().catch(() => null)) as ContactApiResponse | null;
    } catch {
      setStatus({
        state: "error",
        message: "We could not reach the server. Check your connection and try again.",
      });
      return;
    }
    if (data?.ok) {
      setStatus({ state: "success", delivery: data.delivery, reference: data.reference });
      return;
    }
    const error = data?.error;
    if (error?.fields) {
      let first: keyof InquiryInput | undefined;
      for (const [key, message] of Object.entries(error.fields)) {
        if (key in inquirySchema.shape) {
          setError(key as keyof InquiryInput, { type: "server", message });
          first ??= key as keyof InquiryInput;
        }
      }
      if (first) setFocus(first);
    }
    setStatus({ state: "error", message: error?.message ?? "Something went wrong. Please try again." });
  }

  if (status.state === "success") {
    return (
      <div role="status" className="surface rounded-2xl p-8 sm:p-10" tabIndex={-1} ref={(el) => el?.focus()}>
        <CheckCircle2 aria-hidden="true" className="h-10 w-10 text-success" />
        {status.delivery === "email" ? (
          <>
            <h2 className="mt-6 font-display text-2xl font-semibold tracking-tight text-ink">
              Thank you — your inquiry was sent.
            </h2>
            <p className="mt-3 leading-relaxed text-muted">
              We have received your project details and will reply to the email address you provided. Keep
              this reference in case you need to follow up:
            </p>
          </>
        ) : (
          <>
            <h2 className="mt-6 font-display text-2xl font-semibold tracking-tight text-ink">
              Saved locally (development mode)
            </h2>
            <p className="mt-3 leading-relaxed text-muted">
              Email delivery is not configured, so this inquiry was{" "}
              <strong className="text-ink">not emailed</strong>. It was saved to{" "}
              <code className="text-ink">.data/inquiries/</code> on the development server. Configure Resend
              in <code className="text-ink">.env.local</code> to deliver inquiries by email.
            </p>
          </>
        )}
        <p className="mt-4 font-mono text-sm text-violet-soft">Ref: {status.reference}</p>
        <Link href="/work" className={buttonClasses({ variant: "secondary", className: "mt-8" })}>
          Explore our work while you wait
        </Link>
      </div>
    );
  }

  const describedBy = (name: keyof InquiryInput, hint?: string) =>
    [errors[name] ? `${name}-error` : null, hint].filter(Boolean).join(" ") || undefined;

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      noValidate
      className="space-y-7"
      aria-describedby="form-required-note"
    >
      <p id="form-required-note" className="text-sm text-subtle">
        Fields are required unless marked optional.
      </p>

      <div className="grid gap-7 sm:grid-cols-2">
        <div>
          <Label htmlFor="name">Name</Label>
          <input
            id="name"
            type="text"
            autoComplete="name"
            className={fieldClass(!!errors.name)}
            aria-invalid={!!errors.name}
            aria-describedby={describedBy("name")}
            {...register("name")}
          />
          <FieldError id="name-error" message={errors.name?.message} />
        </div>
        <div>
          <Label htmlFor="email">Work email</Label>
          <input
            id="email"
            type="email"
            inputMode="email"
            autoComplete="email"
            className={fieldClass(!!errors.email)}
            aria-invalid={!!errors.email}
            aria-describedby={describedBy("email")}
            {...register("email")}
          />
          <FieldError id="email-error" message={errors.email?.message} />
        </div>
        <div>
          <Label htmlFor="company" optional>
            Company
          </Label>
          <input
            id="company"
            type="text"
            autoComplete="organization"
            className={fieldClass(!!errors.company)}
            aria-invalid={!!errors.company}
            aria-describedby={describedBy("company")}
            {...register("company")}
          />
          <FieldError id="company-error" message={errors.company?.message} />
        </div>
        <div>
          <Label htmlFor="location" optional>
            Country or location
          </Label>
          <input
            id="location"
            type="text"
            autoComplete="country-name"
            className={fieldClass(!!errors.location)}
            aria-invalid={!!errors.location}
            aria-describedby={describedBy("location")}
            {...register("location")}
          />
          <FieldError id="location-error" message={errors.location?.message} />
        </div>
      </div>

      <div>
        <Label htmlFor="service">Service needed</Label>
        <select
          id="service"
          className={cn(
            fieldClass(!!errors.service),
            "bg-[url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 20 20%22 fill=%22%23A5A9BA%22><path d=%22M5.5 7.5 10 12l4.5-4.5%22 stroke=%22%23A5A9BA%22 stroke-width=%221.5%22 fill=%22none%22/></svg>')] appearance-none bg-[length:1.25rem] bg-[right_1rem_center] bg-no-repeat pr-11",
          )}
          aria-invalid={!!errors.service}
          aria-describedby={describedBy("service")}
          {...register("service")}
        >
          <option value="">Choose a service…</option>
          {serviceOptions.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <FieldError id="service-error" message={errors.service?.message} />
      </div>

      <div>
        <Label htmlFor="message">Project description</Label>
        <p id="message-hint" className="mt-1 text-sm text-subtle">
          What are you building or improving, who is it for, and what does success look like?
        </p>
        <textarea
          id="message"
          rows={6}
          className={cn(fieldClass(!!errors.message), "min-h-40 resize-y")}
          aria-invalid={!!errors.message}
          aria-describedby={describedBy("message", "message-hint")}
          {...register("message")}
        />
        <FieldError id="message-error" message={errors.message?.message} />
      </div>

      <div className="grid gap-7 sm:grid-cols-2">
        <div>
          <Label htmlFor="budget" optional>
            Budget range (USD)
          </Label>
          <select id="budget" className={cn(fieldClass(!!errors.budget), "pr-4")} {...register("budget")}>
            <option value="">Prefer not to say</option>
            {budgetOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="timeline" optional>
            Preferred timeline
          </Label>
          <select
            id="timeline"
            className={cn(fieldClass(!!errors.timeline), "pr-4")}
            {...register("timeline")}
          >
            <option value="">No preference</option>
            {timelineOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Honeypot: hidden from people and assistive technology; bots tend to fill it. */}
      <div aria-hidden="true" className="absolute -left-[10000px] h-px w-px overflow-hidden">
        <label htmlFor="website">Website</label>
        <input
          id="website"
          name="website"
          type="text"
          tabIndex={-1}
          autoComplete="off"
          value={honeypot}
          onChange={(e) => setHoneypot(e.target.value)}
        />
      </div>

      <div>
        <div className="flex gap-3">
          <input
            id="consent"
            type="checkbox"
            className="mt-1 h-5 w-5 shrink-0 rounded border-line-strong bg-midnight accent-violet-strong"
            aria-invalid={!!errors.consent}
            aria-describedby={describedBy("consent")}
            {...register("consent")}
          />
          <label htmlFor="consent" className="text-sm leading-relaxed text-muted">
            I agree that Racom Technology may use these details to respond to my inquiry, as described in the{" "}
            <Link href="/privacy" className="text-violet-soft underline underline-offset-2 hover:text-ink">
              Privacy Policy
            </Link>
            .
          </label>
        </div>
        <FieldError id="consent-error" message={errors.consent?.message} />
      </div>

      {status.state === "error" && (
        <div
          role="alert"
          className="flex gap-3 rounded-xl border border-danger/40 bg-danger/[0.07] p-4 text-sm leading-relaxed"
        >
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
          <div>
            <p className="font-medium text-ink">Your inquiry was not sent.</p>
            <p className="mt-1 text-muted">{status.message}</p>
            {contactEmail && (
              <p className="mt-1 text-muted">
                You can also email us directly at{" "}
                <a className="text-violet-soft underline underline-offset-2" href={`mailto:${contactEmail}`}>
                  {contactEmail}
                </a>
                .
              </p>
            )}
          </div>
        </div>
      )}

      <button
        type="submit"
        disabled={isSubmitting}
        className={buttonClasses({ size: "lg", className: "w-full sm:w-auto" })}
      >
        {isSubmitting ? (
          <>
            <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            Sending…
          </>
        ) : (
          "Send inquiry"
        )}
      </button>
      <p className="sr-only" aria-live="polite">
        {isSubmitting ? "Sending your inquiry…" : ""}
      </p>
    </form>
  );
}
