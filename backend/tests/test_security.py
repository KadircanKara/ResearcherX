"""Quota enforcement: per-IP windows, global daily cap, owner bypass."""

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.core.security as security
from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.services.research_service import ResearchService


def make_request(headers: dict[str, str] | None = None, client_host: str = "1.2.3.4") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (client_host, 1234),
        "query_string": b"",
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def fresh_limiter_state():
    security._storage.reset()
    yield
    security._storage.reset()


# --- client_ip -------------------------------------------------------------


def test_client_ip_prefers_first_forwarded_hop():
    req = make_request({"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
    assert security.client_ip(req) == "9.9.9.9"


def test_client_ip_falls_back_to_socket_peer():
    assert security.client_ip(make_request()) == "1.2.3.4"


# --- is_owner ---------------------------------------------------------------


def test_is_owner_matches_configured_key(monkeypatch):
    monkeypatch.setattr(settings, "owner_api_key", "sekrit")
    assert security.is_owner(make_request({"X-API-Key": "sekrit"})) is True
    assert security.is_owner(make_request({"X-API-Key": "wrong"})) is False
    assert security.is_owner(make_request()) is False


def test_is_owner_never_matches_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "owner_api_key", None)
    assert security.is_owner(make_request({"X-API-Key": ""})) is False
    assert security.is_owner(make_request({"X-API-Key": "anything"})) is False


# --- per-IP windows ---------------------------------------------------------


async def test_read_limit_trips_with_retry_after(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_reads", "2/minute")
    req = make_request()
    await security.enforce_read_limits(req)
    await security.enforce_read_limits(req)
    with pytest.raises(HTTPException) as exc:
        await security.enforce_read_limits(req)
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) >= 1


async def test_limits_are_per_ip(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_reads", "1/minute")
    await security.enforce_read_limits(make_request({"X-Forwarded-For": "7.7.7.7"}))
    # A different IP still has budget.
    await security.enforce_read_limits(make_request({"X-Forwarded-For": "8.8.8.8"}))
    with pytest.raises(HTTPException):
        await security.enforce_read_limits(make_request({"X-Forwarded-For": "7.7.7.7"}))


async def test_run_quota_trips_per_ip(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_runs", "1/hour")
    monkeypatch.setattr(settings, "global_daily_run_cap", 100)
    req = make_request()
    await security.enforce_run_quotas(req)
    with pytest.raises(HTTPException) as exc:
        await security.enforce_run_quotas(req)
    assert exc.value.status_code == 429


# --- global daily cap -------------------------------------------------------


async def test_global_cap_counts_todays_runs(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_runs", "100/hour")
    monkeypatch.setattr(settings, "global_daily_run_cap", 2)
    service = ResearchService()
    async with SessionLocal() as db:
        await service.create(db, "first question")
        await service.create(db, "second question")
    with pytest.raises(HTTPException) as exc:
        await security.enforce_run_quotas(make_request())
    assert exc.value.status_code == 429
    # Retry-After points at next UTC midnight: positive, at most 24h.
    assert 1 <= int(exc.value.headers["Retry-After"]) <= 86400


async def test_owner_bypasses_everything(monkeypatch):
    monkeypatch.setattr(settings, "owner_api_key", "sekrit")
    monkeypatch.setattr(settings, "rate_limit_runs", "1/hour")
    monkeypatch.setattr(settings, "global_daily_run_cap", 0)  # fully exhausted
    req = make_request({"X-API-Key": "sekrit"})
    await security.enforce_run_quotas(req)  # must not raise
    await security.enforce_run_quotas(req)


# --- through the API --------------------------------------------------------


async def test_post_returns_429_when_global_cap_reached(monkeypatch):
    monkeypatch.setattr(settings, "global_daily_run_cap", 0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/research", json={"question": "is there capacity?"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
