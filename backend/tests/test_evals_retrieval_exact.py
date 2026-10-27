"""The `--exact` arm of the retrieval harness: HNSW against exact search.

The pure comparison lives in `evals.retrieval.per_kind`; the SQL wiring is
`run_eval`'s, which imports production's dense query builders and runner.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from evals.retrieval.golden_set import Case
from evals.retrieval.metrics import Scored
from evals.retrieval.per_kind import (
    differing_id_sets,
    exact_vs_hnsw_by_kind,
    hnsw_misses,
    latency_summary,
)

# --- pure ---------------------------------------------------------------------


def test_exact_report_flags_a_hnsw_only_miss():
    exact = [["a", "b"], ["c", "ans1"], ["d"], ["e"]]
    hnsw = [["a", "b"], ["c", "x"], ["d"], ["e", "ans3"]]
    answers = [{"a"}, {"ans1"}, {"zzz"}, {"ans3"}]
    # Case 0: both hold it. Case 1: only exact holds it -> the miss.
    # Case 2: neither holds it, which is not HNSW's fault. Case 3: only
    # HNSW holds it (exact's gate cut it), never a miss.
    assert hnsw_misses(exact, hnsw, answers) == [1]


def test_hnsw_misses_refuses_misaligned_lists():
    with pytest.raises(ValueError):
        hnsw_misses([["a"]], [["a"], ["b"]], [{"a"}])
    with pytest.raises(ValueError):
        hnsw_misses([["a"]], [["a"]], [{"a"}, {"b"}])


def test_differing_id_sets_ignores_order_and_counts_cases():
    exact = [["a", "b"], ["c", "d"], ["e"]]
    hnsw = [["b", "a"], ["c", "x"], ["e", "f"]]
    assert differing_id_sets(exact, hnsw) == [1, 2]


def _case(cid: str, kind: str) -> Case:
    return Case(
        id=cid,
        kind=kind,
        question="q",
        paper_title_contains="Paper",
        expect_substrings=("needle",),
    )


def _chunk(text: str) -> Scored:
    return Scored(paper_id="p", paper_title="Paper", chunk_text=text, distance=None)


def test_exact_vs_hnsw_by_kind_reports_recall_at_k_per_kind():
    cases = [_case("a", "paraphrase"), _case("b", "paraphrase"), _case("c", "keyword")]
    hit, miss = _chunk("a needle"), _chunk("nothing")
    hnsw = [[hit], [miss], [hit]]
    exact = [[hit], [hit], [hit]]
    rows = exact_vs_hnsw_by_kind(cases, hnsw, exact, k=60)
    assert [(r.kind, r.n, r.hnsw, r.exact) for r in rows] == [
        ("paraphrase", 2, 0.5, 1.0),
        ("keyword", 1, 1.0, 1.0),
    ]


def test_latency_summary_is_mean_p50_p95():
    mean, p50, p95 = latency_summary([float(x) for x in range(1, 101)])
    assert mean == pytest.approx(50.5)
    assert p50 == pytest.approx(50.5)
    assert p95 == pytest.approx(95.05)
    with pytest.raises(ValueError):
        latency_summary([])


# --- SQL wiring -----------------------------------------------------------------


def _fake_db(n_rows: int = 3) -> MagicMock:
    rows = [
        MagicMock(
            id=f"c{i}",
            paper_id="p1",
            chunk_index=i,
            text=f"chunk {i}",
            section=[],
            page=None,
            distance=0.1 + i * 0.01,
            d_rank=i + 1,
            s_rank=None,
            # Never short, so the HNSW path does not fall back to exact.
            dense_raw_n=10_000,
            dense_scoped_n=10_000,
        )
        for i in range(n_rows)
    ]
    result = MagicMock()
    result.fetchall.return_value = rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.rollback = AsyncMock()
    return db


def _svc() -> MagicMock:
    svc = MagicMock()
    svc.embed = AsyncMock(return_value=[0.0] * 768)
    return svc


def _statements(db: MagicMock) -> list[str]:
    return [str(c.args[0]) for c in db.execute.await_args_list]


def _project():
    from evals.retrieval import run_eval

    return run_eval._Project(project_id="proj-1", ready_ids=("p1",), titles={"p1": "Paper"})


async def test_exact_flag_disables_index_scan():
    from app.services.chat_service import _hnsw_settings_sql
    from evals.retrieval import run_eval

    case = _case("a", "paraphrase")

    db = _fake_db()
    await run_eval._hybrid_chunks_for(
        db, _svc(), case, _project(), threshold=settings.similarity_threshold, exact=True
    )
    statements = _statements(db)
    assert statements[0] == "SET LOCAL enable_indexscan = off"
    assert not any("hnsw." in s for s in statements)
    # The exact SHAPE too: the scope filter sits inside the scan.
    assert "AND c.paper_id IN (SELECT paper_id FROM scope)" in statements[1]

    # Without the flag the global arm runs production's HNSW path.
    db = _fake_db()
    await run_eval._hybrid_chunks_for(
        db, _svc(), case, _project(), threshold=settings.similarity_threshold
    )
    statements = _statements(db)
    assert statements[:4] == _hnsw_settings_sql(settings.hybrid_dense_pool)
    assert not any("enable_indexscan = off" in s for s in statements)


async def test_each_dense_query_starts_a_fresh_transaction():
    """SET LOCAL lasts until the transaction ends. The harness runs every
    case on one session, so an exact query's `enable_indexscan = off` would
    otherwise silently turn the next HNSW query exact too."""
    from evals.retrieval import run_eval

    db = _fake_db()
    await run_eval._hybrid_chunks_for(
        db, _svc(), _case("a", "paraphrase"), _project(), threshold=0.75
    )
    assert db.rollback.await_count == 1


async def test_a_paper_scope_is_always_exact():
    """An explicit scope takes production's exact scan, flag or not."""
    from evals.retrieval import run_eval

    db = _fake_db()
    await run_eval._hybrid_chunks_for(
        db,
        _svc(),
        _case("a", "paraphrase"),
        _project(),
        threshold=settings.intra_paper_ceiling,
        paper_id="p1",
    )
    statements = _statements(db)
    assert statements[0] == "SET LOCAL enable_indexscan = off"
    assert '["p1"]' == db.execute.await_args_list[1].args[1]["ids"]


async def test_the_full_corpus_dense_query_never_uses_the_index():
    """`_SQL` has no LIMIT: it is the whole-corpus truth the golden-set
    status check reads. An index scan could only cut it short."""
    from evals.retrieval import run_eval

    db = _fake_db()
    db.execute.return_value.fetchall.return_value = []
    await run_eval._chunks_for(db, _svc(), _case("a", "paraphrase"), "proj-1")
    statements = _statements(db)
    assert statements[0] == "SET LOCAL enable_indexscan = off"
    assert db.rollback.await_count == 1


async def test_dense_ids_for_times_and_reports_the_fallback():
    from evals.retrieval import run_eval

    db = _fake_db()
    for row in db.execute.return_value.fetchall.return_value:
        row.dense_raw_n = 3  # short scan -> production re-runs exact
    with patch.object(settings, "hybrid_dense_pool", 200):
        probe = await run_eval._dense_probe(db, "[0]", _project(), exact=False)
    assert probe.ids == ["c0", "c1", "c2"]
    assert probe.fell_back is True
    assert probe.ms >= 0.0
    assert any("enable_indexscan = off" in s for s in _statements(db))


async def test_mention_eval_mirror_uses_productions_exact_shape():
    from evals.retrieval import mention_eval

    db = _fake_db()
    with patch.object(settings, "hybrid_retrieval", True):
        await mention_eval._fetch_scope(
            db,
            qvec="[0]",
            qtext="q",
            paper_ids=["p1", "p2"],
            threshold=0.75,
            pool=60,
            project_id="proj-1",
            titles={"p1": "A", "p2": "B"},
        )
    statements = _statements(db)
    assert statements[0] == "SET LOCAL enable_indexscan = off"
    assert "AND c.paper_id IN (SELECT paper_id FROM scope)" in statements[1]
    assert "websearch_to_tsquery('english', :qtext)" in statements[1]
    assert db.execute.await_args_list[1].args[1]["project_id"] == "proj-1"


# --- exit code ------------------------------------------------------------------


async def _main_with(argv: list[str]):
    from evals.retrieval import run_eval

    with (
        patch("sys.argv", ["run_eval", "--project-id", "proj-1", *argv]),
        patch.object(settings, "cohere_api_key", ""),
    ):
        return await run_eval.main()


async def test_an_exact_run_that_measures_nothing_exits_2():
    """`--exact` is a recall GATE: a run that stops before comparing anything
    must not exit 0, or a broken setup reads as a passed guard."""
    assert await _main_with(["--hybrid", "--rerank", "--exact"]) == 2


async def test_an_exact_run_with_no_positive_cases_exits_2():
    from evals.retrieval import run_eval

    with patch.object(run_eval, "_run", AsyncMock(return_value=None)):
        assert await _main_with(["--hybrid", "--exact"]) == 2


async def test_the_same_early_stop_without_exact_keeps_its_exit_code():
    assert not await _main_with(["--hybrid", "--rerank"])


def test_mention_eval_pairs_only_ready_papers():
    """The second mention is picked from what a user could `@`-pick and
    production could retrieve: a pending or failed paper is neither."""
    from evals.retrieval import mention_eval

    sql = " ".join(mention_eval._PAPER_BEST_SQL.text.split())
    assert "AND p.status = 'ready'" in sql
