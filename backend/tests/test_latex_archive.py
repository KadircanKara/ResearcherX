"""The archive parser is the boundary where an uploaded file stops being
hostile. One test per control in the spec's threat table, each building its
own crafted archive so the malicious shape is visible in the test."""

import io
import zipfile

import pytest

from app.core.config import settings
from app.services.latex_archive import (
    ArchiveTooLarge,
    EncryptedArchive,
    InvalidArchive,
    classify_binary,
    read_archive,
)


def _zip(entries: dict[str, bytes], **kw) -> bytes:
    """Build an archive in memory. `entries` maps arcname -> content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data, **kw)
    return buf.getvalue()


def test_a_plain_project_round_trips_its_files():
    blob = _zip({"main.tex": b"\\documentclass{article}", "refs.bib": b"@book{}"})
    entries = read_archive(blob)
    assert sorted(e.path for e in entries) == ["main.tex", "refs.bib"]
    assert all(e.is_binary is False for e in entries)


def test_a_single_common_top_level_directory_is_stripped():
    """Overleaf, arXiv and GitHub all wrap the project in one directory."""
    blob = _zip({"proj/main.tex": b"x", "proj/chapters/intro.tex": b"y"})
    assert sorted(e.path for e in read_archive(blob)) == ["chapters/intro.tex", "main.tex"]


def test_two_top_level_directories_are_not_stripped():
    blob = _zip({"a/main.tex": b"x", "b/other.tex": b"y"})
    assert sorted(e.path for e in read_archive(blob)) == ["a/main.tex", "b/other.tex"]


def test_a_root_level_file_prevents_stripping():
    blob = _zip({"main.tex": b"x", "figures/f.tex": b"y"})
    assert sorted(e.path for e in read_archive(blob)) == ["figures/f.tex", "main.tex"]


def test_a_traversal_entry_rejects_the_whole_archive():
    blob = _zip({"main.tex": b"x", "../../etc/passwd": b"pwned"})
    with pytest.raises(InvalidArchive) as exc:
        read_archive(blob)
    assert exc.value.entry is not None


def test_an_absolute_entry_rejects_the_whole_archive():
    blob = _zip({"main.tex": b"x", "/app/.env": b"secret"})
    with pytest.raises(InvalidArchive):
        read_archive(blob)


def test_a_backslash_entry_rejects_the_whole_archive():
    blob = _zip({"figures\\fig.png": b"x"})
    with pytest.raises(InvalidArchive):
        read_archive(blob)


def test_a_symlink_entry_rejects_the_whole_archive():
    """A symlink's CONTENT is its target path; storing it would let the
    compiler follow it out of the tree."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("main.tex", b"x")
        info = zipfile.ZipInfo("link.tex")
        info.create_system = 3  # Unix
        info.external_attr = 0o120777 << 16  # S_IFLNK
        z.writestr(info, b"/app/.env")
    with pytest.raises(InvalidArchive):
        read_archive(buf.getvalue())


def test_an_encrypted_entry_is_reported_as_password_protected():
    """`zipfile.writestr` recomputes the general-purpose flag bits on write
    and discards any the caller sets, so a real encrypted-looking archive has
    to be built by hand-patching the flag byte in the local file header and
    the central directory header after the fact (offsets 6 and 8 past each
    `PK\\x03\\x04` / `PK\\x01\\x02` signature)."""
    blob = bytearray(_zip({"main.tex": b"x"}))
    for sig, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        idx = blob.find(sig)
        assert idx != -1
        blob[idx + flag_offset] |= 0x1  # general-purpose bit 0 = encrypted
    with pytest.raises(EncryptedArchive):
        read_archive(bytes(blob))


def test_an_archive_expanding_past_the_project_cap_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "latex_project_max_bytes", 1024)
    blob = _zip({"big.tex": b"A" * 4096})
    with pytest.raises(ArchiveTooLarge):
        read_archive(blob)


def test_the_parser_never_consults_the_declared_file_size():
    """`ZipInfo.file_size` is attacker-controlled metadata: a header claiming
    1KB can front gigabytes. The guard has to count bytes it actually
    decompressed.

    Forging a header inside a still-readable archive means hand-patching
    central-directory bytes, which would test zipfile more than it tests us.
    Pinning the invariant at the source level is the honest alternative: this
    fails the moment someone adds a `file_size` shortcut, which is exactly the
    regression that would silently reopen the bomb hole."""
    import inspect

    from app.services import latex_archive

    assert "file_size" not in inspect.getsource(latex_archive)


def test_a_single_file_over_the_per_file_cap_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "latex_file_max_bytes", 16)
    blob = _zip({"big.tex": b"A" * 64})
    with pytest.raises(ArchiveTooLarge):
        read_archive(blob)


def test_too_many_entries_is_refused_before_decompression(monkeypatch):
    monkeypatch.setattr(settings, "latex_max_files", 3)
    blob = _zip({f"f{i}.tex": b"x" for i in range(10)})
    with pytest.raises(ArchiveTooLarge):
        read_archive(blob)


def test_two_entries_colliding_only_by_case_reject_the_archive():
    blob = _zip({"Figures/Fig.tex": b"a", "figures/fig.tex": b"b"})
    with pytest.raises(InvalidArchive):
        read_archive(blob)


def test_a_corrupt_archive_is_reported_as_unreadable():
    with pytest.raises(InvalidArchive):
        read_archive(b"this is not a zip file at all")


def test_directory_entries_are_skipped_not_stored():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("chapters/", b"")
        z.writestr("chapters/intro.tex", b"x")
    assert [e.path for e in read_archive(buf.getvalue())] == ["chapters/intro.tex"]


def test_mac_and_vcs_junk_is_skipped_silently():
    blob = _zip(
        {
            "main.tex": b"x",
            "__MACOSX/._main.tex": b"junk",
            ".DS_Store": b"junk",
            ".git/config": b"junk",
            "Thumbs.db": b"junk",
        }
    )
    assert [e.path for e in read_archive(blob)] == ["main.tex"]


def test_build_artifacts_are_skipped_so_they_do_not_consume_quota():
    blob = _zip(
        {
            "main.tex": b"x",
            "main.aux": b"junk",
            "main.log": b"junk",
            "main.out": b"junk",
            "main.synctex.gz": b"junk",
            "main.fls": b"junk",
            "main.fdb_latexmk": b"junk",
            "main.bbl": b"junk",
            "main.blg": b"junk",
            "main.toc": b"junk",
        }
    )
    assert [e.path for e in read_archive(blob)] == ["main.tex"]


def test_an_archive_of_only_junk_is_rejected_as_empty():
    blob = _zip({"__MACOSX/._x": b"junk", ".DS_Store": b"junk"})
    with pytest.raises(InvalidArchive):
        read_archive(blob)


def test_a_nested_zip_is_stored_as_an_opaque_binary_not_recursed():
    inner = _zip({"secret.tex": b"y"})
    blob = _zip({"main.tex": b"x", "bundle.zip": inner})
    entries = {e.path: e for e in read_archive(blob)}
    assert set(entries) == {"main.tex", "bundle.zip"}
    assert entries["bundle.zip"].is_binary is True
    assert entries["bundle.zip"].data == inner


def test_a_png_is_classified_binary_and_a_tex_file_is_not():
    blob = _zip({"main.tex": b"\\documentclass{article}", "f.png": b"\x89PNG\r\n\x1a\n\x00\x01"})
    entries = {e.path: e.is_binary for e in read_archive(blob)}
    assert entries == {"main.tex": False, "f.png": True}


def test_utf8_source_survives_as_text():
    blob = _zip({"main.tex": "café ünïcode".encode("utf-8")})
    entry = read_archive(blob)[0]
    assert entry.is_binary is False
    assert entry.data.decode("utf-8") == "café ünïcode"


def test_classify_binary_treats_a_nul_byte_as_binary_even_when_decodable():
    """A NUL-bearing file is not source, whatever it decodes to."""
    assert classify_binary(b"text\x00more") is True
    assert classify_binary(b"plain text") is False
    assert classify_binary(b"\xff\xfe\x00\x00") is True
