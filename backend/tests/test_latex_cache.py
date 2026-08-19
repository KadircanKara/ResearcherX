"""The PDF is derived, not stored. This cache is in-process memory, which is
valid for the same reason the event bus and the rate limiter are: uvicorn runs
a single worker by design."""

from app.services.latex_cache import CachedBuild, LatexCache, source_hash


def _build(pdf: bytes = b"%PDF", source: str = "src") -> CachedBuild:
    return CachedBuild(source=source, pdf=pdf, synctex_gz=b"gz", log="")


def test_the_hash_covers_the_engine_not_just_the_source():
    """The same source compiled by xelatex is a different PDF. Keying on
    source alone would serve the wrong artifact after an engine switch."""
    assert source_hash("x", "pdflatex") != source_hash("x", "xelatex")
    assert source_hash("x", "pdflatex") == source_hash("x", "pdflatex")


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
