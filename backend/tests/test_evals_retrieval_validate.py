from evals.retrieval.bm25_arm import CorpusChunk
from evals.retrieval.golden_set import Case
from evals.retrieval.validate_golden_set import (
    document_frequencies,
    satisfying_papers,
    validate_case,
)


def _chunk(cid, pid, title, text, section=""):
    return CorpusChunk(cid, pid, title, text, section)


def _case(kind="paraphrase", question="How does it work?", title="Alpha", subs=("zebra",)):
    return Case(
        id="c1",
        kind=kind,
        question=question,
        paper_title_contains=title,
        expect_substrings=subs,
        split="tune",
        keyword=None,
    )


def test_df_counts_documents_not_occurrences():
    df, n = document_frequencies(["zebra zebra zebra", "zebra apple", "apple"])
    assert n == 3
    zebra = next(t for t in df if t.startswith("zebra"))
    assert df[zebra] == 2


def test_case_with_no_satisfying_chunk_is_a_problem():
    case = _case()
    by_paper = satisfying_papers(case, [_chunk("1", "p1", "Alpha paper", "nothing here")])
    assert by_paper == {}
    problems = validate_case(case, by_paper, {}, 10)
    assert len(problems) == 1 and "no chunk" in problems[0]


def test_case_satisfied_in_two_papers_is_a_problem():
    case = _case(title="paper")
    chunks = [
        _chunk("1", "p1", "Alpha paper", "a zebra"),
        _chunk("2", "p2", "Beta paper", "another zebra"),
    ]
    by_paper = satisfying_papers(case, chunks)
    assert set(by_paper) == {"p1", "p2"}
    problems = validate_case(case, by_paper, {}, 10)
    assert len(problems) == 1
    assert "Alpha paper" in problems[0] and "Beta paper" in problems[0]


def test_leakage_problem_is_reported_with_tokens():
    case = _case(question="Tell me about the zebra")
    by_paper = satisfying_papers(case, [_chunk("1", "p1", "Alpha paper", "a zebra")])
    problems = validate_case(case, by_paper, {}, 100)
    assert len(problems) == 1 and "zebra" in problems[0]


def test_clean_case_has_no_problems():
    case = _case(question="How does the striped animal behave?")
    by_paper = satisfying_papers(case, [_chunk("1", "p1", "Alpha paper", "a zebra")])
    assert validate_case(case, by_paper, {}, 100) == []
