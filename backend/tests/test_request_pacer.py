"""RequestPacer: request starts spread evenly so no second holds more than the rate.

Clock and sleep are injected, so nothing here waits on real time."""

import asyncio

import pytest

from app.services import request_pacer
from app.services.request_pacer import RequestPacer


class FakeTime:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _pacer(rate: float, t: FakeTime) -> RequestPacer:
    return RequestPacer(rate, clock=t.clock, sleep=t.sleep)


async def test_the_first_request_starts_at_once():
    t = FakeTime()
    await _pacer(80, t).wait()
    assert t.sleeps == []


async def test_back_to_back_requests_are_spaced_one_interval_apart():
    t = FakeTime()
    p = _pacer(80, t)
    starts = []
    for _ in range(5):
        await p.wait()
        starts.append(t.now)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert gaps == pytest.approx([1 / 80] * 4)


async def test_no_one_second_window_holds_more_than_the_rate():
    t = FakeTime()
    p = _pacer(80, t)
    starts = []
    for _ in range(400):
        await p.wait()
        starts.append(t.now)
    worst = max(sum(1 for s in starts if a <= s < a + 1.0 - 1e-9) for a in starts)
    assert worst <= 80


async def test_concurrent_waiters_each_get_their_own_slot():
    # No await sits between reading and reserving a slot, so callers that
    # arrive together queue up one interval apart instead of firing at once.
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0)  # yield, so every waiter reads the same clock

    p = RequestPacer(10, clock=lambda: 100.0, sleep=sleep)
    await asyncio.gather(*(p.wait() for _ in range(4)))
    assert sorted(sleeps) == pytest.approx([0.1, 0.2, 0.3])


async def test_an_idle_pacer_does_not_bank_a_burst():
    t = FakeTime()
    p = _pacer(10, t)
    await p.wait()
    t.now += 5.0  # idle long past the interval
    await p.wait()
    await p.wait()
    assert t.sleeps == pytest.approx([0.1])


@pytest.mark.parametrize("rate", [0, -1.0])
def test_a_non_positive_rate_is_refused(rate):
    with pytest.raises(ValueError):
        RequestPacer(rate)


def test_jev_calls_share_one_pacer_at_the_configured_rate(monkeypatch):
    monkeypatch.setattr(request_pacer, "_jev_pacer", None)
    monkeypatch.setattr(request_pacer.settings, "jev_rate_limit_per_s", 80.0)
    first = request_pacer.jev_pacer()
    assert first is request_pacer.jev_pacer()
    assert first.rate_per_s == 80.0
