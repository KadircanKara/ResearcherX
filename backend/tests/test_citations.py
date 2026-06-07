"""Source catalog: dedup, stable numbering, summary merge (citation mapping)."""

from app.schemas.research import SearchFinding, SourceSummary
from app.services.research_service import _norm_url, build_source_catalog


def finding(query: str, sources: list[tuple[str, str]]) -> SearchFinding:
    return SearchFinding(
        query=query,
        summary=f"summary for {query}",
        sources=[SourceSummary(url=u, summary=s) for u, s in sources],
    )


def test_norm_url_collapses_scheme_www_and_trailing_slash():
    a = _norm_url("https://www.Example.com/path/")
    b = _norm_url("http://example.com/path")
    assert a == b == "example.com/path"


def test_catalog_numbers_in_first_seen_order():
    findings = [
        finding("q1", [("https://a.com", "about a"), ("https://b.com", "about b")]),
        finding("q2", [("https://c.com", "about c")]),
    ]
    catalog = build_source_catalog(findings)
    assert [(c.n, c.url) for c in catalog] == [
        (1, "https://a.com"),
        (2, "https://b.com"),
        (3, "https://c.com"),
    ]


def test_catalog_dedupes_same_url_across_findings():
    findings = [
        finding("q1", [("https://a.com", "from q1")]),
        finding("q2", [("https://www.a.com/", "from q2"), ("https://d.com", "about d")]),
    ]
    catalog = build_source_catalog(findings)
    # a.com (two spellings) collapses to one entry; numbering stays contiguous.
    assert [(c.n, c.url) for c in catalog] == [(1, "https://a.com"), (2, "https://d.com")]
    # the duplicate's summary is merged in, not dropped.
    assert "from q1" in catalog[0].summary
    assert "from q2" in catalog[0].summary


def test_catalog_skips_blank_urls():
    catalog = build_source_catalog([finding("q1", [("   ", "noise"), ("https://a.com", "real")])])
    assert [c.url for c in catalog] == ["https://a.com"]


def test_catalog_does_not_duplicate_identical_summary():
    findings = [
        finding("q1", [("https://a.com", "same text")]),
        finding("q2", [("https://a.com", "same text")]),
    ]
    catalog = build_source_catalog(findings)
    assert catalog[0].summary == "same text"  # merged once, not doubled
