"""The Langfuse export of a groundedness run (`run_eval --langfuse`)."""

import asyncio
import json
from datetime import datetime

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core import observability
from evals.groundedness import langfuse_export as lx
from evals.groundedness.metrics import CaseOutcome, ScoredClaim
from evals.groundedness.relevance import RelevanceOutcome
from evals.groundedness.run_eval import CaseError, Experiment, _traced_case
from evals.retrieval.golden_set import Case

POSITIVE = Case(
    id="revisit-time",
    kind="content",
    question="What does minimizing revisit time achieve?",
    paper_title_contains="Joint Optimization",
    expect_substrings=("revisit",),
)
NEGATIVE = Case(
    id="offtopic-cake",
    kind="off_topic",
    question="How do I bake a cake?",
    paper_title_contains=None,
    expect_substrings=(),
)


def _claim(verdict, markers=(1,), paper="p1"):
    return ScoredClaim(
        index=0,
        verdict=verdict,
        markers=markers,
        supporting_excerpts=(1,) if verdict == "supported" else (),
        supporting_papers=frozenset({paper}) if verdict == "supported" else frozenset(),
        marker_papers=frozenset({paper}),
    )


def _scores(scores):
    return {s.name: (s.value, s.data_type) for s in scores}


# -- dataset ----------------------------------------------------------------


def test_a_positive_item_carries_the_expected_evidence():
    item = lx.dataset_item(POSITIVE)
    assert item["datasetName"] == lx.DATASET_NAME
    assert item["id"] == "groundedness-revisit-time"
    assert item["input"] == {"question": POSITIVE.question}
    assert item["expectedOutput"] == {
        "paper_title_contains": "Joint Optimization",
        "expect_substrings": ["revisit"],
    }
    assert item["metadata"] == {"case_id": "revisit-time", "kind": "content"}


def test_an_off_topic_item_expects_a_refusal():
    assert lx.dataset_item(NEGATIVE)["expectedOutput"] == {"refuse": True}


# -- scores -----------------------------------------------------------------


def test_a_positive_case_is_scored_with_the_harness_own_metrics():
    outcome = CaseOutcome(
        case_id=POSITIVE.id,
        kind="content",
        claims=(_claim("supported"), _claim("unsupported", markers=())),
        stance="answered",
        evidence_present=True,
        marker_total=1,
    )
    scores = _scores(lx.case_scores(outcome, None))
    assert scores["support"] == (0.5, "NUMERIC")
    assert scores["hallucination"] == (0.5, "NUMERIC")
    assert scores["citation_coverage"] == (1.0, "NUMERIC")
    assert scores["claims"] == (2.0, "NUMERIC")
    assert scores["clean"] == (0.0, "BOOLEAN")
    assert scores["refused"] == (0.0, "BOOLEAN")
    assert scores["evidence_present"] == (1.0, "BOOLEAN")
    assert "answer_relevance" not in scores


def test_a_refusal_posts_no_rate_it_has_no_denominator_for():
    outcome = CaseOutcome(
        case_id=NEGATIVE.id,
        kind="off_topic",
        claims=(),
        stance="refused",
        evidence_present=None,
        marker_total=0,
    )
    scores = _scores(lx.case_scores(outcome, None))
    # Never a 0 standing in for "not measured".
    assert "support" not in scores and "citation_precision" not in scores
    assert "evidence_present" not in scores
    assert scores["refused"] == (1.0, "BOOLEAN")
    assert scores["clean"] == (1.0, "BOOLEAN")


def test_relevance_scores_are_added_only_when_measured():
    outcome = CaseOutcome(POSITIVE.id, "content", (), "answered", True, 0)
    measured = RelevanceOutcome(
        case_id=POSITIVE.id,
        kind="content",
        answer_relevance=0.75,
        answer_relevance_reason="",
        shown=(1, 2, 3, 4),
        relevant=frozenset({1, 2}),
    )
    scores = _scores(lx.case_scores(outcome, measured))
    assert scores["answer_relevance"] == (0.75, "NUMERIC")
    assert scores["context_precision"] == (0.5, "NUMERIC")
    assert scores["context_precision_at_10"] == (0.5, "NUMERIC")

    unmeasured = RelevanceOutcome(POSITIVE.id, "content", None, "", (1, 2), None)
    assert not {"answer_relevance", "context_precision"} & set(
        _scores(lx.case_scores(outcome, unmeasured))
    )


def test_the_default_run_name_says_who_answered_and_who_judged():
    name = lx.default_run_name(
        answering="gpt-5-mini", judge="gpt-4.1-mini", now=datetime(2026, 9, 24, 17, 5)
    )
    assert name == "gpt-5-mini judged by gpt-4.1-mini · 2026-09-24 17:05Z"


# -- span attributes --------------------------------------------------------


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(lx.ExperimentSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observability.install_provider(provider)
    yield exporter
    observability.install_provider(None)


def _experiment():
    return Experiment(
        experiment_id="abcd1234abcd1234",
        run_name="run-1",
        dataset_id="ds-1",
        metadata={"answering_model": "gpt-5-mini"},
    )


def _run(case, run_case, experiment):
    refs = {}
    result = asyncio.run(
        _traced_case(
            case=case,
            semaphore=asyncio.Semaphore(1),
            run_case=run_case,
            experiment=experiment,
            refs=refs,
        )
    )
    return result, refs


def test_every_span_of_a_case_nests_under_its_root_and_carries_the_experiment(spans):
    async def run_case():
        with observability.span("judge", kind="generation"):
            pass
        return CaseError(POSITIVE.id, "judge", "boom")

    _, refs = _run(POSITIVE, run_case, _experiment())
    by_name = {s.name: s for s in spans.get_finished_spans()}
    root, child = by_name["eval.case"], by_name["judge"]

    assert child.context.trace_id == root.context.trace_id
    assert child.parent.span_id == root.context.span_id
    ref = refs[POSITIVE.id]
    assert ref.trace_id == format(root.context.trace_id, "032x")
    assert ref.observation_id == format(root.context.span_id, "016x")

    for span in (root, child):
        attrs = span.attributes
        assert attrs["langfuse.experiment.id"] == "abcd1234abcd1234"
        assert attrs["langfuse.experiment.name"] == "run-1"
        assert attrs["langfuse.experiment.dataset.id"] == "ds-1"
        assert attrs["langfuse.experiment.item.id"] == "groundedness-revisit-time"
        assert attrs["langfuse.experiment.item.root_observation_id"] == ref.observation_id
        assert attrs["langfuse.experiment.metadata.answering_model"] == "gpt-5-mini"
        # Overrides the environment production code stamps on its own spans.
        assert attrs["langfuse.environment"] == lx.ENVIRONMENT

    assert json.loads(root.attributes[lx.ITEM_EXPECTED_OUTPUT])["expect_substrings"] == ["revisit"]
    assert root.attributes["langfuse.trace.name"] == lx.TRACE_NAME
    assert root.attributes["langfuse.observation.level"] == "ERROR"


def test_without_an_experiment_the_case_is_still_traced_but_never_stamped(spans):
    async def run_case():
        with observability.span("judge", kind="generation"):
            pass
        return CaseError(NEGATIVE.id, "skipped", "aborted")

    _run(NEGATIVE, run_case, None)
    for span in spans.get_finished_spans():
        assert not any(k.startswith("langfuse.experiment") for k in span.attributes)
    root = {s.name: s for s in spans.get_finished_spans()}["eval.case"]
    assert root.attributes["langfuse.observation.level"] == "WARNING"


def test_spans_opened_after_the_case_are_not_stamped(spans):
    async def run_case():
        return CaseError(POSITIVE.id, "judge", "boom")

    async def both():
        await _traced_case(
            case=POSITIVE,
            semaphore=asyncio.Semaphore(1),
            run_case=run_case,
            experiment=_experiment(),
            refs={},
        )
        with observability.span("after"):
            pass

    asyncio.run(both())
    after = {s.name: s for s in spans.get_finished_spans()}["after"]
    assert "langfuse.experiment.id" not in after.attributes


# -- REST -------------------------------------------------------------------


def _exporter(handler):
    return lx.LangfuseExporter(
        host="https://lf.example/",
        public_key="pk",
        secret_key="sk",
        transport=httpx.MockTransport(handler),
    )


def test_the_dataset_is_created_on_first_use_and_found_after():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(404, json={"message": "not found"})
        assert json.loads(request.content)["name"] == lx.DATASET_NAME
        return httpx.Response(200, json={"id": "ds-new"})

    async def go():
        exporter = _exporter(handler)
        try:
            return await exporter.ensure_dataset()
        finally:
            await exporter.aclose()

    assert asyncio.run(go()) == "ds-new"
    assert calls == [
        ("GET", f"/api/public/v2/datasets/{lx.DATASET_NAME}"),
        ("POST", "/api/public/v2/datasets"),
    ]


def test_a_dataset_setup_failure_is_raised_before_any_run():
    async def go():
        exporter = _exporter(lambda request: httpx.Response(401, json={"message": "no"}))
        try:
            await exporter.ensure_dataset()
        finally:
            await exporter.aclose()

    with pytest.raises(lx.LangfuseExportError):
        asyncio.run(go())


def _case_scores(case_id="c1", names=("support", "clean")):
    return lx.CaseScores(
        case_id=case_id,
        trace_id="t" * 32,
        observation_id="o" * 16,
        scores=[lx.Score(name, 1.0, "NUMERIC") for name in names],
    )


def _post(handler, cases, experiment_id="e1"):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    async def go():
        exporter = lx.LangfuseExporter(
            host="https://lf.example/",
            public_key="pk",
            secret_key="sk",
            transport=httpx.MockTransport(handler),
            sleep=sleep,
        )
        try:
            await exporter.post_scores(experiment_id=experiment_id, cases=cases)
        finally:
            await exporter.aclose()
        return exporter

    return asyncio.run(go()), sleeps


def test_scores_go_through_batched_ingestion_with_stable_ids():
    batches = []

    def handler(request):
        assert request.url.path == "/api/public/ingestion"
        batch = json.loads(request.content)["batch"]
        batches.append(batch)
        # One per-event failure, the way the endpoint reports it: a 207.
        return httpx.Response(
            207,
            json={
                "successes": [{"id": e["id"], "status": 201} for e in batch[1:]],
                "errors": [{"id": batch[0]["id"], "status": 400, "message": "bad"}],
            },
        )

    exporter, _ = _post(handler, [_case_scores()])
    (batch,) = batches
    assert [e["type"] for e in batch] == ["score-create", "score-create"]
    assert batch[0]["body"] == {
        "id": "e1-c1-support",
        "traceId": "t" * 32,
        "observationId": "o" * 16,
        "name": "support",
        "value": 1.0,
        "dataType": "NUMERIC",
        "environment": lx.ENVIRONMENT,
    }
    assert exporter.scores_posted == 1
    assert exporter.score_failures == ["c1/support: 400 bad"]


def test_a_run_s_scores_are_split_into_batches():
    sizes = []

    def handler(request):
        batch = json.loads(request.content)["batch"]
        sizes.append(len(batch))
        return httpx.Response(207, json={"successes": [{"id": e["id"]} for e in batch]})

    names = [f"s{i}" for i in range(lx.SCORE_BATCH_SIZE + 5)]
    exporter, _ = _post(handler, [_case_scores(names=names)])
    assert sizes == [lx.SCORE_BATCH_SIZE, 5]
    assert exporter.scores_posted == lx.SCORE_BATCH_SIZE + 5


def test_a_429_is_retried_after_the_interval_the_server_names():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                429, json={"message": "Rate limit exceeded", "details": {"retryAfterSeconds": 57}}
            )
        batch = json.loads(request.content)["batch"]
        return httpx.Response(207, json={"successes": [{"id": e["id"]} for e in batch]})

    exporter, sleeps = _post(handler, [_case_scores()])
    assert sleeps == [58.0]
    assert exporter.scores_posted == 2 and not exporter.score_failures


def test_a_failed_batch_counts_every_score_in_it():
    exporter, _ = _post(lambda request: httpx.Response(500, text="oops"), [_case_scores()])
    assert exporter.scores_posted == 0
    assert [f.split(":")[0] for f in exporter.score_failures] == ["c1/support", "c1/clean"]
