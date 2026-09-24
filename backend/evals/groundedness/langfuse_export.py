"""Export a groundedness run to Langfuse as an EXPERIMENT. Opt-in: `--langfuse`.

What lands in Langfuse, per run:

- the golden set as a dataset (`DATASET_NAME`), one item per case, upserted on
  every exporting run so the dataset tracks `golden_set.json`
- one trace per case (`eval.case`), with the case's retrieval, rerank, answer
  and judge calls nested under it -- the same spans a chat turn produces, so
  latency and cost read the same way they do for `chat.turn`
- every span of that trace stamped with the experiment attributes below, which
  is what makes Langfuse file the traces under the dataset as one run
- per-case scores on the case's root observation

WHY SPAN ATTRIBUTES AND NOT `POST /api/public/dataset-run-items`. That endpoint
is deprecated on Langfuse Cloud and removed on 2026-11-16; v4 builds experiment
data from `langfuse.experiment.*` attributes arriving over OTLP. The names are
Langfuse's own (`langfuse/_client/attributes.py` in their Python SDK), and so is
the propagation rule this module reproduces: every span of an item carries the
attributes, and the environment is `sdk-experiment`, which also keeps eval
traffic out of the dev chat numbers.

THE REPORT STAYS THE SOURCE OF TRUTH. The harness pools support over CLAIMS and
splits every rate by whether the evidence reached the model; a per-case score
averaged in Langfuse's UI weights a two-claim answer like a fifteen-claim one.
These scores are for drill-down and run-to-run comparison, not for the numbers
recorded in the README. Per-case values come from `metrics.py`'s own functions
over a one-case list, so there is no second definition of any metric here.

SCORES GO THROUGH THE BATCHED INGESTION ENDPOINT, not `POST /scores`. The
single-score endpoint sits in Langfuse's general API bucket, 30 requests a
minute on Hobby; a full run is ~480 scores, and the first full export
(2026-09-24) lost 449 of them to 429s. `/api/public/ingestion` takes
`score-create` events in batches under the 1,000/minute ingestion bucket, so a
whole run is a handful of requests. A 429 on any call is retried after the
`retryAfterSeconds` the body names.

FAILURES. Dataset setup runs before any model call and aborts the run: an
export the user asked for that cannot happen should not be discovered after
paying for the answers. A score that fails to post is counted and reported,
never raised -- the report has already printed by then and must not be lost.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from collections.abc import Iterable, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from opentelemetry.sdk.trace import SpanProcessor

from evals.groundedness.metrics import (
    CaseOutcome,
    citation_coverage,
    citation_precision,
    claim_support_rate,
    hallucinated_claim_rate,
)
from evals.groundedness.relevance import RelevanceOutcome, context_precision
from evals.retrieval.golden_set import Case

DATASET_NAME = "groundedness-golden-set"
TRACE_NAME = "eval.groundedness"
# What Langfuse's own experiment runner stamps on experiment spans.
ENVIRONMENT = "sdk-experiment"

_EXPERIMENT_ID = "langfuse.experiment.id"
_EXPERIMENT_NAME = "langfuse.experiment.name"
_EXPERIMENT_METADATA = "langfuse.experiment.metadata"
_EXPERIMENT_DATASET_ID = "langfuse.experiment.dataset.id"
_ITEM_ID = "langfuse.experiment.item.id"
_ITEM_METADATA = "langfuse.experiment.item.metadata"
_ITEM_ROOT_OBSERVATION_ID = "langfuse.experiment.item.root_observation_id"
ITEM_EXPECTED_OUTPUT = "langfuse.experiment.item.expected_output"


# -- dataset ----------------------------------------------------------------


def item_id(case: Case) -> str:
    """Dataset item ids are unique per Langfuse PROJECT, not per dataset, so
    the bare case id is prefixed with the harness it belongs to."""
    return f"groundedness-{case.id}"


def expected_output(case: Case) -> dict:
    if case.is_negative:
        return {"refuse": True}
    return {
        "paper_title_contains": case.paper_title_contains,
        "expect_substrings": list(case.expect_substrings),
    }


def dataset_item(case: Case) -> dict:
    return {
        "datasetName": DATASET_NAME,
        "id": item_id(case),
        "input": {"question": case.question},
        "expectedOutput": expected_output(case),
        "metadata": {"case_id": case.id, "kind": case.kind},
    }


# -- scores -----------------------------------------------------------------


@dataclass(frozen=True)
class Score:
    name: str
    value: float
    data_type: str  # "NUMERIC" | "BOOLEAN"


def case_scores(outcome: CaseOutcome, relevance: RelevanceOutcome | None) -> list[Score]:
    """One case's scores. A rate with no denominator (a refusal has no
    checkable claim) is omitted, never posted as 0 -- the same rule `--csv`
    follows for an unmeasured cell."""
    one = [outcome]
    scores: list[Score] = []

    def numeric(name: str, value: float | None) -> None:
        if value is not None:
            scores.append(Score(name, float(value), "NUMERIC"))

    def boolean(name: str, value: bool) -> None:
        scores.append(Score(name, 1.0 if value else 0.0, "BOOLEAN"))

    numeric("support", claim_support_rate(one))
    numeric("hallucination", hallucinated_claim_rate(one))
    numeric("citation_precision", citation_precision(one))
    numeric("citation_coverage", citation_coverage(one))
    numeric("claims", len(outcome.checkable))
    boolean("clean", outcome.is_clean)
    # Read per kind, as the report does: a refusal is the target on an
    # off_topic case and a failure on a positive.
    boolean("refused", outcome.stance == "refused")
    if outcome.evidence_present is not None:
        boolean("evidence_present", outcome.evidence_present)
    if relevance is not None:
        numeric("answer_relevance", relevance.answer_relevance)
        if relevance.relevant is not None:
            numeric("context_precision", context_precision([relevance]))
            numeric("context_precision_at_10", context_precision([relevance], k=10))
    return scores


# -- experiment span attributes ---------------------------------------------


def new_experiment_id() -> str:
    """16 hex characters, the shape Langfuse's runner gives an experiment id."""
    return secrets.token_hex(8)


def default_run_name(*, answering: str, judge: str, now: datetime) -> str:
    return f"{answering} judged by {judge} · {now:%Y-%m-%d %H:%M}Z"


def experiment_attributes(
    *,
    experiment_id: str,
    run_name: str,
    dataset_id: str,
    case: Case,
    root_observation_id: str,
    run_metadata: dict[str, str],
) -> dict[str, str]:
    """The attributes every span of one case carries. Metadata values are
    strings: Langfuse reads them as flat `…metadata.<key>` attributes."""
    attrs = {
        _EXPERIMENT_ID: experiment_id,
        _EXPERIMENT_NAME: run_name,
        _EXPERIMENT_DATASET_ID: dataset_id,
        _ITEM_ID: item_id(case),
        _ITEM_ROOT_OBSERVATION_ID: root_observation_id,
        f"{_ITEM_METADATA}.case_id": case.id,
        f"{_ITEM_METADATA}.kind": case.kind,
        "langfuse.environment": ENVIRONMENT,
    }
    for key, value in run_metadata.items():
        attrs[f"{_EXPERIMENT_METADATA}.{key}"] = str(value)
    return attrs


_CURRENT: ContextVar[dict[str, str] | None] = ContextVar("eval_experiment_attrs", default=None)


def enter_case(attrs: dict[str, str]) -> Token:
    """Stamp every span started from here in this task with `attrs`. Each case
    runs in its own asyncio task, so the value never leaks into another case."""
    return _CURRENT.set(attrs)


def exit_case(token: Token) -> None:
    _CURRENT.reset(token)


class ExperimentSpanProcessor(SpanProcessor):
    """Copies the current case's experiment attributes onto every new span.

    The spans under a case are opened by production code (the LLM client, the
    embedding service, the reranker) that knows nothing about experiments, so
    the attributes are applied at span START rather than threaded through
    their signatures. `on_start` runs after the span's own attributes are set,
    which is what lets `langfuse.environment` be overridden here.
    """

    def on_start(self, span, parent_context=None) -> None:
        attrs = _CURRENT.get()
        if attrs:
            span.set_attributes(attrs)

    def on_end(self, span) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


# -- REST -------------------------------------------------------------------


class LangfuseExportError(RuntimeError):
    """The dataset could not be prepared, so no run should start."""


# Events per ingestion request. Well under the endpoint's 3.5MB body limit: a
# score event is a few hundred bytes.
SCORE_BATCH_SIZE = 100
# 429 retries per request, each after the interval the server names.
_MAX_RATE_LIMIT_RETRIES = 5


@dataclass(frozen=True)
class CaseScores:
    """One case's scores and the trace they attach to."""

    case_id: str
    trace_id: str
    observation_id: str
    scores: Sequence[Score]


def score_id(experiment_id: str, case_id: str, name: str) -> str:
    """Deterministic, so re-posting a run's scores updates them instead of
    adding duplicates."""
    return f"{experiment_id}-{case_id}-{name}"


def score_event(experiment_id: str, case: CaseScores, score: Score, now: datetime) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        "type": "score-create",
        "body": {
            "id": score_id(experiment_id, case.case_id, score.name),
            "traceId": case.trace_id,
            "observationId": case.observation_id,
            "name": score.name,
            "value": score.value,
            "dataType": score.data_type,
            "environment": ENVIRONMENT,
        },
    }


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.json()["details"]["retryAfterSeconds"]) + 1
    except (ValueError, KeyError, TypeError):
        return 60.0


class LangfuseExporter:
    """The REST calls OTLP cannot make: dataset, items, scores."""

    def __init__(
        self,
        *,
        host: str,
        public_key: str,
        secret_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=host.rstrip("/"),
            auth=(public_key, secret_key),
            timeout=30,
            transport=transport,
        )
        self._sleep = sleep
        self.score_failures: list[str] = []
        self.scores_posted = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """One call, re-sent after a 429 for the interval the body names."""
        for _ in range(_MAX_RATE_LIMIT_RETRIES):
            response = await self._client.request(method, path, **kwargs)
            if response.status_code != 429:
                return response
            await self._sleep(_retry_after(response))
        return await self._client.request(method, path, **kwargs)

    async def ensure_dataset(self) -> str:
        """The dataset's id, creating the dataset on first use."""
        response = await self._request("GET", f"/api/public/v2/datasets/{DATASET_NAME}")
        if response.status_code == 404:
            response = await self._request(
                "POST",
                "/api/public/v2/datasets",
                json={
                    "name": DATASET_NAME,
                    "description": (
                        "ResearcherX groundedness golden set, synced from "
                        "backend/evals/retrieval/golden_set.json by run_eval --langfuse."
                    ),
                },
            )
        if response.status_code >= 400:
            raise LangfuseExportError(
                f"dataset {DATASET_NAME}: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.json()["id"]

    async def upsert_items(self, cases: Iterable[Case]) -> None:
        """Items upsert on their id, so re-sending the whole set each run keeps
        the dataset in step with the golden set's current text."""
        for case in cases:
            response = await self._request(
                "POST", "/api/public/dataset-items", json=dataset_item(case)
            )
            if response.status_code >= 400:
                raise LangfuseExportError(
                    f"dataset item {case.id}: HTTP {response.status_code} {response.text[:200]}"
                )

    async def post_scores(self, *, experiment_id: str, cases: Sequence[CaseScores]) -> None:
        """Every score of a run, as `score-create` ingestion events. The
        endpoint answers 207 with a per-event verdict, so one bad score costs
        only itself."""
        now = datetime.now(UTC)
        events: list[tuple[dict, str]] = [
            (score_event(experiment_id, case, score, now), f"{case.case_id}/{score.name}")
            for case in cases
            for score in case.scores
        ]
        for start in range(0, len(events), SCORE_BATCH_SIZE):
            batch = events[start : start + SCORE_BATCH_SIZE]
            labels = {event["id"]: label for event, label in batch}
            try:
                response = await self._request(
                    "POST",
                    "/api/public/ingestion",
                    json={"batch": [event for event, _ in batch]},
                )
            except httpx.HTTPError as exc:
                self.score_failures += [
                    f"{label}: {type(exc).__name__}" for label in labels.values()
                ]
                continue
            if response.status_code >= 400:
                self.score_failures += [
                    f"{label}: HTTP {response.status_code} {response.text[:120]}"
                    for label in labels.values()
                ]
                continue
            body = response.json()
            for error in body.get("errors", []):
                label = labels.get(error.get("id"), "?")
                self.score_failures.append(
                    f"{label}: {error.get('status')} {str(error.get('message', ''))[:120]}"
                )
            self.scores_posted += len(body.get("successes", []))


def expected_output_json(case: Case) -> str:
    return json.dumps(expected_output(case))
