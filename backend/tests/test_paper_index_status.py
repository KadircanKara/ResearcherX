"""`paper_index_status`: where a paper stands in the dense and keyword indexes."""

from datetime import datetime, timedelta, timezone

from app.services.paper_index_status import Bm25Index, dense_status, keyword_status

T = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
BUILT = Bm25Index(current_version=3, built_at=T, dirty_since=None)


def test_dense_status_follows_ingest_then_chunks():
    assert dense_status("pending", 0) == "pending"
    assert dense_status("failed", 4) == "failed"
    assert dense_status("ready", 4) == "embedded"
    assert dense_status("ready", 0) == "none"


def test_postgres_keyword_search_matches_the_embeddings():
    """`tsv` is generated on the chunk row: embedded means keyword-searchable."""
    assert keyword_status("ready", 3, T, backend="postgres", index=None) == "indexed"
    assert keyword_status("ready", 0, None, backend="postgres", index=None) == "none"
    assert keyword_status("pending", 0, None, backend="postgres", index=None) == "pending"


def test_bm25s_indexed_only_when_the_build_postdates_the_chunks():
    old = T - timedelta(minutes=5)
    new = T + timedelta(minutes=5)
    assert keyword_status("ready", 3, old, backend="bm25s", index=BUILT) == "indexed"
    assert keyword_status("ready", 3, new, backend="bm25s", index=BUILT) == "queued"


def test_bm25s_with_no_published_index_is_queued():
    assert keyword_status("ready", 3, T, backend="bm25s", index=None) == "queued"
    never = Bm25Index(current_version=0, built_at=None, dirty_since=T)
    assert keyword_status("ready", 3, T, backend="bm25s", index=never) == "queued"


def test_bm25s_change_pending_after_the_chunks_is_queued():
    """Chunks written during a build carry a time before `built_at`, but the
    swap left the project dirty: they are not in the served index yet."""
    during = T - timedelta(seconds=10)
    dirty = Bm25Index(current_version=3, built_at=T, dirty_since=during)
    assert keyword_status("ready", 3, during, backend="bm25s", index=dirty) == "queued"
    earlier = T - timedelta(minutes=5)
    assert keyword_status("ready", 3, earlier, backend="bm25s", index=dirty) == "indexed"


def test_bm25s_does_not_mark_unembedded_papers():
    assert keyword_status("failed", 0, None, backend="bm25s", index=BUILT) == "failed"
    assert keyword_status("ready", 0, None, backend="bm25s", index=BUILT) == "none"


def test_summary_status_reads_the_row():
    from app.services.paper_index_status import summary_status

    assert summary_status(None, None) == "none"
    assert summary_status("ready", "llm") == "ready"
    assert summary_status("ready", "abstract") == "abstract"
    assert summary_status("pending", None) == "pending"
    assert summary_status("failed", None) == "failed"
