"""Groundedness harness: is the ANSWER carried by the excerpts it was given?

    docker compose exec -T backend python -m evals.groundedness.run_eval \
        --project-id <uuid> --json /tmp/groundedness.json

The `-m` form is required for the same reason every other harness documents:
`pyproject.toml` packages only `app*`.

WHAT THIS MEASURES THAT `evals/retrieval` DOES NOT. That harness stops at "did
the answering chunk reach the budget". Everything after that -- whether the
model used it, whether it invented what the excerpts do not say, whether the
marker it wrote points at the paper that actually supports the sentence -- was
unmeasured until this module existed. `strip_misattributed_citations` catches
one misattribution shape deterministically (prose that NAMES a different
paper); this catches the shape it structurally cannot see: prose that names
nobody and cites a paper that does not say it.

READS ONLY. Answers are generated through production's own components without
persisting a conversation -- see `generate.py` for exactly what is imported and
what is re-wired.

THE EVIDENCE SPLIT IS THE POINT. A positive whose answering chunk never reached
the budget is a RETRIEVAL failure; scoring its answer as a generation failure
blames the wrong subsystem. Every headline number is reported twice: pooled,
and over the cases where the expected evidence was actually in front of the
model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from evals.groundedness.agreement import pair_verdicts, verdict_shift
from evals.groundedness.claims import extract_claims, marker_count
from evals.groundedness.generate import AnswerGenerator, Generated
from evals.groundedness.judge import DEFAULT_JUDGE_MODEL, Judge, QuotaExhausted
from evals.groundedness.replay import load_generations, save_generations
from evals.groundedness.metrics import (
    CaseOutcome,
    ScoredClaim,
    abstention_rate,
    citation_coverage,
    citation_precision,
    claim_support_rate,
    clean_answer_rate,
    contradiction_rate,
    hallucinated_claim_rate,
    split_by_evidence,
    unjudged_markers,
)
from evals.retrieval.golden_set import Case, chunk_satisfies, load_golden_set

_DEFAULT_SET = Path(__file__).resolve().parents[1] / "retrieval" / "golden_set.json"

# Concurrency across cases. Each case is 3 model calls (answer, claim judge,
# stance) plus one embedding; the ceiling is politeness to the provider, not a
# correctness bound -- nothing in a case touches another case's state.
# Cases in flight. One, not four: a judge call is ~12k tokens against a 30k
# TPM cap, so two cases in flight spend most of their time in rate-limit
# backoff, and four spent the first real run's budget on 429s.
_DEFAULT_CONCURRENCY = 1


@dataclass
class CaseError:
    case_id: str
    stage: str
    error: str


def _is_out_of_credits(exc: BaseException) -> bool:
    """Answer generation goes through the PRODUCTION client, which raises the
    provider's own `RateLimitError` rather than the judge's `QuotaExhausted`.

    Both have to abort the run: on 2026-08-22 the credits ran out mid-run and
    every one of the 20 remaining cases spent a request to produce an identical
    error row.
    """
    text = str(exc).lower()
    return "insufficient_quota" in text or "no credits remaining" in text


def _evidence_present(case: Case, generated: Generated) -> bool | None:
    """Did the text this case expects actually reach the model?

    None for negatives, which have no expected text by construction.
    Uses the golden set's own `chunk_satisfies`, so "the evidence arrived"
    means exactly what "the retrieval harness scored a hit" means -- the two
    harnesses cannot drift into two definitions of the same event.
    """
    if case.is_negative:
        return None
    return any(chunk_satisfies(case, c.title, c.text) for c in generated.chunks)


async def _run_case(
    *,
    case: Case,
    project_id: str,
    generator: AnswerGenerator,
    judge: Judge,
    semaphore: asyncio.Semaphore,
    abort: asyncio.Event,
    replayed: Generated | None = None,
    second_judge: Judge | None = None,
) -> tuple[CaseOutcome, Generated, dict[int, str]] | CaseError:
    if abort.is_set():
        return CaseError(case.id, "skipped", "aborted: the account ran out of credits")
    async with semaphore:
        if abort.is_set():
            return CaseError(case.id, "skipped", "aborted: the account ran out of credits")
        if replayed is not None:
            generated = replayed
        else:
            try:
                generated = await generator.generate(project_id=project_id, question=case.question)
            except Exception as exc:  # noqa: BLE001 - reported, never silently dropped
                if _is_out_of_credits(exc):
                    abort.set()
                    return CaseError(case.id, "generate", "aborted: the account ran out of credits")
                return CaseError(case.id, "generate", f"{type(exc).__name__}: {exc}")

        claims = extract_claims(generated.answer)
        paper_of = {c.n: c.paper_id for c in generated.chunks}

        try:
            verdicts = await judge.judge_claims(
                question=case.question,
                claims=[(c.index, c.text) for c in claims],
                excerpts=[(c.n, c.title, c.text) for c in generated.chunks],
            )
            stance = await judge.judge_stance(question=case.question, answer=generated.answer)
            # The candidate judge sees BYTE-IDENTICAL input -- same answer, same
            # catalog, same claim list. Judging freshly generated answers
            # instead would make a disagreement inseparable from the two runs
            # having answered differently.
            second_verdicts: dict[int, str] = {}
            if second_judge is not None:
                other = await second_judge.judge_claims(
                    question=case.question,
                    claims=[(c.index, c.text) for c in claims],
                    excerpts=[(c.n, c.title, c.text) for c in generated.chunks],
                )
                second_verdicts = {index: v.verdict for index, v in other.items()}
        except QuotaExhausted:
            abort.set()
            return CaseError(case.id, "judge", "aborted: the account ran out of credits")
        except Exception as exc:  # noqa: BLE001
            if _is_out_of_credits(exc):
                abort.set()
                return CaseError(case.id, "judge", "aborted: the account ran out of credits")
            return CaseError(case.id, "judge", f"{type(exc).__name__}: {exc}")

        scored = tuple(
            ScoredClaim(
                index=claim.index,
                verdict=verdicts[claim.index].verdict,
                markers=claim.markers,
                supporting_excerpts=tuple(verdicts[claim.index].supporting_excerpts),
                supporting_papers=frozenset(
                    paper_of[n] for n in verdicts[claim.index].supporting_excerpts if n in paper_of
                ),
                marker_papers=frozenset(paper_of[n] for n in claim.markers if n in paper_of),
            )
            for claim in claims
        )
        outcome = CaseOutcome(
            case_id=case.id,
            kind=case.kind,
            claims=scored,
            stance=stance.stance,
            evidence_present=_evidence_present(case, generated),
            marker_total=marker_count(generated.answer),
        )
        return outcome, generated, second_verdicts


def _fmt(value: float | None) -> str:
    return "  -  " if value is None else f"{value:.2f}"


def _summary_block(label: str, outcomes: list[CaseOutcome]) -> str:
    if not outcomes:
        return f"{label:<34} (no cases)"
    claims = sum(len(o.checkable) for o in outcomes)
    return (
        f"{label:<34}{len(outcomes):>5}{claims:>8}"
        f"{_fmt(claim_support_rate(outcomes)):>10}"
        f"{_fmt(hallucinated_claim_rate(outcomes)):>10}"
        f"{_fmt(contradiction_rate(outcomes)):>10}"
        f"{_fmt(clean_answer_rate(outcomes)):>10}"
        f"{_fmt(citation_precision(outcomes)):>10}"
        f"{_fmt(citation_coverage(outcomes)):>10}"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure answer groundedness and citation correctness."
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--set", type=Path, default=_DEFAULT_SET)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=_DEFAULT_CONCURRENCY,
        help=(
            "cases in flight. The judge's TPM cap, not this, is the real throughput "
            "bound -- raising it mostly buys 429s the judge then has to sleep off"
        ),
    )
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument(
        "--save-generations",
        type=Path,
        default=None,
        help="write the answers and their excerpt catalogs here, for --replay",
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=None,
        help=(
            "judge the answers in this generation cache instead of producing new ones. "
            "Makes a judge comparison controlled (identical input) and cheap (no generation)"
        ),
    )
    parser.add_argument(
        "--compare-judge",
        default=None,
        metavar="MODEL",
        help=(
            "also judge every claim with MODEL and report agreement against --judge-model. "
            "Use with --replay to answer 'is a cheaper judge good enough' for judge tokens only"
        ),
    )
    parser.add_argument("--per-case", action="store_true", help="print the per-case table")
    parser.add_argument(
        "--limit", type=int, default=None, help="first N cases only, for a smoke run"
    )
    args = parser.parse_args()

    cases = load_golden_set(args.set)
    if args.limit:
        cases = cases[: args.limit]

    generator = AnswerGenerator()
    judge = Judge(model=args.judge_model)
    second_judge = Judge(model=args.compare_judge) if args.compare_judge else None

    replayed: dict[str, Generated] = {}
    replay_model = None
    if args.replay:
        replayed, replay_model = load_generations(args.replay)
        # A cache is keyed by case id, so a set the cache does not cover would
        # silently generate fresh answers for the missing cases -- which is
        # exactly the uncontrolled comparison --replay exists to prevent.
        missing = [c.id for c in cases if c.id not in replayed]
        if missing:
            raise SystemExit(
                f"{args.replay}: no saved generation for {len(missing)} case(s): "
                f"{', '.join(missing[:5])}{' ...' if len(missing) > 5 else ''}"
            )
    semaphore = asyncio.Semaphore(args.concurrency)
    # Set by the first case to see a credit-exhaustion error. Every case still
    # queued then short-circuits instead of spending a request on a failure
    # already known to be terminal.
    abort = asyncio.Event()

    results = await asyncio.gather(
        *(
            _run_case(
                case=case,
                project_id=args.project_id,
                generator=generator,
                judge=judge,
                semaphore=semaphore,
                abort=abort,
                replayed=replayed.get(case.id),
                second_judge=second_judge,
            )
            for case in cases
        )
    )

    outcomes: list[CaseOutcome] = []
    generations: dict[str, Generated] = {}
    errors: list[CaseError] = []
    candidate_verdicts: dict[str, dict[int, str]] = {}
    for result in results:
        if isinstance(result, CaseError):
            errors.append(result)
            continue
        outcome, generated, second = result
        outcomes.append(outcome)
        generations[outcome.case_id] = generated
        if second:
            candidate_verdicts[outcome.case_id] = second

    positives = [o for o in outcomes if o.kind != "off_topic"]
    negatives = [o for o in outcomes if o.kind == "off_topic"]
    with_evidence, without_evidence = split_by_evidence(positives)

    from app.core.config import settings

    answering = replay_model if args.replay else settings.llm_model
    replay_note = f" (replayed from {args.replay.name})" if args.replay else ""
    print(
        f"judge: {args.judge_model}   answering model: {answering}{replay_note}   "
        f"project: {args.project_id}"
    )
    print(f"cases: {len(outcomes)} scored, {len(errors)} errored   set: {args.set.name}")
    print(
        "(claims are SENTENCES; no_claim sentences -- headings, refusals -- are excluded "
        "from every rate)"
    )
    print()
    header = (
        f"{'group':<34}{'cases':>5}{'claims':>8}{'support':>10}{'halluc':>10}"
        f"{'contra':>10}{'clean':>10}{'cite-P':>10}{'cite-cov':>10}"
    )
    print(header)
    print(_summary_block("positives (pooled)", positives))
    print(_summary_block("  evidence reached the model", with_evidence))
    print(_summary_block("  evidence did NOT reach", without_evidence))
    print(_summary_block("off_topic negatives", negatives))
    print()
    print(
        f"abstention  positives: {_fmt(abstention_rate(positives))} (lower is better)   "
        f"negatives: {_fmt(abstention_rate(negatives))} (1.00 is the target)"
    )
    print(
        f"markers shown but unjudgeable (sentences too short to be claims): "
        f"{unjudged_markers(outcomes)}"
    )
    stripped = sum(g.stripped_markers for g in generations.values())
    print(f"markers removed by strip_misattributed_citations before scoring: {stripped}")

    if args.per_case:
        print()
        print(f"{'case':<34}{'kind':<11}{'ev':<4}{'claims':>7}{'bad':>5}{'stance':>10}")
        for outcome in outcomes:
            evidence = {True: "yes", False: "NO", None: "-"}[outcome.evidence_present]
            bad = sum(1 for c in outcome.checkable if not c.is_grounded)
            print(
                f"{outcome.case_id:<34}{outcome.kind:<11}{evidence:<4}"
                f"{len(outcome.checkable):>7}{bad:>5}{outcome.stance:>10}"
            )

    if candidate_verdicts:
        reference = {
            o.case_id: {c.index: c.verdict for c in o.claims}
            for o in outcomes
            if o.case_id in candidate_verdicts
        }
        agreement = pair_verdicts(reference, candidate_verdicts)
        print()
        print(f"JUDGE AGREEMENT — {args.judge_model} (reference) vs {args.compare_judge}")
        print(
            f"  claims compared: {agreement.n}   raw: {_fmt(agreement.raw)}   "
            f"on problem/not-a-problem: {_fmt(agreement.binary)}   "
            f"kappa: {_fmt(agreement.kappa)}"
        )
        print(f"  the candidate is {verdict_shift(agreement.pairs)}")
        print(
            f"  MISSED problems (candidate said fine, reference did not): "
            f"{len(agreement.false_clean)}   false alarms: {len(agreement.false_alarm)}"
        )
        for pair in agreement.false_clean:
            print(f"    {pair.case_id} claim {pair.index}: {pair.reference} -> {pair.candidate}")
        print(
            "  Read `kappa` with `claims compared`, never alone: these verdicts are "
            "heavily skewed toward supported, so a judge that stopped discriminating "
            "still scores high raw agreement."
        )

    if abort.is_set():
        print()
        print(
            "RUN ABORTED — the account ran out of credits. Every number above is "
            "measured on the cases that completed; the rest were never attempted."
        )

    if errors:
        print()
        print("ERRORS (excluded from every denominator above)")
        for err in errors:
            print(f"  {err.case_id:<34}{err.stage:<10}{err.error}")

    if args.save_generations and generations:
        save_generations(
            args.save_generations,
            generations,
            answering_model=answering,
            project_id=args.project_id,
        )
        print(f"\nwrote {args.save_generations} ({len(generations)} generations)")

    if args.json:
        payload = {
            "judge_model": args.judge_model,
            "answering_model": settings.llm_model,
            "project_id": args.project_id,
            "set": str(args.set),
            "cases": [
                {
                    **{k: v for k, v in asdict(o).items() if k != "claims"},
                    "claims": [
                        {
                            **asdict(c),
                            "supporting_papers": sorted(c.supporting_papers),
                            "marker_papers": sorted(c.marker_papers),
                        }
                        for c in o.claims
                    ],
                    "answer": generations[o.case_id].answer,
                    "chunk_count": len(generations[o.case_id].chunks),
                    "scope_source": generations[o.case_id].scope_source,
                }
                for o in outcomes
            ],
            "errors": [asdict(e) for e in errors],
        }
        args.json.write_text(json.dumps(payload, indent=2, default=list))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
