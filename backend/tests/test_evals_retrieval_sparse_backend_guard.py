"""The harness arms that run `chat_service._hybrid_sql` directly measure the
Postgres tsv lexical arm, so they refuse to run under any other
SPARSE_BACKEND rather than silently measuring a query production is not
running. The production arms of mention_eval follow the setting and stay."""

import argparse
from unittest.mock import patch

import pytest

from app.core.config import settings
from evals.retrieval import mention_eval, run_eval
from evals.retrieval.arms import sparse_backend_refusal


def test_postgres_is_allowed_and_anything_else_is_refused():
    assert sparse_backend_refusal("postgres") is None
    message = sparse_backend_refusal("bm25s")
    assert "SPARSE_BACKEND=bm25s" in message and "SPARSE_BACKEND=postgres" in message


async def test_run_eval_refuses_the_hybrid_arm_under_bm25s(capsys):
    with (
        patch.object(settings, "sparse_backend", "bm25s"),
        patch.object(run_eval, "SessionLocal", side_effect=AssertionError("must not run")),
    ):
        assert await run_eval._run(argparse.Namespace(hybrid=True)) is None
    assert "--hybrid refused" in capsys.readouterr().out


@pytest.mark.parametrize("backend", ["postgres", "bm25s"])
def test_mention_eval_keeps_only_production_arms_under_bm25s(backend):
    with patch.object(settings, "sparse_backend", backend):
        modes = {arm.mode for arm in mention_eval._arms()}
    if backend == "postgres":
        assert modes == {"production", "flat", "per_paper"}
    else:
        assert modes == {"production"}


def test_mention_eval_mirrors_stay_on_the_dense_only_path_under_bm25s():
    """The kill switch never touches the lexical arm, so nothing to refuse."""
    with (
        patch.object(settings, "sparse_backend", "bm25s"),
        patch.object(settings, "hybrid_retrieval", False),
    ):
        assert mention_eval._mirror_refusal() is None
        assert {arm.mode for arm in mention_eval._arms()} == {"production", "flat", "per_paper"}
