"""search_one decision logic: validate → retry → degrade, with fake agents.

Drives ResearchService.run_async with scripted planner/searcher/synth/critic
fakes and a recording bus — no network, no LLM.
"""

import pytest

import app.services.research_service as rs
from app.core.config import settings
from app.db.session import SessionLocal
from app.schemas.research import (
    Critique,
    FindingValidation,
    ResearchPlan,
    SearchFinding,
)
from app.services.research_service import ResearchService


def good_finding(query: str) -> SearchFinding:
    return SearchFinding(query=query, summary=f"useful summary for {query}", sources=["https://s"])


def empty_finding(query: str) -> SearchFinding:
    return SearchFinding(query=query, summary="No results found.", sources=[])


class FakePlanner:
    def __init__(self, sub_queries: list[str], verdicts: list[FindingValidation | Exception]):
        self._plan = ResearchPlan(sub_queries=sub_queries, rationale="because")
        self._verdicts = list(verdicts)
        self.validate_calls = 0

    async def run(self, inp) -> ResearchPlan:
        return self._plan

    async def validate(self, inp) -> FindingValidation:
        self.validate_calls += 1
        verdict = self._verdicts.pop(0)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict


def make_searcher_class(script: list[SearchFinding]) -> type:
    remaining = list(script)
    calls: list[str] = []

    class _FakeSearcher:
        recorded_calls = calls

        async def run(self, inp) -> SearchFinding:
            calls.append(inp.query)
            return remaining.pop(0).model_copy(deep=True)

    return _FakeSearcher


class FakeSynthesizer:
    async def stream(self, inp):
        yield "part1 "
        yield "part2"


class FakeCritic:
    async def run(self, inp) -> Critique:
        return Critique(issues=[], overall="pass")


class BusRecorder:
    def __init__(self) -> None:
        self.events: list[dict | None] = []

    async def publish(self, run_id: str, event: dict) -> None:
        self.events.append(event)

    async def close(self, run_id: str) -> None:
        self.events.append(None)

    def of_type(self, kind: str) -> list[dict]:
        return [e for e in self.events if e and e.get("type") == kind]


@pytest.fixture
def harness(monkeypatch):
    """Build a fully-faked service. Returns (run_it, recorder, planner_ref)."""

    def build(planner: FakePlanner, searcher_script: list[SearchFinding]):
        service = ResearchService()
        service._planner = planner
        service._synthesizer = FakeSynthesizer()
        service._critic = FakeCritic()
        searcher_cls = make_searcher_class(searcher_script)
        monkeypatch.setattr(rs, "SearcherAgent", searcher_cls)
        recorder = BusRecorder()
        monkeypatch.setattr(rs, "bus", recorder)

        async def run_it(question: str = "what is the meaning of tests?"):
            async with SessionLocal() as db:
                run = await service.create(db, question)
            await service.run_async(run.id)
            async with SessionLocal() as db:
                refreshed = await service.get(db, run.id)
            steps = sorted(refreshed.steps, key=lambda s: s.created_at)
            return refreshed, steps, searcher_cls

        run_it.service = service  # for tests that swap individual agents
        return run_it, recorder

    return build


def steps_of(steps, kind: str):
    return [s for s in steps if str(s.kind) == kind]


async def test_valid_first_attempt(harness):
    planner = FakePlanner(["q1"], [FindingValidation(verdict="valid")])
    run_it, recorder = harness(planner, [good_finding("q1")])

    run, steps, searcher = await run_it()

    assert str(run.status) == "completed"
    searches = steps_of(steps, "search")
    assert len(searches) == 1
    assert searches[0].output["validated"] is True  # finalized post-validation
    assert searches[0].output["attempts"] == 1
    assert recorder.of_type("search_retry") == []
    (finding_event,) = recorder.of_type("finding")
    assert finding_event["finding"]["validated"] is True


async def test_invalid_then_valid_on_revised_query(harness):
    planner = FakePlanner(
        ["q1"],
        [
            FindingValidation(verdict="invalid", reasons=["off-topic"], revised_query="q1 better"),
            FindingValidation(verdict="valid"),
        ],
    )
    run_it, recorder = harness(planner, [good_finding("q1"), good_finding("q1 better")])

    run, steps, searcher = await run_it()

    assert searcher.recorded_calls == ["q1", "q1 better"]
    (retry_event,) = recorder.of_type("search_retry")
    assert retry_event["old_query"] == "q1"
    assert retry_event["new_query"] == "q1 better"
    searches = steps_of(steps, "search")
    assert len(searches) == 2
    assert searches[-1].output["validated"] is True
    assert searches[-1].output["attempts"] == 2
    assert searches[0].output["validated"] is False  # rejected attempt stays unflagged


async def test_degraded_after_retry_cap(harness):
    invalid = lambda q: FindingValidation(  # noqa: E731
        verdict="invalid", reasons=["bad"], revised_query=q
    )
    planner = FakePlanner(["q1"], [invalid("q2"), invalid("q3"), invalid("q4-unused")])
    run_it, recorder = harness(
        planner, [good_finding("q1"), good_finding("q2"), good_finding("q3")]
    )

    run, steps, searcher = await run_it()

    assert str(run.status) == "completed"  # degrade, don't fail
    assert searcher.recorded_calls == ["q1", "q2", "q3"]
    searches = steps_of(steps, "search")
    assert searches[-1].output["accepted_degraded"] is True
    (finding_event,) = recorder.of_type("finding")
    assert finding_event["finding"]["accepted_degraded"] is True


async def test_no_revision_breaks_early(harness):
    planner = FakePlanner(
        ["q1"], [FindingValidation(verdict="invalid", reasons=["bad"], revised_query=None)]
    )
    run_it, recorder = harness(planner, [good_finding("q1")])

    run, steps, searcher = await run_it()

    assert searcher.recorded_calls == ["q1"]  # no usable revision → no retry
    assert steps_of(steps, "search")[-1].output["accepted_degraded"] is True


async def test_same_revision_does_not_loop(harness):
    planner = FakePlanner(
        ["q1"], [FindingValidation(verdict="invalid", reasons=["bad"], revised_query="Q1")]
    )
    run_it, recorder = harness(planner, [good_finding("q1")])

    run, steps, searcher = await run_it()

    assert searcher.recorded_calls == ["q1"]  # case-insensitive same query → break


async def test_empty_finding_without_budget_skips_validator_llm(harness, monkeypatch):
    monkeypatch.setattr(settings, "max_search_retries", 0)
    planner = FakePlanner(["q1"], [])  # must never be consulted
    run_it, recorder = harness(planner, [empty_finding("q1")])

    run, steps, searcher = await run_it()

    assert planner.validate_calls == 0  # cheap pre-check skipped the LLM call
    validates = steps_of(steps, "validate")
    assert validates[0].output["verdict"] == "invalid"
    assert "empty result: no sources" in validates[0].output["reasons"]
    assert steps_of(steps, "search")[-1].output["accepted_degraded"] is True


async def test_empty_finding_never_accepted_even_if_model_says_valid(harness):
    planner = FakePlanner(["q1"], [FindingValidation(verdict="valid")])
    run_it, recorder = harness(planner, [empty_finding("q1")])

    run, steps, searcher = await run_it()

    assert planner.validate_calls == 1
    validates = steps_of(steps, "validate")
    assert validates[0].output["verdict"] == "invalid"  # forced: empty is garbage by definition
    assert "empty result: no sources" in validates[0].output["reasons"]


async def test_validator_error_fails_open_without_leaking(harness):
    planner = FakePlanner(["q1"], [RuntimeError("secret provider detail")])
    run_it, recorder = harness(planner, [good_finding("q1")])

    run, steps, searcher = await run_it()

    assert str(run.status) == "completed"
    assert steps_of(steps, "search")[-1].output["validated"] is True  # fail-open
    validates = steps_of(steps, "validate")
    assert validates[0].output["reasons"] == ["validator unavailable"]
    # The exception text must not reach anything client-visible.
    all_visible = repr([s.output for s in steps]) + repr(recorder.events)
    assert "secret provider detail" not in all_visible


async def test_synthesis_stream_retries_on_next_provider(harness, monkeypatch):
    """Mid-stream APIError → report_reset + one retry; second attempt wins."""
    import httpx
    from openai import APIError

    import app.services.research_service as rs_module

    planner = FakePlanner(["q1"], [FindingValidation(verdict="valid")])
    run_it, recorder = harness(planner, [good_finding("q1")])

    class FlakySynth:
        attempts = 0

        async def stream(self, inp):
            FlakySynth.attempts += 1
            if FlakySynth.attempts == 1:
                yield "partial "
                raise APIError(
                    "Provider returned error",
                    request=httpx.Request("POST", "http://x"),
                    body=None,
                )
            yield "clean "
            yield "report"

    rotations = []

    def fake_rotate():
        rotations.append(1)
        return type("P", (), {"base_url": "http://next"})()

    monkeypatch.setattr(rs_module, "rotate_current", fake_rotate)
    run_it.service._synthesizer = FlakySynth()

    run, steps, searcher = await run_it()

    assert str(run.status) == "completed"
    assert run.report == "clean report"  # partial draft discarded
    assert FlakySynth.attempts == 2
    assert rotations == [1]  # failed over before the retry
    types = [e.get("type") for e in recorder.events if e]
    assert "report_reset" in types
    # Deltas: 1 from the failed attempt + 2 from the clean attempt.
    assert types.count("report_delta") == 3
    reset_idx = types.index("report_reset")
    assert types.index("critique") > reset_idx


async def test_critic_failure_does_not_fail_run(harness):
    """Critic is advisory: it fails open, and the report survives it."""
    planner = FakePlanner(["q1"], [FindingValidation(verdict="valid")])
    run_it, recorder = harness(planner, [good_finding("q1")])

    class ExplodingCritic:
        async def run(self, inp):
            raise RuntimeError("malformed JSON from free-tier provider")

    run_it.service._critic = ExplodingCritic()

    run, steps, searcher = await run_it()

    assert str(run.status) == "completed"
    assert run.report == "part1 part2"  # persisted BEFORE critique ran
    assert recorder.of_type("critique") == []  # no fabricated verdict
    crit_steps = steps_of(steps, "critique")
    assert crit_steps[0].output == {"unavailable": True}
    assert "malformed JSON" not in repr(recorder.events)  # nothing leaked


async def test_pipeline_failure_is_sanitized(harness):
    planner = FakePlanner(["q1"], [])

    async def explode(inp):
        raise RuntimeError("groq exploded with internal detail")

    planner.run = explode
    run_it, recorder = harness(planner, [])

    run, steps, searcher = await run_it()

    assert str(run.status) == "failed"
    assert run.error == f"The research run failed. (ref: {run.id})"
    assert "groq exploded" not in (run.error or "")
    (error_event,) = recorder.of_type("error")
    assert error_event["message"] == run.error
