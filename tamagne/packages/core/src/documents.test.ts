import { describe, expect, it } from "vitest";
import { DOCUMENT_MAX_BYTES, checkDocument, riskyPdfFeatures, sniffDocumentType } from "./index.js";

const pdf = Buffer.from("%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF");
const jpeg = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 0x10, 0x4a, 0x46, 0x49, 0x46]);
const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0x0d]);

describe("document checks", () => {
  it("decides the type from the bytes", () => {
    expect(sniffDocumentType(pdf)).toBe("application/pdf");
    expect(sniffDocumentType(jpeg)).toBe("image/jpeg");
    expect(sniffDocumentType(png)).toBe("image/png");
    expect(sniffDocumentType(Buffer.from("MZ\x90\x00"))).toBeNull(); // Windows executable
    expect(sniffDocumentType(Buffer.from("<html><script>"))).toBeNull();
    expect(sniffDocumentType(Buffer.from("PK\x03\x04"))).toBeNull(); // zip / docx
  });

  it("accepts matching or generic declared types", () => {
    expect(checkDocument(pdf, "application/pdf")).toEqual({ ok: true, mime: "application/pdf" });
    expect(checkDocument(jpeg, "image/jpg")).toEqual({ ok: true, mime: "image/jpeg" });
    expect(checkDocument(png, "application/octet-stream")).toEqual({ ok: true, mime: "image/png" });
  });

  it("refuses empty, oversized, unknown and disguised files", () => {
    expect(checkDocument(Buffer.alloc(0), "application/pdf")).toEqual({ ok: false, reason: "empty" });
    const big = Buffer.concat([pdf, Buffer.alloc(DOCUMENT_MAX_BYTES)]);
    expect(checkDocument(big, "application/pdf")).toEqual({ ok: false, reason: "too_large" });
    expect(checkDocument(Buffer.from("MZ\x90\x00"), "application/pdf")).toEqual({ ok: false, reason: "unsupported_type" });
    expect(checkDocument(png, "application/pdf")).toEqual({ ok: false, reason: "type_mismatch" });
    expect(checkDocument(pdf, "text/html")).toEqual({ ok: false, reason: "type_mismatch" });
  });

  it("refuses PDFs with active content", () => {
    const js = Buffer.from("%PDF-1.7\n<< /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>");
    expect(riskyPdfFeatures(js)).toEqual(["/JavaScript", "/JS"]);
    expect(checkDocument(js, "application/pdf")).toEqual({ ok: false, reason: "active_content" });
    expect(checkDocument(Buffer.from("%PDF-1.7\n<< /Launch << /F (cmd.exe) >> >>"), "application/pdf")).toEqual({ ok: false, reason: "active_content" });
    // Similar-looking names are not false positives.
    expect(riskyPdfFeatures(Buffer.from("%PDF-1.7\n<< /JSONData 1 >>"))).toEqual([]);
  });
});
