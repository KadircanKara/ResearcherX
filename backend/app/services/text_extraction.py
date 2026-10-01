"""Uploaded non-PDF papers: format detection and bytes -> markdown.

PURE: no DB, no ORM, no settings, no service imports, no PyMuPDF. PDFs are
`pdf_extraction.py`'s, the only module that touches PyMuPDF; this module
only RECOGNISES a PDF (so the upload route can tell the formats apart) and
never parses one.

Supported uploads are exactly `.pdf`, `.docx`, `.md`/`.markdown`, `.txt`
and `.rtf`. Detection is by extension, CONFIRMED by content: an extension
alone is a claim the uploader makes, and a PDF renamed `.txt` would
otherwise be indexed as a page of binary noise. Anything else, or a
mismatch, is an `UnsupportedFileType` whose `message` is safe to show the
client verbatim.

What each format becomes, all of it fed to the SAME markdown chunker as
`index_manual` (`structured_chunker.chunk_markdown`, tier 3: sections from
headings, no pages):

    .docx      mammoth -> HTML -> markdown here. Word "Heading 1/2/3" styles
               become `#`/`##`/`###`; images are dropped.
    .md        decoded, kept as markdown.
    .txt       decoded, kept as is. Deliberately NOT escaped: a line like
               `## Methods` cuts a section exactly as it does in text pasted
               into the Manual tab, which takes the same chunker. One rule
               for both plain-text paths, and a re-chunk from the stored
               `extracted_text` reproduces it exactly.
    .rtf       striprtf -> plain text.

Why mammoth -> HTML -> markdown rather than `mammoth.convert_to_markdown`:
mammoth's markdown writer is deprecated upstream, and it backslash-escapes
punctuation (`1\\. Introduction`), which breaks the numbered-heading
detection in `section_outline.heading_level` and puts stray backslashes
into the text that is embedded and quoted back to the reader. The HTML
mammoth emits is a small, fixed vocabulary (p, h1-h6, lists, tables,
inline marks), so the stdlib `HTMLParser` below covers it without a third
dependency.
"""

from __future__ import annotations

import codecs
import io
import re
import zipfile
import zlib
from html.parser import HTMLParser

PDF = "pdf"
DOCX = "docx"
MARKDOWN = "markdown"
TEXT = "text"
RTF = "rtf"

_FORMAT_BY_EXTENSION = {
    ".pdf": PDF,
    ".docx": DOCX,
    ".md": MARKDOWN,
    ".markdown": MARKDOWN,
    ".txt": TEXT,
    ".rtf": RTF,
}

CONTENT_TYPES = {
    PDF: "application/pdf",
    DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    MARKDOWN: "text/markdown",
    TEXT: "text/plain",
    RTF: "application/rtf",
}

_EXTENSION_BY_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    CONTENT_TYPES[DOCX]: ".docx",
    "text/markdown": ".md",
    "text/plain": ".txt",
    "application/rtf": ".rtf",
}

UNSUPPORTED_MESSAGE = "Unsupported file type. Upload a PDF, DOCX, Markdown, TXT or RTF file."

# A .docx is a zip, and a zip's declared sizes are attacker-controlled
# metadata. These bound what is actually DECOMPRESSED, counted byte by byte
# (the `latex_archive._read_bounded` rule). 100MB is generous: the upload
# cap is 30MB and Word's own parts compress roughly 4-10x, with images
# stored nearly as-is.
DOCX_MAX_UNCOMPRESSED = 100 * 1024 * 1024
DOCX_MAX_ENTRIES = 5000
_DOCX_MAIN_PART = "word/document.xml"
_READ_CHUNK = 64 * 1024

_ZIP_MAGIC = b"PK\x03\x04"
_RTF_MAGIC = b"{\\rtf"
_PDF_MAGIC = b"%PDF"
# PDF readers accept junk before the header within the first 1024 bytes.
_PDF_HEADER_WINDOW = 1024

# Text formats: a sample with more than this share of C0 control characters
# (tab, newline, carriage return and form feed excepted) is binary.
_TEXT_SAMPLE = 8192
_MAX_CONTROL_RATIO = 0.05


class UnsupportedFileType(Exception):
    """An extension we do not take, or content that contradicts it.

    `message` is client-safe; the route returns it as a 422.
    """

    def __init__(self, message: str = UNSUPPORTED_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


class UnreadableFile(Exception):
    """The right kind of file, but not one we can read: a corrupt archive,
    one over the decompression bound, a parser failure. Client-safe text."""

    message = "This file could not be read. Check that it opens on your computer and try again."


def _mismatch(fmt: str) -> UnsupportedFileType:
    ext = {PDF: ".pdf", DOCX: ".docx", MARKDOWN: "Markdown", TEXT: ".txt", RTF: ".rtf"}[fmt]
    return UnsupportedFileType(
        f"This file's contents don't match its {ext} extension. "
        "Upload a PDF, DOCX, Markdown, TXT or RTF file."
    )


# ── detection ───────────────────────────────────────────────────────────────


def format_for_extension(ext: str | None) -> str:
    """The format an extension names. Accepts `docx`, `.docx`, `.DOCX` or a
    whole filename. `None`/empty means PDF: the upload route predates the
    other formats, and a client that sends no extension is an old one."""
    if not ext:
        return PDF
    ext = ext.strip().lower()
    if "." in ext:
        ext = ext[ext.rindex(".") :]
    else:
        ext = "." + ext
    fmt = _FORMAT_BY_EXTENSION.get(ext)
    if fmt is None:
        raise UnsupportedFileType()
    return fmt


def detect_format(ext: str | None, data: bytes) -> str:
    """The format named by `ext`, confirmed against the bytes.

    An EMPTY file is accepted for the text formats (it indexes as zero
    chunks) and refused for the binary ones, which cannot be empty.
    """
    fmt = format_for_extension(ext)
    if fmt == PDF:
        if _PDF_MAGIC not in data[:_PDF_HEADER_WINDOW]:
            raise _mismatch(fmt)
    elif fmt == DOCX:
        check_docx(data)
    elif fmt == RTF:
        if not _strip_bom(data).lstrip().startswith(_RTF_MAGIC):
            raise _mismatch(fmt)
    else:
        _check_text(fmt, data)
    return fmt


def content_type_for(fmt: str) -> str:
    return CONTENT_TYPES[fmt]


def extension_for_content_type(content_type: str | None) -> str:
    """The download extension for a stored file. Rows written before the
    other formats existed all say `application/pdf`; anything unknown is
    served as a PDF too, which is the only thing that could have been
    stored then."""
    base = (content_type or "").split(";", 1)[0].strip().lower()
    return _EXTENSION_BY_CONTENT_TYPE.get(base, ".pdf")


def _strip_bom(data: bytes) -> bytes:
    return data[len(codecs.BOM_UTF8) :] if data.startswith(codecs.BOM_UTF8) else data


def _check_text(fmt: str, data: bytes) -> None:
    head = _strip_bom(data)
    if head.startswith((_ZIP_MAGIC, _PDF_MAGIC, _RTF_MAGIC)):
        raise _mismatch(fmt)
    sample = decode_text(data[:_TEXT_SAMPLE])
    if not sample:
        return
    control = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\t\n\r\f")
    if control / len(sample) > _MAX_CONTROL_RATIO:
        raise _mismatch(fmt)


def check_docx(
    data: bytes,
    *,
    max_uncompressed: int = DOCX_MAX_UNCOMPRESSED,
    max_entries: int = DOCX_MAX_ENTRIES,
) -> None:
    """Refuse anything that is not a sane Word document BEFORE a parser
    sees it.

    Not a zip, or a zip without `word/document.xml` -> `UnsupportedFileType`
    (a mismatch). Too many entries, or more than `max_uncompressed` bytes
    once ACTUALLY decompressed -> `UnreadableFile`. The declared
    `ZipInfo.file_size` total is checked first only because it is free; it
    is never trusted, since a header claiming 1KB can front gigabytes. Every
    entry is streamed and counted, nothing is kept. Decompression is
    deterministic, so the parser that reads this same archive afterwards
    reads exactly the bytes counted here.
    """
    if not data.startswith(_ZIP_MAGIC):
        raise _mismatch(DOCX)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError):
        raise _mismatch(DOCX) from None
    with zf:
        if len(infos) > max_entries:
            raise UnreadableFile()
        if _DOCX_MAIN_PART not in {i.filename for i in infos}:
            raise _mismatch(DOCX)
        if sum(i.file_size for i in infos) > max_uncompressed:
            raise UnreadableFile()
        total = 0
        try:
            for info in infos:
                if info.is_dir():
                    continue
                with zf.open(info) as fh:
                    while chunk := fh.read(_READ_CHUNK):
                        total += len(chunk)
                        if total > max_uncompressed:
                            raise UnreadableFile()
        except UnreadableFile:
            raise
        except (
            zipfile.BadZipFile,
            zlib.error,
            NotImplementedError,  # unsupported compression method
            RuntimeError,  # encrypted entry
            OSError,
            EOFError,
            ValueError,
        ):
            raise UnreadableFile() from None


# ── decoding ───────────────────────────────────────────────────────────────


def decode_text(data: bytes) -> str:
    """Bytes of a text file -> str. A BOM decides when present (UTF-8, or
    UTF-16 as Windows Notepad's "Unicode" writes it); then strict UTF-8,
    then cp1252 (what "ANSI" means on a Western Windows), then latin-1,
    which cannot fail. cp1252 leaves five bytes undefined, so a file using
    them falls through to latin-1 rather than raising."""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


_BLANK_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_WS_RE = re.compile(r"[ \t]+\n")


def normalize_text(text: str) -> str:
    """One newline convention, no NULs, no trailing whitespace on a line,
    no run of more than one blank line. Whitespace-only becomes ""."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _TRAILING_WS_RE.sub("\n", text)
    text = _BLANK_RUN_RE.sub("\n\n", text)
    return text.strip()


# ── docx ───────────────────────────────────────────────────────────────────

# `_HEADING_RE` in structured_chunker reads at most four `#`.
_MAX_HEADING_DEPTH = 4
_BLOCK_TAGS = {"p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}
_WS_RE = re.compile(r"[ \t\r\f\v ]+")


class _HtmlToMarkdown(HTMLParser):
    """mammoth's HTML -> markdown blocks. Headings become `#` lines; every
    other block becomes a paragraph; table rows become one line of cells
    joined by " | "; list items get "- ". Inline marks are flattened to their
    text. Images carry no text and are dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buf: list[str] = []
        self._heading = 0
        self._cells: list[str] | None = None
        self._skip_link = 0
        self._li_depth = 0

    def _prefix(self) -> str:
        return "- " if self._li_depth else ""

    def _flush(self) -> str:
        raw = "".join(self._buf)
        self._buf = []
        lines = (_WS_RE.sub(" ", ln).strip() for ln in raw.split("\n"))
        return "\n".join(ln for ln in lines if ln)

    def _emit(self, prefix: str = "") -> None:
        text = self._flush()
        if text:
            self.blocks.append(prefix + text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("td", "th"):
            if self._cells is not None:
                self._cells.append(self._flush())
            return
        if tag == "tr":
            self._emit()
            self._cells = []
            return
        if tag in _BLOCK_TAGS:
            if self._cells is None:
                # Text before a nested block (a list item holding a sub-list)
                # is its own block.
                self._emit(self._prefix())
                if tag == "li":
                    self._li_depth += 1
                elif tag[0] == "h" and tag[1:].isdigit():
                    self._heading = int(tag[1:])
        elif tag == "br":
            self._buf.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            # mammoth's footnote/endnote back-links render as a bare "↑".
            if href.startswith(("#footnote-ref-", "#endnote-ref-")):
                self._skip_link += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            if self._cells is not None:
                self._cells.append(self._flush())
            return
        if tag == "tr":
            cells = [c.replace("\n", " ") for c in (self._cells or []) if c]
            self._cells = None
            if cells:
                self.blocks.append(" | ".join(cells))
            return
        if tag == "a" and self._skip_link:
            self._skip_link -= 1
            return
        if tag in _BLOCK_TAGS and self._cells is None:
            if self._heading:
                level = min(self._heading, _MAX_HEADING_DEPTH)
                text = self._flush().replace("\n", " ")
                if text:
                    self.blocks.append("#" * level + " " + text)
                self._heading = 0
            else:
                self._emit(self._prefix())
                if tag == "li":
                    self._li_depth = max(0, self._li_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_link:
            return
        # Source newlines in HTML are whitespace; only <br> breaks a line.
        self._buf.append(data.replace("\n", " "))

    def close(self) -> None:
        super().close()
        self._emit()


def html_to_markdown(html: str) -> str:
    parser = _HtmlToMarkdown()
    parser.feed(html)
    parser.close()
    return "\n\n".join(parser.blocks)


def _docx_to_markdown(data: bytes) -> str:
    import mammoth

    check_docx(data)
    # The image is never opened, so its bytes are never read or base64'd.
    drop_images = mammoth.images.img_element(lambda _image: {})
    try:
        result = mammoth.convert_to_html(io.BytesIO(data), convert_image=drop_images)
    except Exception:  # noqa: BLE001 — any parser failure is "unreadable", never a 500
        raise UnreadableFile() from None
    return html_to_markdown(result.value)


def _rtf_to_text(data: bytes) -> str:
    from striprtf.striprtf import rtf_to_text

    # RTF is 7-bit by spec with `\'hh` escapes in the document's code page,
    # but real writers also emit raw 8-bit bytes; cp1252 is the overwhelming
    # default (`\ansicpg1252`). `replace`, never `strict`: one bad byte must
    # not cost the whole document.
    source = _strip_bom(data).decode("cp1252", errors="replace")
    try:
        return rtf_to_text(source, encoding="cp1252", errors="replace")
    except Exception:  # noqa: BLE001
        raise UnreadableFile() from None


def extract_markdown(fmt: str, data: bytes) -> str:
    """Bytes of a non-PDF upload -> normalised markdown. "" when the file
    holds no text, which the caller indexes as zero chunks.

    Assumes `detect_format` already accepted the bytes; the docx bound is
    re-checked here anyway, because it is what stands between a parser and a
    zip bomb and must not depend on the caller remembering it.
    """
    if fmt == DOCX:
        text = _docx_to_markdown(data)
    elif fmt == RTF:
        text = _rtf_to_text(data)
    elif fmt in (MARKDOWN, TEXT):
        text = decode_text(data)
    else:
        raise ValueError(f"extract_markdown does not handle {fmt!r}")
    return normalize_text(text)
