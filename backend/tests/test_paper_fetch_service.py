# backend/tests/test_paper_fetch_service.py
"""Tests for paper_fetch_service: DOI extraction + PDF fetch with OA fallback."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.paper_fetch_service import (
    PaywallError,
    _extract_doi,
    fetch_pdf,
)


def test_extract_doi_from_ieee_url():
    url = "https://ieeexplore.ieee.org/document/10.1109/TPAMI.2023.1234567"
    assert _extract_doi(url) == "10.1109/TPAMI.2023.1234567"


def test_extract_doi_from_doi_org():
    assert _extract_doi("https://doi.org/10.1234/something.here") == "10.1234/something.here"


def test_extract_doi_none_when_missing():
    assert _extract_doi("https://arxiv.org/abs/2301.00001") is None


@pytest.mark.asyncio
async def test_fetch_pdf_direct_success():
    pdf_bytes = b"%PDF-direct"
    with patch(
        "app.services.paper_fetch_service._get_pdf_bytes",
        new=AsyncMock(return_value=pdf_bytes),
    ):
        result = await fetch_pdf("https://example.com/open.pdf")
    assert result == pdf_bytes


@pytest.mark.asyncio
async def test_fetch_pdf_paywalled_fallback_via_oa():
    oa_pdf = b"%PDF-oa"
    call_results = [None, oa_pdf]

    async def mock_get(url: str) -> bytes | None:
        return call_results.pop(0)

    with (
        patch("app.services.paper_fetch_service._get_pdf_bytes", side_effect=mock_get),
        patch(
            "app.services.paper_fetch_service._resolve_oa_url",
            new=AsyncMock(return_value="https://oa.example.com/paper.pdf"),
        ),
    ):
        result = await fetch_pdf("https://doi.org/10.1234/test")
    assert result == oa_pdf


@pytest.mark.asyncio
async def test_fetch_pdf_raises_paywall_error_when_all_fail():
    with (
        patch("app.services.paper_fetch_service._get_pdf_bytes", new=AsyncMock(return_value=None)),
        patch(
            "app.services.paper_fetch_service._resolve_oa_url",
            new=AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(PaywallError):
            await fetch_pdf("https://doi.org/10.1234/blocked")


@pytest.mark.asyncio
async def test_fetch_pdf_raises_paywall_when_no_doi():
    with patch("app.services.paper_fetch_service._get_pdf_bytes", new=AsyncMock(return_value=None)):
        with pytest.raises(PaywallError):
            await fetch_pdf("https://no-doi-here.com/paper")
