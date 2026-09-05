"""app/local_rag/cli.py — argument parsing only.

The commands themselves are thin wiring over `ingest()`, `retrieve()` and
`answer()`, each tested in its own module. What is worth pinning here is
that a flag reaches the parameter it names — a silently ignored `--top-n`
looks exactly like a retrieval result.
"""

from app.local_rag.cli import build_parser, params_from_args
from app.local_rag.search import SearchParams


def test_ask_defaults_to_the_operator_set_parameters():
    args = build_parser().parse_args(["ask", "what is revisit time?"])
    assert params_from_args(args) == SearchParams()


def test_top_n_and_candidates_reach_the_search_parameters():
    args = build_parser().parse_args(["ask", "q", "--top-n", "3", "--candidates", "25"])
    params = params_from_args(args)
    assert params.rerank_top_n == 3
    assert params.rerank_candidates == 25
    # Untouched flags keep the operator-set values.
    assert params.rrf_k == 60
    assert (params.dense_weight, params.sparse_weight) == (0.5, 0.5)


def test_the_distance_gate_is_overridable():
    args = build_parser().parse_args(["ask", "q", "--threshold", "0.9"])
    assert params_from_args(args).similarity_threshold == 0.9


def test_ingest_takes_several_sources_and_a_root():
    args = build_parser().parse_args(["ingest", "a.pdf", "b.txt", "--root", "/tmp/idx"])
    assert args.command == "ingest"
    assert args.sources == ["a.pdf", "b.txt"]
    assert args.root == "/tmp/idx"


def test_ask_can_skip_generation():
    args = build_parser().parse_args(["ask", "q", "--no-answer"])
    assert args.no_answer is True
