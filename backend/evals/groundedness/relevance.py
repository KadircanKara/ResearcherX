"""Answer relevance, context precision, the judge-independence guard, the CSV.

Pure -- no model calls, no DB, no `app.*` import. The judge methods that
produce the verdicts scored here live on `judge.Judge`, beside the support
judge, so every prompt the judge is given is in one file.

WHY THESE TWO SIT BESIDE GROUNDEDNESS RATHER THAN INSIDE IT. Groundedness asks
"is what the answer says carried by the excerpts". That is silent on two other
ways a turn fails:

  answer relevance    a fully grounded answer to a question nobody asked.
                      Every claim supported, the user no better off.
  context precision   how much of the catalog was about the question at all.
                      Retrieval's recall is `evals/retrieval`'s job and is
                      measured deterministically there; this is the other half
                      -- the noise the model had to read past, which recall
                      cannot see and which costs ~430 tokens a chunk.

Both are judged, so both are opt-in (`--metrics`): the default run stays the
support-only run every "Measured" block in README.md was taken with.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from typing import IO, Iterable, Mapping, Sequence

from evals.groundedness.metrics import CaseOutcome

METRICS = ("support", "answer_relevance", "context_relevance")
_DEFAULT_METRICS = frozenset({"support"})


def parse_metrics(raw: str | None) -> frozenset[str]:
    """`--metrics a,b` -> a set. Unknown names are refused, not ignored: a typo
    that silently ran support-only would produce a report with the requested
    column quietly missing."""
    if raw is None:
        return _DEFAULT_METRICS
    names = frozenset(part.strip() for part in raw.split(",") if part.strip())
    unknown = sorted(names - set(METRICS))
    if unknown:
        raise ValueError(
            f"unknown metric(s) {', '.join(unknown)}; choose from {', '.join(METRICS)}"
        )
    # `support` is the harness's spine -- the evidence split and the per-case
    # rows hang off it -- so naming only an add-on adds to it, never replaces it.
    return names | _DEFAULT_METRICS


@dataclass(frozen=True)
class RelevanceOutcome:
    case_id: str
    kind: str
    # None when not measured: the metric was not requested, the call errored,
    # or the case is off_topic -- where the right answer is a refusal, which a
    # relevance judge scores low. `stance` already grades those cases.
    answer_relevance: float | None
    answer_relevance_reason: str
    # Excerpt numbers in CATALOG order -- the order the model read them in.
    # The numbers are post-renumbering citation numbers, NOT ranks: position
    # in this tuple is the rank.
    shown: tuple[int, ...]
    # None when context relevance was not measured, which is different from
    # "measured, nothing relevant" (an empty set -- the expected result on an
    # off_topic question).
    relevant: frozenset[int] | None


def context_precision(outcomes: Sequence[RelevanceOutcome], k: int | None = None) -> float | None:
    """Relevant excerpts over excerpts shown, pooled across cases.

    With `k`, only the first k catalog POSITIONS count. A catalog shorter than
    k is divided by its own length: dividing by k would punish a well-gated
    short catalog for not padding itself with noise.
    """
    relevant = shown = 0
    for outcome in outcomes:
        if outcome.relevant is None:
            continue
        window = outcome.shown if k is None else outcome.shown[:k]
        shown += len(window)
        relevant += sum(1 for n in window if n in outcome.relevant)
    return relevant / shown if shown else None


def mean_answer_relevance(outcomes: Sequence[RelevanceOutcome]) -> float | None:
    scores = [o.answer_relevance for o in outcomes if o.answer_relevance is not None]
    return sum(scores) / len(scores) if scores else None


# -- the judge must not be the answerer --------------------------------------


class SelfJudgeError(RuntimeError):
    """The judge model is the answering model."""


# Bare aliases the Claude CLI accepts, which carry no vendor prefix to match.
_CLAUDE_ALIASES = ("opus", "sonnet", "haiku", "fable", "mythos")


def _family(model: str) -> str | None:
    name = model.strip().lower()
    if "claude" in name or name.split("-")[0] in _CLAUDE_ALIASES:
        return "claude"
    for prefix in ("gpt", "gemini", "llama", "mistral", "qwen", "deepseek"):
        if prefix in name:
            return prefix
    return None


def check_judge_independence(
    *, judge_model: str, answering_model: str, allow_self_judge: bool
) -> str:
    """A line for the report header, or "" when there is nothing to disclose.

    Raises `SelfJudgeError` when the judge IS the answering model, unless
    forced. Self-preference bias is the reason `judge.py` owns its own client;
    that protection is worth nothing if both ends are then pointed at one
    model, which is one flag away when the judge and the system under test
    share an endpoint (the CLI proxy serves both).
    """
    judge, answering = judge_model.strip().lower(), answering_model.strip().lower()
    if judge == answering:
        if not allow_self_judge:
            raise SelfJudgeError(
                f"judge model and answering model are both {judge!r}: a model grading its "
                "own output is self-preference bias. Pick another --judge-model, or pass "
                "--allow-self-judge to run anyway and have the report say so."
            )
        return (
            f"WARNING: SELF-JUDGED -- {judge!r} graded its own answers. Every judged number "
            "below is biased toward the system under test."
        )
    family = _family(judge)
    if family is not None and family == _family(answering):
        return (
            f"NOTE: judge and answering model are from the same model family ({family}); "
            "self-preference bias is reduced by using a different model, not removed."
        )
    return ""


# -- csv ---------------------------------------------------------------------

CSV_COLUMNS = (
    "case_id",
    "kind",
    "question",
    "answer",
    "contexts",
    "evidence_present",
    "stance",
    "claims",
    "supported",
    "unsupported",
    "contradicted",
    "groundedness",
    "answer_relevance",
    "answer_relevance_reason",
    "context_count",
    "relevant_contexts",
    "context_precision",
    "context_precision_at_10",
    "judge_model",
    "answering_model",
)


def _cell(value: float | None) -> float | str:
    # An unmeasured score is an EMPTY cell. A zero is a measurement, and a
    # metric that was not requested must not be readable as the worst score.
    return "" if value is None else value


def csv_row(
    *,
    outcome: CaseOutcome,
    relevance: RelevanceOutcome | None,
    question: str,
    answer: str,
    contexts: Sequence[str],
    judge_model: str,
    answering_model: str,
) -> dict[str, object]:
    checkable = outcome.checkable
    by_verdict = {
        verdict: sum(1 for c in checkable if c.verdict == verdict)
        for verdict in ("supported", "unsupported", "contradicted")
    }
    one = [relevance] if relevance is not None else []
    judged_context = relevance is not None and relevance.relevant is not None
    return {
        "case_id": outcome.case_id,
        "kind": outcome.kind,
        "question": question,
        "answer": answer,
        "contexts": json.dumps(list(contexts), ensure_ascii=False),
        "evidence_present": _cell(outcome.evidence_present),
        "stance": outcome.stance,
        "claims": len(checkable),
        **by_verdict,
        "groundedness": _cell(by_verdict["supported"] / len(checkable) if checkable else None),
        "answer_relevance": _cell(relevance.answer_relevance if relevance else None),
        "answer_relevance_reason": relevance.answer_relevance_reason if relevance else "",
        "context_count": len(contexts),
        "relevant_contexts": len(relevance.relevant) if judged_context else "",
        "context_precision": _cell(context_precision(one)),
        "context_precision_at_10": _cell(context_precision(one, k=10)),
        "judge_model": judge_model,
        "answering_model": answering_model,
    }


def write_csv(handle: IO[str], rows: Iterable[Mapping[str, object]]) -> None:
    writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
