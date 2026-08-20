"""The archive parser is the boundary where an uploaded file stops being
hostile. One test per control in the spec's threat table, each building its
own crafted archive so the malicious shape is visible in the test."""

import io
import struct
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


def test_a_relative_traversal_entry_rejects_the_archive_even_when_a_sibling_would_mask_it():
    """A prior bug computed the shared-wrapper-directory strip on raw,
    unvalidated names: `{"../a.tex", "../b.tex"}` both shared the `../`
    "prefix" and were silently stripped down to `a.tex`/`b.tex` instead of
    being rejected. Validation must run before stripping is even considered."""
    blob = _zip({"../a.tex": b"x", "../b.tex": b"y"})
    with pytest.raises(InvalidArchive):
        read_archive(blob)


def test_an_absolute_entry_rejects_the_archive_even_when_a_sibling_would_mask_it():
    blob = _zip({"/etc/passwd": b"x", "/etc/shadow": b"y"})
    with pytest.raises(InvalidArchive):
        read_archive(blob)


def test_a_drive_letter_entry_rejects_the_archive_even_when_a_sibling_would_mask_it():
    blob = _zip({"C:/a.tex": b"x", "C:/b.tex": b"y"})
    with pytest.raises(InvalidArchive):
        read_archive(blob)


def test_git_metadata_sharing_a_prefix_is_filtered_not_treated_as_a_shared_wrapper():
    """A real project file alongside the `.git/` entries: this pins that
    `.git/*` is filtered as junk specifically, not merely that an
    all-junk archive is rejected as empty (`.git/config` and `.git/HEAD`
    alone, with no other file, passed the old version of this test for the
    wrong reason -- `test_an_archive_of_only_junk_is_rejected_as_empty`
    already covers that case)."""
    blob = _zip({"main.tex": b"x", ".git/config": b"y", ".git/HEAD": b"z"})
    assert [e.path for e in read_archive(blob)] == ["main.tex"]


def test_a_single_surviving_top_level_directory_still_strips_when_alone():
    """Deliberate behaviour, previously held only by the ruling in
    `_common_prefix`'s docstring: a lone top-level directory strips even
    though there is nothing else at the root to compare it against."""
    blob = _zip({"chapters/intro.tex": b"x"})
    assert [e.path for e in read_archive(blob)] == ["intro.tex"]


def test_a_macosx_sibling_does_not_prevent_a_real_wrapper_from_being_stripped():
    """macOS Archive Utility emits a `__MACOSX/` sibling on essentially every
    zip a Mac user produces. The junk member must be filtered out BEFORE the
    shared-prefix decision, or it silently defeats stripping for everyone."""
    blob = _zip(
        {
            "proj/main.tex": b"x",
            "proj/a.tex": b"y",
            "__MACOSX/._proj": b"junk",
        }
    )
    assert sorted(e.path for e in read_archive(blob)) == ["a.tex", "main.tex"]


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


def test_a_symlink_claiming_to_be_windows_created_is_still_rejected():
    """`create_system` is a byte the uploader fully controls. The symlink
    check must not be gated on it -- an attacker can simply claim a creator
    system with no mode bits to read and sail through."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("main.tex", b"x")
        info = zipfile.ZipInfo("link.tex")
        info.create_system = 0  # claims MS-DOS/Windows, not Unix
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


def test_a_highly_compressible_bomb_is_refused_while_decompressing(monkeypatch):
    """A zip bomb: a small stored archive that expands to far more than the
    project cap. The guard has to count bytes as they come out of the
    decompressor, not trust anything read from a header."""
    monkeypatch.setattr(settings, "latex_project_max_bytes", 1024)
    blob = _zip({"bomb.tex": b"\x00" * 4_000_000})
    assert len(blob) < 100_000  # the archive is small; the expansion is not
    with pytest.raises(ArchiveTooLarge):
        read_archive(blob)


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


def test_a_corrupted_compressed_payload_is_reported_as_invalid_not_a_500():
    """zipfile/zlib validate and decompress lazily inside open()/read(), well
    outside the up-front `ZipFile(...)` parse. A payload with intact headers
    but scrambled deflate bytes must still come back as InvalidArchive."""
    blob = bytearray(_zip({"main.tex": b"hello world " * 50}))
    idx = blob.find(b"PK\x03\x04")
    _sig, _ver, _flag, _method, _mtime, _mdate, _crc, csize, _usize, nlen, elen = struct.unpack(
        "<4sHHHHHIIIHH", bytes(blob[idx : idx + 30])
    )
    data_start = idx + 30 + nlen + elen
    flip_at = data_start + csize // 2
    blob[flip_at] ^= 0xFF
    with pytest.raises(InvalidArchive):
        read_archive(bytes(blob))


def test_an_entry_using_an_unsupported_compression_method_is_reported_as_invalid_not_a_500():
    """Method 99 is WinZip's marker for AES-encrypted data; `zipfile` raises
    `NotImplementedError` for any method it doesn't implement. Must not
    escape as a 500."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("main.tex", b"hello")
    blob = bytearray(buf.getvalue())
    idx_local = blob.find(b"PK\x03\x04")
    struct.pack_into("<H", blob, idx_local + 8, 99)
    idx_central = blob.find(b"PK\x01\x02")
    struct.pack_into("<H", blob, idx_central + 10, 99)
    with pytest.raises(InvalidArchive):
        read_archive(bytes(blob))


def test_directory_entries_are_skipped_not_stored():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("chapters/", b"")
        z.writestr("chapters/intro.tex", b"x")
        z.writestr("main.tex", b"y")
    assert sorted(e.path for e in read_archive(buf.getvalue())) == [
        "chapters/intro.tex",
        "main.tex",
    ]


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
