"""Retrieval eval harness — measures, never mutates.

Run:  docker compose exec -T backend python -m evals.retrieval.run_eval

The `-m` form is required: `pyproject.toml` packages only `app*`, so `evals` is
not installed and file-path invocation drops cwd from sys.path.

One query per case pulls EVERY chunk with its cosine distance; all scoring
happens in evals.retrieval.metrics, so the threshold sweep costs no extra queries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.embedding_service import EmbeddingService
from evals.retrieval.golden_set import Case, load_golden_set
from evals.retrieval.metrics import (
    THRESHOLDS,
    Scored,
    SeparationDiagnosis,
    best_satisfying_distance,
    diagnose_separation,
    first_satisfying_rank,
    leave_one_out_lo,
    mean_reciprocal_rank,
    noise_floor,
    order_statistic_risk,
    recall_at_k,
    recommended_point,
    separating_threshold,
    simulate_retrieval,
    sweep,
    topk_satisfying_distance,
)

_DEFAULT_SET = Path(__file__).parent / "golden_set.json"

# A printed separating threshold only drops the PROVISIONAL banner when
# there are enough cases on BOTH sides AND the margin clears the spread of
# the negatives that define it — see the "closed-form separating interval"
# section of main() and README.md's "Reading the output". `lo` is set by a
# single worst-case positive exactly as `hi` is set by a single closest
# negative, so the gate must check sample size on both sides, not just the
# negative one -- these numbers also match the ROBUST-finding text's own
# ">=20 positives, >=10 negatives" advice; the gate and the advice must
# agree, or the advice is telling users to reach a bar the code never checks.
_MIN_POSITIVES_FOR_CONFIDENCE = 20
_MIN_NEGATIVES_FOR_CONFIDENCE = 10

_MODEL_COUNTS_SQL = text("SELECT model, count(*) AS n FROM paper_chunk_embeddings GROUP BY model")

# Mirrors chat_service._retrieve_paper_chunks: same <=> operator, same model
# filter, same project scoping, and since 2026-08-10 the same GLOBAL top-k —
# there is no per-paper ceiling in production to diverge from.
#
# `--project-id` is REQUIRED rather than defaulting to every project. There is
# no unscoped path in production, so an unscoped run here would report numbers
# production cannot produce: under a global top-k another project's chunks
# compete for the same budget, silently displacing the ones production would
# have retrieved. That was harmless under the old per-paper ceiling, where each
# paper held its own budget. There is no sensible default project, so the flag
# is required instead of guessed.
#
# ONE divergence remains (see README.md's "What it measures vs. what production
# does"): this embeds the golden-set question verbatim, while production
# reformulates through a QueryReformulatorAgent when the conversation has prior
# turns. For the single-turn golden set the two paths agree.
_SQL = text("""
    SELECT c.paper_id AS paper_id,
           p.title AS paper_title,
           c.text AS chunk_text,
           (c.embedding <=> CAST(:qvec AS vector)) AS distance
    FROM paper_chunk_embeddings c
    JOIN papers p ON p.id = c.paper_id
    WHERE c.model = :model
      AND p.project_id = :project_id
    ORDER BY distance ASC
""")


def _vec(embedding: list[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"


def _positive_int(raw: str) -> int:
    """argparse type for --k: must be a positive integer.

    `simulate_retrieval`'s `[:k]` silently accepts k <= 0 (an empty result),
    which would make every case an automatic miss and read as a retrieval
    failure instead of the usage error it actually is.
    """
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError(f"--k must be >= 1, got {value}")
    return value


def _model_mismatch_message(counts: dict[str, int], target_model: str) -> str | None:
    """None if `target_model` has at least one chunk (or the corpus is
    entirely empty — the per-case "corpus: 0 chunks" note already covers
    that clearly enough on its own). Otherwise a message naming which
    model(s) DO have chunks, so a real embedding-model/index mismatch reads
    as what it is instead of misdirecting every single case to "no paper
    matching ..." — the papers are there; the configured model's embeddings
    are not.
    """
    if not counts or counts.get(target_model, 0) > 0:
        return None
    other = ", ".join(f"{m!r} ({n})" for m, n in sorted(counts.items()))
    return (
        f"no chunk embeddings found for model {target_model!r}. Papers exist; "
        f"embeddings exist only for: {other}. Check EMBEDDING_MODEL, or re-index "
        "the library for this model."
    )


def _positive_case_status(case: Case, chunks: list[Scored]) -> str | None:
    """None if `case` is winnable in this corpus; otherwise a reason to route
    it to `errors` instead of `positives`.

    Both causes here are golden-set problems, not retrieval failures: either
    the paper itself is gone (drift), or — subtler — the paper is still
    there but no chunk in the ENTIRE corpus contains the expected
    substring(s). `best_satisfying_distance` returning None proves that
    outright: `chunks` already holds every chunk in the corpus for this
    query (the SQL has no LIMIT), so there is nowhere else to look. Scoring
    that case as a plain retrieval miss would silently drag recall down for
    a golden-set defect instead of flagging it.
    """
    if not any(case.paper_title_contains.lower() in c.paper_title.lower() for c in chunks):
        return f"no paper matching {case.paper_title_contains!r}"
    if best_satisfying_distance(case, chunks) is None:
        return (
            f"paper matching {case.paper_title_contains!r} found, but no chunk in the "
            f"corpus contains {list(case.expect_substrings)!r}"
        )
    return None


def _is_provisional(n_pos: int, n_neg: int, margin: float, neg_spread: float) -> bool:
    """Whether a found separating interval must print PROVISIONAL rather
    than an unqualified SEPARATION FOUND.

    Gates on sample size on BOTH sides, not just negatives: `lo` is set by a
    single worst-case positive exactly as `hi` is set by a single closest
    negative, so a golden set with plenty of negatives but only 1-2
    positives is exactly as fragile as one with plenty of positives but too
    few negatives — an unqualified verdict from either shape rests on a
    single witness. `_MIN_POSITIVES_FOR_CONFIDENCE` /
    `_MIN_NEGATIVES_FOR_CONFIDENCE` also match the numbers the ROBUST-finding
    text recommends widening to; the gate and the advice must agree, or the
    advice is telling users to reach a bar the code doesn't check.
    """
    return not (
        n_pos >= _MIN_POSITIVES_FOR_CONFIDENCE
        and n_neg >= _MIN_NEGATIVES_FOR_CONFIDENCE
        and margin > neg_spread
    )


def _off_topic_acceptance_rate(negatives: list[list[Scored]], threshold: float) -> float | None:
    """Fraction of off_topic cases where at least one retrieved chunk beats
    `threshold` — the ROBUST FINDING's number.

    None when there is nothing to measure: no off_topic cases at all, or
    every one of them returned zero chunks. `bool(negatives)` is NOT a safe
    test for that: the shape `[[], [], []]` (one empty list per off_topic
    case, e.g. from an empty `paper_chunk_embeddings` table) is truthy, so a
    naive `if negatives:` would compute `0 accepted / 3 = 0.0` and report
    "accepts 0% of off-topic questions" — the exact INVERSE of the true,
    unmeasured situation. Use `any(negatives)`, exactly like `usable_negatives`
    in `main()` and `sweep`'s own guard in metrics.py — this is the third time
    this precise shape has produced a false-confident number on this branch.
    Do not test `negatives` truthiness directly anywhere else in this module;
    always go through `any(negatives)` (or this function).
    """
    if not any(negatives):
        return None
    accepted = sum(1 for chunks in negatives if any(c.distance < threshold for c in chunks))
    return accepted / len(negatives)


def _display_point(lo: float, hi: float) -> str:
    """Recommended midpoint of (lo, hi], formatted for display — or the raw
    bounds when the interval is too narrow for `recommended_point` to find a
    rounded value that still lies inside it (see its docstring: a measured
    possibility on a narrow interval, not a bug). The ValueError it raises is
    correct to raise from a pure metrics function, but must never propagate
    out of `main()`: uncaught, it would take out the ROBUST FINDING section
    printed after it, and — with --json — the whole file dump, over a display
    nicety.
    """
    try:
        return f"{recommended_point(lo, hi):.4f}"
    except ValueError:
        return f"no display value fits — raw bounds ({lo:.6f}, {hi:.6f}]"


async def _chunks_for(db, svc: EmbeddingService, case: Case, project_id: str) -> list[Scored]:
    embedding = await svc.embed(case.question, task_type="RETRIEVAL_QUERY")
    params = {
        "qvec": _vec(embedding),
        "model": settings.embedding_model,
        "project_id": project_id,
    }
    rows = (await db.execute(_SQL, params)).fetchall()
    return [
        Scored(
            paper_id=r.paper_id,
            paper_title=r.paper_title,
            chunk_text=r.chunk_text,
            distance=float(r.distance),
        )
        for r in rows
    ]


def _corpus_note(chunks: list[Scored]) -> str:
    # Grouped by paper_id, not title: two papers could in principle share a
    # title (e.g. both title-less), which would understate the paper count.
    papers = len({c.paper_id for c in chunks})
    return f"{len(chunks)} chunks across {papers} papers"


async def main() -> None:  # noqa: PLR0912, PLR0915 — a report script, not a library API
    parser = argparse.ArgumentParser(description="Measure retrieval quality.")
    parser.add_argument(
        "--k",
        type=_positive_int,
        default=settings.max_context_chunks,
        help="total chunks retrieved globally (default: max_context_chunks)",
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help=(
            "scope the corpus to one project's papers, matching production's own "
            "scoping. Required: production has no unscoped path, so an unscoped "
            "run would measure a corpus production can never assemble"
        ),
    )
    parser.add_argument("--set", type=Path, default=_DEFAULT_SET)
    parser.add_argument("--json", type=Path, default=None, help="also dump results here")
    args = parser.parse_args()

    cases = load_golden_set(args.set)
    svc = EmbeddingService()

    positives: list[tuple[Case, list[Scored]]] = []
    negatives: list[list[Scored]] = []
    negative_ids: list[str] = []
    errors: list[str] = []
    per_case: list[dict] = []

    async with SessionLocal() as db:
        counts = {r.model: r.n for r in (await db.execute(_MODEL_COUNTS_SQL)).fetchall()}
        mismatch = _model_mismatch_message(counts, settings.embedding_model)
        if mismatch:
            print(f"\nERROR: {mismatch}")
            return

        corpus_note = ""
        for case in cases:
            chunks = await _chunks_for(db, svc, case, args.project_id)
            if not corpus_note:
                corpus_note = _corpus_note(chunks)

            if case.is_negative:
                negatives.append(chunks)
                negative_ids.append(case.id)
                best = min((c.distance for c in chunks), default=None)
                per_case.append({"id": case.id, "kind": case.kind, "best_distance": best})
                continue

            status = _positive_case_status(case, chunks)
            if status is not None:
                errors.append(f"{case.id}: {status}")
                continue

            positives.append((case, chunks))
            rank = first_satisfying_rank(case, simulate_retrieval(chunks, args.k))
            topk_distance = topk_satisfying_distance(case, chunks, args.k)
            per_case.append(
                {
                    "id": case.id,
                    "kind": case.kind,
                    "rank": rank,
                    # Globally-nearest satisfying chunk in the WHOLE corpus,
                    # ignoring top-k — NOT top-k-aware. Invites the wrong "so
                    # lower the threshold" inference; see README.md's
                    # "Reading the output".
                    "best_distance": best_satisfying_distance(case, chunks),
                    # Top-k-aware: the distance of the satisfying chunk that
                    # actually survives this case's own top-k, or
                    # None when it doesn't (see `blocked` and
                    # metrics.topk_satisfying_distance). This is the number
                    # the closed-form separation actually uses.
                    "topk_distance": topk_distance,
                    "blocked": topk_distance is None,
                }
            )

    print(
        f"\ncorpus: {corpus_note}   model: {settings.embedding_model}   k={args.k}   "
        f"project: {args.project_id}"
    )
    print("(small, thematically clustered corpus — numbers are indicative, not conclusive)")
    print(
        "(recall/MRR below apply no distance cutoff — they are the retrieval "
        "ceiling, not production recall. k here is a GLOBAL budget, matching "
        f"production's max_context_chunks of {settings.max_context_chunks}.)"
    )

    if errors:
        print("ERRORS (golden-set problems, not retrieval failures):")
        for err in errors:
            print(f"  ! {err}")
        print()

    # recall_at_k / mean_reciprocal_rank raise ValueError on an empty case
    # list. That happens for real when every positive case hit a golden-set
    # problem above — report it plainly instead of letting the ValueError
    # propagate.
    if positives:
        print(
            f"recall@{args.k}: {recall_at_k(positives, args.k):.2f}    "
            f"MRR: {mean_reciprocal_rank(positives, args.k):.3f}"
        )
    else:
        print(f"recall@{args.k}: n/a    MRR: n/a")
        print(
            "  No positive cases could be scored — every content/metadata/figure case "
            "hit a golden-set problem (see ERRORS above)."
        )
    floor = noise_floor(negatives)
    print(f"noise floor (best off-topic distance): {floor if floor is None else round(floor, 4)}\n")

    # Column width derived from the actual ids so a long case id can never
    # collide with the next column (a fixed 28-char width collided in
    # review with a 29-char synthetic id).
    id_col = max(28, max((len(row["id"]) for row in per_case), default=28) + 1)
    print(f"{'case':<{id_col}}{'kind':<10}{'rank':>6}{'best_dist':>12}")
    for row in per_case:
        rank = row.get("rank")
        dist = row.get("best_distance")
        print(
            f"{row['id']:<{id_col}}{row['kind']:<10}"
            f"{'-' if rank is None else rank:>6}"
            f"{'-' if dist is None else round(dist, 4):>12}"
        )

    usable_negatives = any(negatives)

    # --- grid sweep: coarse visual aid / cross-check, NOT the headline ----
    print(
        f"\nthreshold sweep grid (k={args.k}, step 0.05 — cross-check only, see closed form below):"
    )
    rows: list = []
    if not usable_negatives:
        print("  skipped — no usable off_topic cases")
    elif not positives:
        print("  skipped — no positive cases scored")
    else:
        print(f"{'threshold':>10}{'content_recall':>16}{'offtopic_accept':>18}")
        rows = sweep(positives, negatives, args.k, THRESHOLDS)
        for row in rows:
            print(
                f"{row.threshold:>10.2f}{row.content_recall:>16.2f}{row.off_topic_false_accept:>18.2f}"
            )
        grid_best = separating_threshold(rows)
        if grid_best is None:
            print(
                "  grid cross-check: no row in the swept grid achieves separation (the grid "
                "is coarse — a real interval can fall entirely between two grid points; see "
                "the closed form below for the exact answer)"
            )
        else:
            print(
                f"  grid cross-check: grid found threshold {grid_best:.2f} (coarse; see the "
                "closed form below for the exact interval)"
            )

    # --- closed form: the headline, exact, authoritative -------------------
    # Populated only in the "both sides present" branch below; stays None
    # (and so does the "separation" key in --json) when there was nothing to
    # diagnose — same condition the printed NO RECOMMENDATION branches use.
    diagnosis: SeparationDiagnosis | None = None
    print(f"\nclosed-form separating interval (k={args.k}, exact):")
    if not usable_negatives:
        print(
            "  NO RECOMMENDATION: the golden set has no usable off_topic cases (either "
            "there are none, or every off_topic case returned zero chunks), so recall is "
            "trivially maximised by retrieving everything. Add negatives."
        )
    elif not positives:
        print(
            "  NO RECOMMENDATION: no positive cases were scored (see ERRORS above), so "
            "content recall cannot be measured at any threshold."
        )
    else:
        diagnosis = diagnose_separation(positives, list(zip(negative_ids, negatives)), args.k)
        if diagnosis.blocked_case_ids:
            print("  NO SEPARATION POSSIBLE AT THIS k.")
            print(
                f"  case(s) with no satisfying chunk within their own top-{args.k}: "
                f"{', '.join(diagnosis.blocked_case_ids)}"
            )
            print(
                "  No threshold recovers this: thresholding only removes competing chunks, "
                "it can never let a chunk leapfrog nearer competitors that already excluded "
                "it. Increase --k or fix retrieval ranking — not the threshold."
            )
        elif diagnosis.interval is None:
            print("  NO THRESHOLD SEPARATES CONTENT FROM NOISE (exact — not a grid artifact).")
            print(
                f"  worst content distance {diagnosis.lo:.4f} ({diagnosis.lo_case_id})  >=  "
                f"closest off-topic {diagnosis.hi:.4f} ({diagnosis.hi_case_id})"
            )
            print(
                "  An absolute cosine cutoff cannot work for this model on this corpus — the "
                "next lever is reranking or hybrid retrieval, not a better constant."
            )
        else:
            lo, hi = diagnosis.interval.lo, diagnosis.interval.hi
            margin = hi - lo
            point_display = _display_point(lo, hi)
            n_pos, n_neg = len(positives), len(negatives)
            neg_bests = [min(c.distance for c in chunks) for chunks in negatives if chunks]
            neg_spread = (max(neg_bests) - min(neg_bests)) if len(neg_bests) > 1 else 0.0
            provisional = _is_provisional(n_pos, n_neg, margin, neg_spread)

            header = "SEPARATION FOUND"
            if provisional:
                header += " — PROVISIONAL, DO NOT SHIP"
            print(f"  {header}")
            print(
                f"    worst content distance   {lo:.4f}   ({diagnosis.lo_case_id})   "
                f"n={n_pos} positives"
            )
            print(
                f"    closest off-topic        {hi:.4f}   ({diagnosis.hi_case_id})   "
                f"n={n_neg} negatives"
            )
            print(
                f"    any T in ({lo:.4f}, {hi:.4f}] separates; midpoint {point_display}, "
                f"margin {margin:.4f}"
            )
            if args.k != settings.max_context_chunks:
                print(
                    f"    NOTE: this recommendation is at --k {args.k}. Production's "
                    f"global budget is max_context_chunks = {settings.max_context_chunks} "
                    "— this may describe an operating point production never reaches."
                )

            if provisional:
                print()
                alt = leave_one_out_lo(positives, excluding=diagnosis.lo_case_id, k=args.k)
                print("    This interval is set by ONE positive and ONE negative case.")
                if alt is None:
                    print(
                        f"    (Dropping {diagnosis.lo_case_id} leaves too few positives to "
                        "check what the interval would become.)"
                    )
                elif alt < hi:
                    print(
                        f"    Drop {diagnosis.lo_case_id} and it becomes {_display_point(alt, hi)}."
                    )
                else:
                    print(
                        f"    Drop {diagnosis.lo_case_id} and separation no longer holds at "
                        f"all (next-worst distance {alt:.4f} >= {hi:.4f})."
                    )
                p_pos, p_neg, p_either = order_statistic_risk(n_pos, n_neg)
                print(
                    f"    On order statistics alone, one more off-topic question (making "
                    f"{n_neg + 1}) has a 1-in-{n_neg + 1} chance of falling below {hi:.4f}, and "
                    f"one more positive (making {n_pos + 1}) has a 1-in-{n_pos + 1} chance of "
                    f"exceeding {lo:.4f} — together ~{p_either:.0%} odds the recommendation is "
                    "already dead if the set grows by one of each."
                )
                print(
                    f"    Treat {point_display} as a hypothesis to test, not a value to configure."
                )

    # --- robust finding: survives any resampling, printed unconditionally --
    current = settings.similarity_threshold
    rate = _off_topic_acceptance_rate(negatives, current)
    if rate is not None:
        print(
            f"\n  ROBUST FINDING: the current similarity_threshold ({current:g}) accepts "
            f"{rate:.0%} of off-topic questions in this set."
        )
    print(
        "  Widen the set (>=20 positives, >=10 negatives, negatives drawn from NEAR-domain "
        "topics) before trusting any specific number."
    )

    if args.json:
        separation = (
            None
            if diagnosis is None
            else {
                "lo": diagnosis.lo,
                "hi": diagnosis.hi,
                "lo_case_id": diagnosis.lo_case_id,
                "hi_case_id": diagnosis.hi_case_id,
                "blocked_case_ids": list(diagnosis.blocked_case_ids),
                "k": args.k,
            }
        )
        args.json.write_text(
            json.dumps(
                {
                    "model": settings.embedding_model,
                    "k": args.k,
                    "project_id": args.project_id,
                    "corpus": corpus_note,
                    "cases": per_case,
                    "errors": errors,
                    "sweep": {
                        "note": (
                            "coarse cross-check only, NOT the headline — a real separating "
                            "interval can fall entirely between two grid points; see "
                            "'separation' for the exact closed-form answer"
                        ),
                        "rows": [vars(r) for r in rows],
                    },
                    "separation": separation,
                },
                indent=2,
            )
        )
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
