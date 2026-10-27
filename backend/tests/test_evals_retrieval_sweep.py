from evals.retrieval import sweep as sw
from evals.retrieval.golden_set import Case
from evals.retrieval.metrics import Scored
from evals.retrieval.sweep import (
    K_GRID,
    W_DENSE_GRID,
    CaseRankings,
    GridPoint,
    best,
    current_point,
    report,
    sweep,
)


def _case(cid: str, split: str = "tune", kind: str = "keyword") -> Case:
    return Case(
        id=cid,
        kind=kind,
        question="q",
        paper_title_contains="Paper",
        expect_substrings=("needle",),
        split=split,
    )


def _row(cid: str, text: str = "nothing") -> Scored:
    return Scored(paper_id="p", paper_title="Paper", chunk_text=text, distance=None, chunk_id=cid)


def _rankings(split: str = "tune", kind: str = "keyword", cid: str = "c") -> CaseRankings:
    by_id = {"x": _row("x"), "a": _row("a", "has needle")}
    return CaseRankings(case=_case(cid, split, kind), dense=["x", "a"], sparse=["a"], by_id=by_id)


def _point(w: float, k: int, mrr: float, r60: float = 1.0) -> GridPoint:
    return GridPoint(w, k, {}, mrr, r60)


def test_sweep_refuses_nothing_and_covers_full_grid():
    points = sweep([_rankings()], budget=60)
    assert len(points) == 36
    assert {(p.w_dense, p.k) for p in points} == {(w, k) for w in W_DENSE_GRID for k in K_GRID}


def test_w_sparse_is_complement(monkeypatch):
    seen = []

    def spy(dense, sparse, *, w_dense, w_sparse, k):
        seen.append((w_dense, w_sparse))
        return [(key, 1.0) for key in dense]

    monkeypatch.setattr(sw, "fuse_rrf", spy)
    sweep([_rankings()], budget=60)
    assert all(abs(wd + ws - 1.0) < 1e-12 for wd, ws in seen)


def test_sweep_reuses_fuse_rrf(monkeypatch):
    calls = []
    real = sw.fuse_rrf

    def spy(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(sw, "fuse_rrf", spy)
    sweep([_rankings(cid="1"), _rankings(cid="2")], budget=60)
    assert len(calls) == 36 * 2


def test_budget_truncates_the_fused_list():
    p = sw.evaluate_point([_rankings()], 0.7, 30, budget=1)
    assert p.macro_r60 == 1.0  # 'a' outranks 'x' once sparse agrees
    p0 = sw.evaluate_point(
        [CaseRankings(_case("z"), ["x", "a"], [], {"x": _row("x"), "a": _row("a", "needle")})],
        0.7,
        30,
        budget=1,
    )
    assert p0.macro_r60 == 0.0


def test_best_picks_highest_macro_mrr():
    pts = [_point(0.5, 10, 0.4), _point(0.3, 5, 0.9), _point(0.7, 30, 0.6)]
    assert (best(pts).w_dense, best(pts).k) == (0.3, 5)


def test_best_breaks_ties_toward_current_settings():
    cur = current_point()
    pts = [_point(0.3, 5, 0.8), _point(0.8, 100, 0.8), _point(0.6, 30, 0.8), _point(*cur, 0.8)]
    assert (best(pts).w_dense, best(pts).k) == cur
    pts = [_point(0.3, 5, 0.8, 0.9), _point(0.8, 100, 0.8, 1.0)]
    assert best(pts).k == 100  # r@60 breaks the tie before distance does


def _opposed_rankings():
    """A tune case that wants a dense-heavy point and a test case that wants a
    sparse-heavy one. In both, the rival sits at dense rank 1 vs the other at
    dense rank 40, so which one fuses first flips with w_dense * 39 / (k + 40)
    against w_sparse."""
    fillers = [f"f{i}" for i in range(38)]
    ids = ["ans", "d", "x1", "a", *fillers]
    by_id = {i: _row(i) for i in ids}
    by_id["ans"] = _row("ans", "has needle")
    by_id["a"] = _row("a", "has needle")
    # Tune: the answer is dense rank 1; a distractor is sparse rank 1.
    tune = CaseRankings(_case("tune1", "tune"), ["ans", *fillers, "d"], ["d"], by_id)
    # Test: the answer is dense rank 40 but sparse rank 1; x1 leads dense.
    test = CaseRankings(_case("test1", "test"), ["x1", *fillers, "a"], ["a"], by_id)
    return tune, test


def test_tune_point_is_chosen_without_test_cases():
    tune, test = _opposed_rankings()
    chosen = best(sweep([tune], budget=60))
    assert chosen.macro_mrr == 1.0
    # The test case really does disagree: it is not perfect at the tune pick,
    # and pooling it would move the choice.
    pooled = best(sweep([tune, test], budget=60))
    assert (pooled.w_dense, pooled.k) != (chosen.w_dense, chosen.k)

    payload = report([tune], [test], budget=60)
    assert (payload["best_tune"]["w_dense"], payload["best_tune"]["k"]) == (
        chosen.w_dense,
        chosen.k,
    )
    assert (payload["best_tune"]["w_dense"], payload["best_tune"]["k"]) != (
        pooled.w_dense,
        pooled.k,
    )
    # Test-split numbers come from the test cases alone.
    assert payload["n_tune"] == 1 and payload["n_test"] == 1
    expected = sw.evaluate_point([test], chosen.w_dense, chosen.k, budget=60)
    assert payload["best_test"] == vars(expected)
    assert payload["best_test"]["macro_mrr"] < 1.0
    assert report([tune], [], budget=60)["best_test"] is None


def test_current_point_is_read_from_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "hybrid_dense_weight", 0.4)
    monkeypatch.setattr(settings, "hybrid_rrf_k", 10)
    assert current_point() == (0.4, 10)
    pts = [_point(0.3, 5, 0.8), _point(0.4, 10, 0.8), _point(0.8, 100, 0.8)]
    assert (best(pts).w_dense, best(pts).k) == (0.4, 10)


def test_report_evaluates_an_off_grid_current_point(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "hybrid_dense_weight", 0.65)
    monkeypatch.setattr(settings, "hybrid_rrf_k", 45)
    payload = report([_rankings()], [_rankings(split="test", cid="t")], budget=60)
    assert (payload["current_tune"]["w_dense"], payload["current_tune"]["k"]) == (0.65, 45)
    assert (payload["current_test"]["w_dense"], payload["current_test"]["k"]) == (0.65, 45)
