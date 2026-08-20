"""The PDF is derived, not stored. This cache is in-process memory, which is
valid for the same reason the event bus and the rate limiter are: uvicorn runs
a single worker by design."""

from app.services.latex_cache import CachedBuild, LatexCache


def _build(
    pdf: bytes = b"%PDF", root: str | None = "/tmp/rx-latex-abc", main_path: str = "master.tex"
) -> CachedBuild:
    return CachedBuild(pdf=pdf, synctex_gz=b"gz", log="", root=root, main_path=main_path)


def test_a_put_can_be_read_back():
    cache = LatexCache(max_entries=4, max_bytes=10_000)
    cache.put("k", _build())

    assert cache.get("k").pdf == b"%PDF"


def test_a_miss_returns_none():
    assert LatexCache(max_entries=4, max_bytes=10_000).get("absent") is None


def test_the_entry_count_is_bounded_and_evicts_oldest_first():
    cache = LatexCache(max_entries=2, max_bytes=10_000)
    cache.put("a", _build())
    cache.put("b", _build())
    cache.put("c", _build())

    assert cache.get("a") is None
    assert cache.get("b") is not None
    assert cache.get("c") is not None


def test_reading_an_entry_makes_it_the_most_recent():
    cache = LatexCache(max_entries=2, max_bytes=10_000)
    cache.put("a", _build())
    cache.put("b", _build())
    cache.get("a")  # a is now newest
    cache.put("c", _build())

    assert cache.get("a") is not None
    assert cache.get("b") is None


def test_the_byte_budget_evicts_even_under_the_entry_cap():
    """A PDF is comfortably over a megabyte and this process also holds the
    event bus, so entries alone cannot bound memory."""
    cache = LatexCache(max_entries=10, max_bytes=100)
    cache.put("a", _build(pdf=b"x" * 80))
    cache.put("b", _build(pdf=b"y" * 80))

    assert cache.get("a") is None
    assert cache.get("b") is not None


def test_latest_for_a_document_survives_a_failed_recompile():
    """The last good PDF must stay on screen when the next compile fails."""
    cache = LatexCache(max_entries=4, max_bytes=10_000)
    cache.put("k1", _build(pdf=b"good"), document_id="doc1")

    assert cache.latest_for("doc1").pdf == b"good"


def test_latest_for_an_unknown_document_is_none():
    assert LatexCache(max_entries=4, max_bytes=10_000).latest_for("nobody") is None


def test_reading_the_fallback_build_protects_it_from_eviction():
    """The last-good-PDF path must survive the pressure it exists to survive.
    Without promotion on read, another document's traffic evicts the build a
    user is actively falling back on."""
    cache = LatexCache(max_entries=2, max_bytes=10_000)
    cache.put("a", _build(), document_id="doc1")
    cache.put("b", _build())
    cache.latest_for("doc1")  # doc1's build is now newest
    cache.put("c", _build())

    assert cache.latest_for("doc1") is not None


def test_evicting_a_build_forgets_the_documents_pointing_at_it():
    """`_latest` must not grow for the life of the process: an entry that has
    been evicted can never be served again, so its pointers are dead weight."""
    cache = LatexCache(max_entries=1, max_bytes=10_000)
    cache.put("a", _build(), document_id="doc1")
    cache.put("b", _build(), document_id="doc2")

    assert cache.latest_for("doc1") is None
    assert cache._latest == {"doc2": "b"}


def test_a_cached_build_no_longer_carries_a_source_field():
    """`source` was dropped: both sync directions answer from the PDF and the
    map alone (see latex_compiler.synctex_forward/reverse), so keeping the
    source text here was dead weight against `size`'s eviction accounting.
    Fails if `source` is ever reintroduced as a field -- `hasattr` would then
    be True and the assertion would fail."""
    build = _build()

    assert not hasattr(build, "source")


def test_a_build_round_trips_its_root_and_main_path():
    """The fields a reverse-sync query now needs alongside the PDF and the
    map. Fails if `put`/`get` ever stopped preserving them verbatim, or if
    the constructor silently dropped/renamed either."""
    cache = LatexCache(max_entries=4, max_bytes=10_000)
    cache.put("k", _build(root="/tmp/rx-latex-xyz", main_path="src/paper.tex"))

    build = cache.get("k")
    assert build.root == "/tmp/rx-latex-xyz"
    assert build.main_path == "src/paper.tex"


def test_a_build_with_no_extraction_root_still_reports_its_size():
    """A degraded/no-tree build (root=None) must still be evictable on byte
    accounting -- `size` must not choke on root being absent. Fails if `size`
    were ever wired to depend on `root` rather than staying pdf + synctex."""
    build = _build(pdf=b"x" * 50, root=None)

    assert build.size == 50 + len(b"gz")
