"""reindex_papers picks the best tier per paper and never touches the
network unless told to."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts import reindex_papers as rp


def _row(**kw):
    base = dict(
        id="p",
        title="T",
        extracted_text=None,
        extracted_pages=None,
        outline=None,
        pdf_url=None,
        abstract=None,
        body=None,
        has_file=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_tier_prefers_stored_pages_then_pdf_then_markdown_then_manual():
    assert rp.tier_for(_row(extracted_pages=[{"page": 1, "text": "x"}])) == "pages"
    assert rp.tier_for(_row(pdf_url="https://arxiv.org/abs/1")) == "pdf"
    assert rp.tier_for(_row(has_file=True)) == "pdf"
    assert rp.tier_for(_row(extracted_text="## A\n\nx")) == "markdown"
    assert rp.tier_for(_row(body="hand-entered")) == "manual"


async def test_stored_pages_replay_without_network():
    row = _row(extracted_pages=[{"page": 2, "text": "## II. MODEL\n\nswarm text"}], outline=[])
    with patch.object(rp, "fetch_pdf", new=AsyncMock(side_effect=AssertionError("network"))):
        records, tier, persist = await rp.records_for(row, allow_fetch=True)
    assert tier == "pages" and persist is None
    assert records and records[0].section == ("II. MODEL",) and records[0].page == 2


async def test_pdf_tier_requires_fetch_and_falls_back_to_markdown_without_it():
    row = _row(pdf_url="https://arxiv.org/abs/1", extracted_text="## A\n\nx")
    records, tier, _ = await rp.records_for(row, allow_fetch=False)
    assert tier == "markdown" and records[0].section == ("A",)


async def test_a_failed_fetch_degrades_to_markdown_and_persists_nothing():
    row = _row(pdf_url="https://arxiv.org/abs/1", extracted_text="## A\n\nx")
    with patch.object(rp, "fetch_pdf", new=AsyncMock(side_effect=RuntimeError("403"))):
        records, tier, persist = await rp.records_for(row, allow_fetch=True)
    assert tier == "markdown" and persist is None and records


def test_arxiv_abs_url_is_rewritten_to_the_pdf_url():
    assert rp._pdf_url("https://arxiv.org/abs/2009.04234") == "https://arxiv.org/pdf/2009.04234"
    assert rp._pdf_url("https://example.org/x.pdf") == "https://example.org/x.pdf"
