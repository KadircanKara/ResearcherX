"""Crossref record parsing. No network — the responses are recorded literals."""

from unittest.mock import AsyncMock, patch

from app.services.title_extraction_service import (
    extract_title_from_doi,
    parse_crossref_message,
)

FULL_MESSAGE = {
    "title": ["Joint Optimization of Connectivity, Coverage, and Revisit Time"],
    "author": [
        {"given": "Kadircan", "family": "Kara"},
        {"given": "Evşen", "family": "Yanmaz"},
    ],
    "published": {"date-parts": [[2024, 5, 13]]},
    "container-title": ["IEEE Transactions on Robotics"],
}


def test_parses_every_field():
    meta = parse_crossref_message(FULL_MESSAGE)
    assert meta.title == "Joint Optimization of Connectivity, Coverage, and Revisit Time"
    assert meta.authors == ["Kadircan Kara", "Evşen Yanmaz"]
    assert meta.year == 2024
    assert meta.venue == "IEEE Transactions on Robotics"


def test_joins_given_and_family_into_display_order():
    """Crossref splits names; both extraction paths must produce the same shape."""
    meta = parse_crossref_message({"author": [{"given": "Ada", "family": "Lovelace"}]})
    assert meta.authors == ["Ada Lovelace"]


def test_uses_org_name_when_there_is_no_person_name():
    meta = parse_crossref_message({"author": [{"name": "World Health Organization"}]})
    assert meta.authors == ["World Health Organization"]


def test_missing_author_key_yields_empty_list_not_an_error():
    meta = parse_crossref_message({"title": ["A Paper"]})
    assert meta.authors == []
    assert meta.title == "A Paper"


def test_falls_back_through_the_date_keys():
    meta = parse_crossref_message({"issued": {"date-parts": [[2019, 1, 1]]}})
    assert meta.year == 2019


def test_survives_a_null_date_part():
    """Crossref really does return [[None]] for records with no date."""
    meta = parse_crossref_message({"published": {"date-parts": [[None]]}})
    assert meta.year is None


def test_survives_null_valued_keys():
    meta = parse_crossref_message(
        {"title": None, "author": None, "published": None, "container-title": None}
    )
    assert meta.title is None
    assert meta.authors == []
    assert meta.year is None
    assert meta.venue is None


def test_empty_message_is_all_absent():
    meta = parse_crossref_message({})
    assert meta.title is None
    assert meta.authors == []
    assert meta.year is None
    assert meta.venue is None


async def test_extract_title_from_doi_still_returns_only_the_title():
    """suggest_paper_title_from_url in app/api/v1/projects.py depends on this."""
    with patch(
        "app.services.title_extraction_service.fetch_crossref_meta",
        new=AsyncMock(return_value=parse_crossref_message(FULL_MESSAGE)),
    ):
        title = await extract_title_from_doi("10.1109/TRO.2024.1234")
    assert title == "Joint Optimization of Connectivity, Coverage, and Revisit Time"


async def test_extract_title_from_doi_returns_none_when_crossref_has_nothing():
    with patch(
        "app.services.title_extraction_service.fetch_crossref_meta",
        new=AsyncMock(return_value=None),
    ):
        assert await extract_title_from_doi("10.9999/nope") is None
