import "server-only";
import { site } from "@/config/site";
import {
  budgetOptions,
  type Inquiry,
  optionLabel,
  serviceOptions,
  timelineOptions,
} from "@/lib/contact/schema";

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function rows(inquiry: Inquiry): [string, string | undefined][] {
  return [
    ["Name", inquiry.name],
    ["Email", inquiry.email],
    ["Company", inquiry.company],
    ["Location", inquiry.location],
    ["Service", optionLabel(serviceOptions, inquiry.service)],
    ["Budget", optionLabel(budgetOptions, inquiry.budget)],
    ["Timeline", optionLabel(timelineOptions, inquiry.timeline)],
  ];
}

/** Build the notification email. All user input is escaped in the HTML part. */
export function buildInquiryEmail(inquiry: Inquiry, submissionId: string) {
  const subject = `New project inquiry — ${inquiry.name}${inquiry.company ? ` (${inquiry.company})` : ""}`;
  const fieldLines = rows(inquiry)
    .filter(([, v]) => v)
    .map(([k, v]) => `${k}: ${v}`)
    .join("\n");
  const text = `${fieldLines}\n\nProject description:\n${inquiry.message}\n\n—\nSubmitted via the ${site.name} website. Reference: ${submissionId}`;

  const htmlRows = rows(inquiry)
    .filter(([, v]) => v)
    .map(
      ([k, v]) =>
        `<tr><td style="padding:4px 16px 4px 0;color:#666;vertical-align:top">${escapeHtml(k)}</td><td style="padding:4px 0">${escapeHtml(v ?? "")}</td></tr>`,
    )
    .join("");
  const html = `<div style="font-family:system-ui,sans-serif;font-size:14px;line-height:1.5;color:#111">
<h2 style="font-size:18px;margin:0 0 12px">New project inquiry</h2>
<table style="border-collapse:collapse">${htmlRows}</table>
<h3 style="font-size:15px;margin:20px 0 6px">Project description</h3>
<p style="white-space:pre-wrap;margin:0">${escapeHtml(inquiry.message)}</p>
<p style="color:#888;font-size:12px;margin-top:24px">Submitted via the ${escapeHtml(site.name)} website. Reference: ${escapeHtml(submissionId)}</p>
</div>`;
  return { subject, text, html };
}
