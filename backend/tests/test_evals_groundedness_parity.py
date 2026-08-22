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
from evals.groundedness import generate, run_eval


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


def test_credit_exhaustion_is_told_apart_from_a_throughput_limit():
    """A 429 means two different things on this endpoint. Treating them the
    same is what turned the first real run into 20 identical error rows: the
    credits ran out, and every remaining case spent a request to rediscover it.
    """
    from evals.groundedness.judge import _is_terminal_quota, _retry_after_seconds

    terminal = Exception(
        "Error code: 429 - You have no credits remaining. code: credit_balance_exhausted"
    )
    throughput = Exception(
        "Error code: 429 - Rate limit reached for gpt-4.1 ... Limit 30000, Used 29575, "
        "Requested 12533. Please try again in 24.216s."
    )
    assert _is_terminal_quota(terminal)
    assert not _is_terminal_quota(throughput)
    # The server's own hint wins, plus a second of slack -- a retry landing
    # exactly on the boundary is refused again.
    assert _retry_after_seconds(throughput, 1) == 25.216
    # No hint: back off far enough to clear a whole TPM minute.
    assert _retry_after_seconds(Exception("429"), 3) == 15.0


def test_the_runner_aborts_the_whole_run_when_credits_run_out():
    """Terminal for BOTH paths: the judge raises QuotaExhausted, but answer
    generation goes through the production client and raises the provider's own
    RateLimitError, so the runner has to recognise the text too."""
    source = inspect.getsource(run_eval)
    assert "abort.set()" in source
    assert "aborted: the account ran out of credits" in source


def test_the_prompt_no_longer_instructs_a_hand_off_to_general_knowledge():
    """The hand-off was removed from production on 2026-08-22.

    The disclosed/undisclosed split STAYS: it is what makes runs recorded
    before that change readable, and a model can still produce the pattern
    unprompted -- `disclosed` then measures a defect rather than a design, and
    the report prints both rates either way. What must not survive is the
    prompt instructing it.
    """
    from app.agents.chat_agent import SYSTEM

    prompt = SYSTEM.lower()
    assert "based on general knowledge: ...'" not in prompt
    assert "never answer from general knowledge" in prompt
    assert "the ingested documents do not cover this." in prompt


def test_the_hand_off_markers_still_detect_the_pattern_they_were_built_for():
    """`_HANDOFF_MARKERS` outlives the prompt that motivated it, so it is
    pinned against a real answer from the 2026-08-22 reference run rather than
    against SYSTEM."""
    from evals.groundedness.claims import extract_claims

    claims = extract_claims(
        "The assigned papers do not appear to cover this. "
        "Based on general knowledge: FPV goggles include the Fat Shark Dominator series."
    )
    assert claims[0].disclosed is False
    assert claims[1].disclosed is True


def test_the_refusal_string_still_matches_the_production_prompt():
    """The harness excludes production's exact refusal from the claim list, so
    the two must not drift. If SYSTEM's refusal wording changes and REFUSAL
    does not, every refusal becomes a graded claim again -- and the two judges
    measured on 2026-08-22 disagreed about that sentence on 12 of 12 negatives
    (gpt-4.1 called it supported, gpt-4.1-mini called it unsupported), so the
    drift would show up as judge noise rather than as an obvious break.
    """
    from app.agents.chat_agent import SYSTEM
    from evals.groundedness.claims import REFUSAL

    assert REFUSAL in SYSTEM.lower()
