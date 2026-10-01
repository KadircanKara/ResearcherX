"""text_extraction: detection by extension confirmed by content, and
bytes -> markdown for every non-PDF upload format. Pure, so no DB."""

import codecs
import io
import zipfile

import pytest

from app.services import text_extraction as te
from app.services.paper_ingest_service import chunk_records_for_markdown
from tests.docx_builder import build_docx

PDF_BYTES = b"%PDF-1.4\n%fake"


# ── detection ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ext", "fmt"),
    [
        ("pdf", te.PDF),
        (".PDF", te.PDF),
        ("docx", te.DOCX),
        ("md", te.MARKDOWN),
        ("Markdown", te.MARKDOWN),
        ("txt", te.TEXT),
        ("rtf", te.RTF),
        ("My Paper.v2.DOCX", te.DOCX),
        (None, te.PDF),
        ("", te.PDF),
    ],
)
def test_the_supported_extensions_and_nothing_else(ext, fmt):
    assert te.format_for_extension(ext) == fmt


@pytest.mark.parametrize("ext", ["doc", "odt", "html", "tex", "png", "zip", "pages", "pdfx"])
def test_every_other_extension_is_refused_with_the_client_message(ext):
    with pytest.raises(te.UnsupportedFileType) as exc:
        te.format_for_extension(ext)
    assert exc.value.message == te.UNSUPPORTED_MESSAGE


def test_content_confirms_the_extension():
    assert te.detect_format("pdf", PDF_BYTES) == te.PDF
    assert te.detect_format("docx", build_docx([(None, "x")])) == te.DOCX
    assert te.detect_format("rtf", rb"{\rtf1 hi}") == te.RTF
    assert te.detect_format("txt", b"plain words") == te.TEXT
    assert te.detect_format("md", b"# Title\n\nbody") == te.MARKDOWN


@pytest.mark.parametrize(
    ("ext", "data"),
    [
        ("pdf", b"not a pdf at all"),
        ("docx", PDF_BYTES),
        ("docx", b"plain text"),
        ("rtf", b"plain text"),
        ("txt", PDF_BYTES),
        ("md", build_docx([(None, "x")])),
        ("txt", rb"{\rtf1 hi}"),
        ("txt", bytes(range(256)) * 4),  # binary noise
    ],
)
def test_content_that_contradicts_the_extension_is_refused(ext, data):
    with pytest.raises(te.UnsupportedFileType) as exc:
        te.detect_format(ext, data)
    assert "don't match" in exc.value.message


def test_a_zip_that_is_not_a_word_document_is_a_mismatch():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    with pytest.raises(te.UnsupportedFileType):
        te.detect_format("docx", buf.getvalue())


def test_empty_text_files_are_accepted_and_empty_binaries_are_not():
    assert te.detect_format("txt", b"") == te.TEXT
    assert te.detect_format("md", b"") == te.MARKDOWN
    for ext in ("pdf", "docx", "rtf"):
        with pytest.raises(te.UnsupportedFileType):
            te.detect_format(ext, b"")


def test_the_download_extension_follows_the_stored_content_type():
    for fmt, ext in [
        (te.PDF, ".pdf"),
        (te.DOCX, ".docx"),
        (te.MARKDOWN, ".md"),
        (te.TEXT, ".txt"),
        (te.RTF, ".rtf"),
    ]:
        assert te.extension_for_content_type(te.content_type_for(fmt)) == ext
    assert te.extension_for_content_type("text/plain; charset=utf-8") == ".txt"
    assert te.extension_for_content_type(None) == ".pdf"


# ── docx ───────────────────────────────────────────────────────────────────


def test_word_heading_styles_become_sections():
    docx = build_docx(
        [
            (1, "1. Introduction"),
            (None, "Swarms search. Price $5 and a_b."),
            (2, "1.1 Background"),
            (None, "Prior work."),
            (3, "Detail"),
            (None, "Deeper."),
            (1, "2. Method"),
            (None, "The method."),
        ]
    )
    md = te.extract_markdown(te.DOCX, docx)
    assert md.startswith("# 1. Introduction\n\nSwarms search. Price $5 and a_b.")
    # No markdown escaping: mammoth's own writer would emit `1\. Introduction`.
    assert "\\" not in md

    records = chunk_records_for_markdown(md)
    assert [(r.section, r.text, r.page) for r in records] == [
        (("1. Introduction",), "Swarms search. Price $5 and a_b.", None),
        (("1. Introduction", "1.1 Background"), "Prior work.", None),
        (("1. Introduction", "1.1 Background", "Detail"), "Deeper.", None),
        (("2. Method",), "The method.", None),
    ]


def test_html_lists_tables_and_images_flatten_to_text_blocks():
    html = (
        "<h4>Deep</h4><h6>Deeper</h6><p>Line one<br />line two</p>"
        "<ul><li>alpha<ul><li>beta</li></ul></li></ul>"
        "<table><tr><td><p>a</p></td><td><h2>b</h2></td></tr></table>"
        '<p>after<img src="" /> table<sup><a href="#footnote-1">[1]</a></sup></p>'
        '<ol><li id="footnote-1"><p>A note. <a href="#footnote-ref-1">↑</a></p></li></ol>'
    )
    assert te.html_to_markdown(html).split("\n\n") == [
        "#### Deep",
        "#### Deeper",  # the chunker reads at most four `#`
        "Line one\nline two",
        "- alpha",
        "- beta",
        "a | b",
        "after table[1]",  # a heading inside a cell does not leak out
        "- A note.",
    ]


def test_a_docx_zip_bomb_is_refused_on_decompressed_bytes():
    bomb = build_docx([(None, "x")], extra={"word/media/pad.bin": b"\0" * (2 * 1024 * 1024)})
    assert len(bomb) < 64 * 1024  # tiny on the wire
    with pytest.raises(te.UnreadableFile):
        te.check_docx(bomb, max_uncompressed=1024 * 1024)
    te.check_docx(bomb, max_uncompressed=4 * 1024 * 1024)  # under the bound: fine


def test_an_entry_that_under_declares_its_size_is_refused():
    """A header claiming 1 byte for a 512KB entry passes the free declared-
    total check, so the refusal has to come from reading the entry: CPython
    stops at the declared size and the CRC then fails, which must surface
    as `UnreadableFile`, never as a parser seeing the rest."""
    payload = b"A" * (512 * 1024)
    docx = build_docx([(None, "x")], extra={"word/media/pad.bin": payload})
    # Rewrite the declared uncompressed size of the pad entry to 1 byte in
    # both the local and central headers.
    zf = zipfile.ZipFile(io.BytesIO(docx))
    info = zf.getinfo("word/media/pad.bin")
    real = len(payload).to_bytes(4, "little")
    lied = (1).to_bytes(4, "little")
    patched = bytearray(docx)
    for header_sig, size_offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        start = 0
        while (at := patched.find(header_sig, start)) != -1:
            if patched[at + size_offset : at + size_offset + 4] == real:
                patched[at + size_offset : at + size_offset + 4] = lied
            start = at + 4
    assert info.file_size == len(payload)
    with pytest.raises(te.UnreadableFile):
        te.check_docx(bytes(patched), max_uncompressed=256 * 1024)


def test_too_many_entries_is_refused():
    extra = {f"word/media/{i}.bin": b"" for i in range(20)}
    with pytest.raises(te.UnreadableFile):
        te.check_docx(build_docx([(None, "x")], extra=extra), max_entries=10)


def test_a_corrupt_word_document_is_unreadable_not_a_crash():
    docx = build_docx([(None, "x")])
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(docx)) as src, zipfile.ZipFile(buf, "w") as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "word/document.xml":
                data = b"<not-xml"
            dst.writestr(item, data)
    with pytest.raises(te.UnreadableFile):
        te.extract_markdown(te.DOCX, buf.getvalue())


def test_an_empty_word_document_is_empty_text():
    assert te.extract_markdown(te.DOCX, build_docx([])) == ""


# ── text, markdown, rtf ─────────────────────────────────────────────────────


def test_utf8_bom_is_stripped():
    data = codecs.BOM_UTF8 + "Café — naïve".encode()
    assert te.extract_markdown(te.TEXT, data) == "Café — naïve"


def test_cp1252_falls_back_cleanly():
    data = "“Smart quotes” and café".encode("cp1252")
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    assert te.extract_markdown(te.TEXT, data) == "“Smart quotes” and café"


def test_bytes_cp1252_leaves_undefined_fall_through_to_latin1():
    assert te.decode_text(b"a\x81b") == "a\x81b"


def test_utf16_with_bom_is_decoded():
    data = codecs.BOM_UTF16_LE + "Unicode notepad".encode("utf-16-le")
    assert te.detect_format("txt", data) == te.TEXT
    assert te.extract_markdown(te.TEXT, data) == "Unicode notepad"


def test_newlines_nuls_and_blank_runs_are_normalised():
    data = b"one\r\ntwo\rthree  \n\n\n\nfour\x00five\n"
    assert te.extract_markdown(te.TEXT, data) == "one\ntwo\nthree\n\nfourfive"


def test_whitespace_only_is_empty_and_chunks_to_nothing():
    md = te.extract_markdown(te.TEXT, b"  \n\r\n\t ")
    assert md == ""
    assert chunk_records_for_markdown(md) == []


def test_markdown_headings_become_sections():
    md = te.extract_markdown(te.MARKDOWN, b"# Intro\n\nHello.\n\n## Setup\n\nWorld.")
    records = chunk_records_for_markdown(md)
    assert [(r.section, r.text) for r in records] == [
        (("Intro",), "Hello."),
        (("Intro", "Setup"), "World."),
    ]


def test_plain_text_takes_the_same_chunker_as_manual_entry():
    """Documented choice: a .txt line that happens to read as a markdown
    heading cuts a section, exactly as in text pasted into the Manual tab."""
    md = te.extract_markdown(te.TEXT, b"Preamble.\n\n## Results\n\nNumbers.")
    records = chunk_records_for_markdown(md)
    assert [(r.section, r.text) for r in records] == [
        ((), "Preamble."),
        (("Results",), "Numbers."),
    ]


def test_rtf_becomes_plain_text():
    rtf = (
        rb"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0 Times;}}"
        rb"\f0\b Bold\b0  \'e9t\'e9 text.\par Second paragraph.\par}"
    )
    assert te.detect_format("rtf", rtf) == te.RTF
    text = te.extract_markdown(te.RTF, rtf)
    assert "Bold été text." in text
    assert "Second paragraph." in text
    assert "\\" not in text and "{" not in text
