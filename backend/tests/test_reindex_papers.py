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


def test_tier_precedence_holds_when_a_lower_tier_is_also_available():
    """The single-condition rows above pass regardless of branch order —
    this pins the ORDER itself: a paper that qualifies for more than one
    tier must resolve to the highest."""
    assert (
        rp.tier_for(
            _row(extracted_pages=[{"page": 1, "text": "x"}], pdf_url="https://arxiv.org/abs/1")
        )
        == "pages"
    )
    assert rp.tier_for(_row(pdf_url="https://arxiv.org/abs/1", extracted_text="## A\n\nx")) == "pdf"
    assert rp.tier_for(_row(extracted_text="## A\n\nx", body="hand-entered")) == "markdown"


async def test_stored_pages_replay_without_network():
    row = _row(extracted_pages=[{"page": 2, "text": "## II. MODEL\n\nswarm text"}], outline=[])
    with patch.object(rp, "fetch_pdf", new=AsyncMock(side_effect=AssertionError("network"))):
        records, tier, persist, fetch_attempted = await rp.records_for(row, allow_fetch=True)
    assert tier == "pages" and persist is None and fetch_attempted is False
    assert records and records[0].section == ("II. MODEL",) and records[0].page == 2


async def test_pdf_tier_requires_fetch_and_falls_back_to_markdown_without_it():
    row = _row(pdf_url="https://arxiv.org/abs/1", extracted_text="## A\n\nx")
    records, tier, _, fetch_attempted = await rp.records_for(row, allow_fetch=False)
    assert tier == "markdown" and records[0].section == ("A",)
    assert fetch_attempted is False  # allow_fetch=False must never touch the network


async def test_a_failed_fetch_degrades_to_markdown_and_persists_nothing():
    row = _row(pdf_url="https://arxiv.org/abs/1", extracted_text="## A\n\nx")
    with patch.object(rp, "fetch_pdf", new=AsyncMock(side_effect=RuntimeError("403"))):
        records, tier, persist, fetch_attempted = await rp.records_for(row, allow_fetch=True)
    assert tier == "markdown" and persist is None and records
    # The regression this pins: the fetch DID hit the network and failed —
    # the resolved tier says "markdown" but the caller must still pace as
    # if a fetch happened, or a run under sustained failure hammers the
    # remote host at full speed instead of backing off.
    assert fetch_attempted is True


async def test_a_successful_fetch_reports_it_was_attempted():
    row = _row(pdf_url="https://arxiv.org/abs/1")
    pdf_bytes = b"%PDF-1.4 fake"
    with patch.object(rp, "fetch_pdf", new=AsyncMock(return_value=(pdf_bytes, "https://x"))):
        with patch.object(rp, "extract_document") as mock_extract:
            mock_extract.return_value = SimpleNamespace(
                pages=[], outline=[], markdown="", has_outline=False
            )
            _records, tier, _persist, fetch_attempted = await rp.records_for(row, allow_fetch=True)
    assert tier == "pdf" and fetch_attempted is True


async def test_a_retained_blob_never_counts_as_a_network_fetch():
    """`has_file` papers read the blob from the database — no network at
    all — so pacing must not fire for them even under `--fetch`."""
    row = _row(has_file=True)
    with patch.object(rp, "_blob_bytes", new=AsyncMock(return_value=b"%PDF-1.4 fake")):
        with patch.object(rp, "fetch_pdf", new=AsyncMock(side_effect=AssertionError("network"))):
            with patch.object(rp, "extract_document") as mock_extract:
                mock_extract.return_value = SimpleNamespace(
                    pages=[], outline=[], markdown="", has_outline=False
                )
                _records, tier, _persist, fetch_attempted = await rp.records_for(
                    row, allow_fetch=True
                )
    assert tier == "pdf" and fetch_attempted is False


def test_arxiv_abs_url_is_rewritten_to_the_pdf_url():
    assert rp._pdf_url("https://arxiv.org/abs/2009.04234") == "https://arxiv.org/pdf/2009.04234"
    assert rp._pdf_url("https://example.org/x.pdf") == "https://example.org/x.pdf"


# ── the no-content skip guard ──────────────────────────────────────────────


async def test_a_paper_with_nothing_to_index_is_skipped_not_emptied():
    """pdf_url set (tier `pdf`) but fetch disabled, no stored markdown, no
    abstract, no body: nothing to chunk anywhere. `index_manual`/
    `index_chunks` both delete-then-write, so calling either here would
    wipe this paper's existing chunk rows and leave zero. `_reindex_one`
    must refuse to write at all."""
    row = _row(pdf_url="https://arxiv.org/abs/1")
    result = await rp._reindex_one(row, allow_fetch=False)
    assert result is None


async def test_a_genuinely_manual_paper_with_content_is_not_skipped():
    """The skip guard must not swallow the normal manual-tier path."""
    row = _row(abstract="an abstract", body="some body text")
    records, tier, persist, fetch_attempted = await rp.records_for(row, allow_fetch=False)
    assert tier == "manual" and records == [] and persist is None and fetch_attempted is False
    assert rp._has_manual_content(row) is True
