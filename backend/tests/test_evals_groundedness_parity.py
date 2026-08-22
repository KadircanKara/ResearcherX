"""The groundedness harness must not drift from the pipeline it measures.

`evals/groundedness/generate.py` imports every production DECISION but repeats
the WIRING (see its docstring for why it cannot just call
`ChatService.respond`). Repeated wiring rots silently: the harness keeps
reporting numbers for a system that has moved on. These tests pin the parts
whose drift would corrupt a measurement rather than crash it.
"""

import inspect
import re

from app.services import chat_service
from evals.groundedness import generate


def _post_pass_order(source: str) -> list[str]:
    """Order of the citation post-pass CALLS in a function's source.

    Matched as `name(` rather than as a bare name: production's own comments
    discuss `renumber_citations` several lines above the call site, and a
    plain substring search reads that prose as the call -- reporting an order
    the code does not have.
    """
    found = []
    for name in (
        "expand_grouped_citations",
        "strip_misattributed_citations",
        "renumber_citations",
    ):
        match = re.search(rf"\b{name}\(", source)
        if match:
            found.append((match.start(), name))
    return [name for _, name in sorted(found)]


def test_the_harness_runs_the_citation_post_pass_in_productions_order():
    """Expand, then strip, then renumber.

    The order is load-bearing in production (a marker stripped after
    renumbering leaves a chip pointing at a claim that no longer cites it and
    burns a number in the visible sequence). A harness that ran them in a
    different order would score citations the user would never see.
    """
    production = _post_pass_order(inspect.getsource(chat_service.ChatService.respond))
    harness = _post_pass_order(inspect.getsource(generate.AnswerGenerator.generate))
    assert harness == production
    assert harness == [
        "expand_grouped_citations",
        "strip_misattributed_citations",
        "renumber_citations",
    ]


def test_the_harness_imports_retrieval_rather_than_reimplementing_it():
    """The per-paper floor shipped as a no-op because a harness mirrored the
    SQL instead of calling it. Both retrieval entry points must be the
    production methods, not copies."""
    source = inspect.getsource(generate)
    assert "_retrieve_paper_chunks" in source
    assert "_retrieve_mentioned_chunks" in source
    # The mirror smell is the harness touching the embedding tables itself.
    # Its one query loads `Paper` rows, which is metadata, not retrieval.
    assert "paper_chunk_embeddings" not in source
    assert "<=>" not in source


def test_the_harness_writes_nothing():
    """Every eval harness reads only. A groundedness run that persisted its
    answers would leave dozens of throwaway conversations in the dev project —
    the reason this module assembles the pipeline instead of calling respond."""
    source = inspect.getsource(generate)
    for writer in ("save_message", "create_conversation", "db.add", "commit()"):
        assert writer not in source


def test_the_scope_ladder_matches_production_minus_the_rung_needing_a_click():
    """Resolver then global. A mention cannot exist for a golden-set question,
    but the resolver can fire on one, and a harness that skipped it would
    measure a scope production never uses."""
    source = inspect.getsource(generate.AnswerGenerator.generate)
    assert "resolve_papers_with_evidence" in source
    assert "ScopeWidener" in inspect.getsource(generate) or "_widener" in source
