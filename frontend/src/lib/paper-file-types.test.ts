import { describe, expect, it } from "vitest";
import {
  UPLOAD_ACCEPT,
  extensionForType,
  isPdfName,
  uploadExtension,
} from "./paper-file-types";

describe("uploadExtension", () => {
  it("names the five formats whatever the case, and nothing else", () => {
    expect(uploadExtension("a.pdf")).toBe("pdf");
    expect(uploadExtension("Paper.v2.DOCX")).toBe("docx");
    expect(uploadExtension("notes.md")).toBe("md");
    expect(uploadExtension("notes.Markdown")).toBe("markdown");
    expect(uploadExtension("plain.TXT")).toBe("txt");
    expect(uploadExtension("old.rtf")).toBe("rtf");
    for (const name of ["a.doc", "a.odt", "a.html", "a.tex", "pdf", "README", "a.pdf.zip"]) {
      expect(uploadExtension(name)).toBeNull();
    }
  });

  it("matches the input's accept list", () => {
    expect(UPLOAD_ACCEPT).toBe(".pdf,.docx,.md,.markdown,.txt,.rtf");
  });
});

describe("isPdfName", () => {
  it("is true only for PDFs, which alone get a suggested title", () => {
    expect(isPdfName("x.PDF")).toBe(true);
    expect(isPdfName("x.docx")).toBe(false);
    expect(isPdfName("x.txt")).toBe(false);
  });
});

describe("extensionForType", () => {
  it("names a download after the type it was served with", () => {
    expect(extensionForType("application/pdf")).toBe(".pdf");
    expect(
      extensionForType("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    ).toBe(".docx");
    expect(extensionForType("text/markdown")).toBe(".md");
    expect(extensionForType("text/plain;charset=utf-8")).toBe(".txt");
    expect(extensionForType("application/rtf")).toBe(".rtf");
  });

  it("falls back to a PDF, the only thing stored before other formats", () => {
    expect(extensionForType("")).toBe(".pdf");
    expect(extensionForType("application/octet-stream")).toBe(".pdf");
  });
});
