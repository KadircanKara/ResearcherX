from evals.retrieval.golden_set import Case
from evals.retrieval.metrics import Scored
from evals.retrieval.per_kind import ARMS, KindRow, macro, per_kind_table


def _case(cid: str, kind: str) -> Case:
    return Case(
        id=cid,
        kind=kind,
        question="q",
        paper_title_contains=None if kind == "off_topic" else "Paper",
        expect_substrings=() if kind == "off_topic" else ("needle",),
    )


def _chunk(text: str, distance: float | None) -> Scored:
    return Scored(paper_id="p", paper_title="Paper", chunk_text=text, distance=distance)


HIT = "has needle"
MISS = "nothing"


def _lists(per_case: list[list[Scored]]) -> dict[str, list[list[Scored]]]:
    return {arm: per_case for arm in ARMS}


def _row(rows: list[KindRow], kind: str, arm: str) -> KindRow:
    return next(r for r in rows if r.kind == kind and r.arm == arm)


def test_per_kind_groups_by_kind_and_arm():
    cases = [_case("a", "paraphrase"), _case("b", "paraphrase"), _case("c", "keyword")]
    per_case = [[_chunk(HIT, 0.1)], [_chunk(MISS, 0.1)], [_chunk(MISS, 0.1), _chunk(HIT, 0.2)]]
    rows = per_kind_table(cases, _lists(per_case))
    assert len(rows) == 2 * len(ARMS)
    para = _row(rows, "paraphrase", "dense")
    assert para.n == 2
    assert para.recall[10] == 0.5 and para.recall[60] == 0.5
    assert para.mrr == 0.5
    kw = _row(rows, "keyword", "hybrid_bm25")
    assert kw.n == 1 and kw.mrr == 0.5


def test_sparse_arms_use_presorted_order():
    cases = [_case("a", "keyword")]
    # List order has the hit first; distance order would put it last.
    per_case = [[_chunk(HIT, 0.9), _chunk(MISS, 0.1)]]
    rows = per_kind_table(cases, _lists(per_case))
    assert _row(rows, "keyword", "dense").mrr == 0.5
    assert _row(rows, "keyword", "sparse_sql").mrr == 1.0
    assert _row(rows, "keyword", "hybrid_bm25").mrr == 1.0


def test_sparse_arms_tolerate_missing_distance():
    cases = [_case("a", "keyword")]
    lists = _lists([[_chunk(HIT, 0.1)]])
    lists["sparse_bm25"] = [[_chunk(MISS, None), _chunk(HIT, None)]]
    rows = per_kind_table(cases, lists)
    assert _row(rows, "keyword", "sparse_bm25").mrr == 0.5


def test_macro_is_unweighted_mean_over_positive_kinds():
    cases = (
        [_case(f"p{i}", "paraphrase") for i in range(30)]
        + [_case(f"pk{i}", "paraphrase_keyword") for i in range(10)]
        + [_case(f"k{i}", "keyword") for i in range(2)]
    )
    # paraphrase: all hit; paraphrase_keyword: none; keyword: all hit.
    per_case = [[_chunk(HIT, 0.1)]] * 30 + [[_chunk(MISS, 0.1)]] * 10 + [[_chunk(HIT, 0.1)]] * 2
    rows = per_kind_table(cases, _lists(per_case))
    assert macro(rows, "dense", "mrr") == 2 / 3
    assert macro(rows, "dense", "r@60") == 2 / 3


def test_off_topic_rows_are_excluded():
    cases = [_case("a", "keyword"), _case("n", "off_topic")]
    per_case = [[_chunk(HIT, 0.1)], [_chunk(HIT, 0.1)]]
    rows = per_kind_table(cases, _lists(per_case))
    assert {r.kind for r in rows} == {"keyword"}
    assert _row(rows, "keyword", "dense").n == 1
