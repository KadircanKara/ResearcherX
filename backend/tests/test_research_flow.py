"""ONE end-to-end flow test: POST → faked pipeline → COMPLETED + event order.

Exercises the real API handlers, task registry, service orchestration, event
bus, and sqlite persistence. Only the agents (LLM + search) are faked.
"""

import httpx

import app.services.research_service as rs
from app.main import app
from app.api.v1 import research as research_api
from app.services.task_registry import registry
from tests.test_validation_loop import (
    FakeCritic,
    FakePlanner,
    FakeSynthesizer,
    good_finding,
    make_searcher_class,
)
from app.schemas.research import FindingValidation


async def test_full_research_flow(monkeypatch):
    service = research_api.service
    planner = FakePlanner(
        ["sub q1", "sub q2"],
        [FindingValidation(verdict="valid"), FindingValidation(verdict="valid")],
    )
    monkeypatch.setattr(service, "_planner", planner)
    monkeypatch.setattr(service, "_synthesizer", FakeSynthesizer())
    monkeypatch.setattr(service, "_critic", FakeCritic())
    monkeypatch.setattr(
        rs, "SearcherAgent", make_searcher_class([good_finding("sub q1"), good_finding("sub q2")])
    )

    # Spy on the real bus: record the event sequence while still delivering.
    events: list[dict] = []
    real_publish = rs.bus.publish

    async def recording_publish(run_id: str, event: dict) -> None:
        events.append(event)
        await real_publish(run_id, event)

    monkeypatch.setattr(rs.bus, "publish", recording_publish)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/v1/health")
        assert health.status_code == 200

        created = await client.post("/v1/research", json={"question": "How do tests work?"})
        assert created.status_code == 201
        run_id = created.json()["id"]

        # The pipeline runs as a registered in-process task — await it.
        task = registry.get(run_id)
        assert task is not None, "run task must be registered, not fire-and-forget"
        await task
        assert registry.get(run_id) is None, "done-callback must discard the task"

        fetched = await client.get(f"/v1/research/{run_id}")

    body = fetched.json()
    assert body["status"] == "completed"
    assert body["report"] == "part1 part2"
    assert body["error"] is None

    kinds = [s["kind"] for s in body["steps"]]
    assert kinds.count("plan") == 1
    assert kinds.count("search") == 2
    assert kinds.count("validate") == 2
    assert kinds.count("synthesize") == 1
    assert kinds.count("critique") == 1

    # Event sequence: status:running first, status:completed last, and the
    # pipeline milestones in pipeline order in between.
    types = [e["type"] for e in events]
    assert types[0] == "status" and events[0]["status"] == "running"
    assert types[-1] == "status" and events[-1]["status"] == "completed"
    assert types.count("finding") == 2
    assert types.count("report_delta") == 2
    assert (
        types.index("plan")
        < types.index("finding")
        < types.index("report_delta")
        < types.index("critique")
    )


async def test_get_unknown_run_is_404():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/research/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
