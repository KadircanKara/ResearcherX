"""PaperMeta coercion and LLM metadata extraction.

The LLM call itself is faked — the suite deliberately cannot reach a model.
Real extraction accuracy is measured by evals/metadata/run_eval.py.
"""

from unittest.mock import AsyncMock, patch

from app.services.title_extraction_service import PaperMeta, extract_metadata_from_text


def test_authors_accepts_a_list():
    m = PaperMeta(authors=["Kadircan Kara", "Evşen Yanmaz"])
    assert m.authors == ["Kadircan Kara", "Evşen Yanmaz"]


def test_authors_coerced_from_a_comma_string():
    """Some responses return a string where the schema asked for a list."""
    m = PaperMeta(authors="Kadircan Kara, İslam Güven, Evşen Yanmaz")
    assert m.authors == ["Kadircan Kara", "İslam Güven", "Evşen Yanmaz"]


def test_authors_drops_junk_and_collapses_whitespace():
    m = PaperMeta(authors=["  Ada   Lovelace ", "", None, 42, "   "])
    assert m.authors == ["Ada Lovelace"]


def test_authors_none_becomes_empty_list():
    assert PaperMeta(authors=None).authors == []


def test_authors_capped():
    m = PaperMeta(authors=[f"Author {i}" for i in range(200)])
    assert len(m.authors) == 50


def test_year_parsed_from_string():
    assert PaperMeta(year="2024").year == 2024


def test_year_parsed_from_date_string():
    assert PaperMeta(year="2024-05-01").year == 2024


def test_year_rejects_nonsense():
    """An out-of-range or unparseable year is absence, not a stored lie."""
    assert PaperMeta(year="not a year").year is None
    assert PaperMeta(year=99).year is None
    assert PaperMeta(year=3000).year is None
    assert PaperMeta(year=None).year is None


def test_venue_blank_becomes_none():
    assert PaperMeta(venue="   ").venue is None
    assert PaperMeta(venue=None).venue is None


def test_venue_whitespace_collapsed():
    assert PaperMeta(venue="IEEE   ICRA\n2024").venue == "IEEE ICRA 2024"


def test_authors_have_pdf_mangled_diacritics_repaired():
    """The corpus really stores these forms; users must never see them."""
    m = PaperMeta(authors=["Evs¸en Yanmaz", "˙Islam G¨uven"])
    assert m.authors == ["Evşen Yanmaz", "İslam Güven"]


def test_venue_diacritics_repaired():
    assert PaperMeta(venue="Conf´erence Internationale").venue == "Conférence Internationale"


def test_defaults_are_absent():
    m = PaperMeta()
    assert m.authors == []
    assert m.year is None
    assert m.venue is None


async def test_extract_metadata_from_text_returns_parsed_meta():
    parsed = PaperMeta(title="T", authors=["A B"], year=2024, venue="ICRA")
    with patch(
        "app.services.title_extraction_service.parse_structured",
        new=AsyncMock(return_value=parsed),
    ) as fake:
        meta = await extract_metadata_from_text("Some first-page text")
    assert meta.authors == ["A B"]
    assert meta.year == 2024
    assert fake.await_count == 1


async def test_extract_metadata_from_text_empty_input_skips_the_llm():
    with patch(
        "app.services.title_extraction_service.parse_structured",
        new=AsyncMock(side_effect=AssertionError("must not be called")),
    ):
        meta = await extract_metadata_from_text("   ")
    assert meta.authors == []
    assert meta.title is None


async def test_extract_metadata_from_text_fails_open():
    """A flaky extractor degrades to absence; it never breaks ingest."""
    with patch(
        "app.services.title_extraction_service.parse_structured",
        new=AsyncMock(side_effect=ValueError("llm down")),
    ):
        meta = await extract_metadata_from_text("Some first-page text")
    assert meta.authors == []
    assert meta.year is None
    assert meta.venue is None
