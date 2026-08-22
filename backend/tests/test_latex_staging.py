"""The short-lived parking spot between an import's plan and its commit."""

import pytest

from app.services.latex_archive import ArchiveEntry
from app.services.latex_staging import (
    LatexStaging,
    StagedImport,
    StagingExpired,
    StagingNotFound,
)


def staged(project="p1", user="u1", size=10) -> StagedImport:
    return StagedImport(
        project_id=project,
        user_id=user,
        entries=[ArchiveEntry(path="main.tex", data=b"x" * size, is_binary=False)],
    )


def test_a_token_round_trips():
    s = LatexStaging(max_entries=4, max_bytes=1000, ttl_s=60)
    token = s.put(staged(), now=0.0)
    assert s.take(token, "p1", "u1", now=1.0).entries[0].path == "main.tex"


def test_a_token_is_single_use():
    # Committing twice must not import twice.
    s = LatexStaging(max_entries=4, max_bytes=1000, ttl_s=60)
    token = s.put(staged(), now=0.0)
    s.take(token, "p1", "u1", now=1.0)
    with pytest.raises(StagingNotFound):
        s.take(token, "p1", "u1", now=1.0)


def test_an_expired_token_is_distinguishable_from_an_unknown_one():
    # The UI says "re-upload, that took too long" for one and "not found"
    # for the other; collapsing them makes the useful message unreachable.
    s = LatexStaging(max_entries=4, max_bytes=1000, ttl_s=60)
    token = s.put(staged(), now=0.0)
    with pytest.raises(StagingExpired):
        s.take(token, "p1", "u1", now=61.0)


def test_a_token_is_refused_to_another_user():
    s = LatexStaging(max_entries=4, max_bytes=1000, ttl_s=60)
    token = s.put(staged(user="u1"), now=0.0)
    with pytest.raises(StagingNotFound):
        s.take(token, "p1", "u2", now=1.0)


def test_a_token_is_refused_in_another_project():
    s = LatexStaging(max_entries=4, max_bytes=1000, ttl_s=60)
    token = s.put(staged(project="p1"), now=0.0)
    with pytest.raises(StagingNotFound):
        s.take(token, "p2", "u1", now=1.0)


def test_the_oldest_entry_is_evicted_past_the_count_bound():
    s = LatexStaging(max_entries=2, max_bytes=10_000, ttl_s=60)
    first = s.put(staged(), now=0.0)
    s.put(staged(), now=1.0)
    s.put(staged(), now=2.0)
    with pytest.raises(StagingNotFound):
        s.take(first, "p1", "u1", now=3.0)


def test_entries_are_evicted_until_the_byte_bound_holds():
    s = LatexStaging(max_entries=10, max_bytes=100, ttl_s=60)
    first = s.put(staged(size=60), now=0.0)
    s.put(staged(size=60), now=1.0)
    with pytest.raises(StagingNotFound):
        s.take(first, "p1", "u1", now=2.0)
