/**
 * Business document file rules. The declared Content-Type and file name are
 * untrusted; the type is decided from the file's own bytes.
 */
export const DOCUMENT_MAX_BYTES = 10 * 1024 * 1024;
export const DOCUMENT_TYPES = [
  "BUSINESS_LICENSE",
  "COMMERCIAL_REGISTRATION",
  "TIN_CERTIFICATE",
  "VAT_CERTIFICATE",
  "MEMORANDUM_OF_ASSOCIATION",
  "OTHER",
] as const;
export type BusinessDocumentType = (typeof DOCUMENT_TYPES)[number];

/** Documents that must be approved before a business can be verified. */
export const REQUIRED_DOCUMENT_TYPES: readonly BusinessDocumentType[] = ["BUSINESS_LICENSE"];

export type AllowedMime = "application/pdf" | "image/jpeg" | "image/png";

export function sniffDocumentType(buf: Buffer): AllowedMime | null {
  if (buf.length >= 5 && buf.subarray(0, 5).toString("latin1") === "%PDF-") return "application/pdf";
  if (buf.length >= 3 && buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) return "image/jpeg";
  if (buf.length >= 8 && buf.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return "image/png";
  return null;
}

/**
 * PDFs that can run code or carry other files are refused outright. This is a coarse
 * pre-filter (names can be obfuscated); the malware scanner is the real check.
 */
const RISKY_PDF_NAMES = ["/JavaScript", "/JS", "/Launch", "/EmbeddedFile", "/RichMedia", "/XFA"];

export function riskyPdfFeatures(buf: Buffer): string[] {
  const text = buf.toString("latin1");
  return RISKY_PDF_NAMES.filter((name) => new RegExp(`${name.replace("/", "\\/")}(?![A-Za-z])`).test(text));
}

export type DocumentRejection = "empty" | "too_large" | "unsupported_type" | "type_mismatch" | "active_content";

export function checkDocument(buf: Buffer, declaredMime: string | undefined): { ok: true; mime: AllowedMime } | { ok: false; reason: DocumentRejection } {
  if (buf.length === 0) return { ok: false, reason: "empty" };
  if (buf.length > DOCUMENT_MAX_BYTES) return { ok: false, reason: "too_large" };
  const mime = sniffDocumentType(buf);
  if (!mime) return { ok: false, reason: "unsupported_type" };
  const declared = (declaredMime ?? "").toLowerCase().split(";")[0]!.trim();
  // Browsers sometimes send image/jpg; anything else that disagrees with the bytes is refused.
  const normalized = declared === "image/jpg" ? "image/jpeg" : declared;
  if (normalized && normalized !== "application/octet-stream" && normalized !== mime) return { ok: false, reason: "type_mismatch" };
  if (mime === "application/pdf" && riskyPdfFeatures(buf).length > 0) return { ok: false, reason: "active_content" };
  return { ok: true, mime };
}
