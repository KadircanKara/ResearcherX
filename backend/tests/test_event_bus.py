"""Event bus: pub/sub semantics and the bounded drop-oldest behavior."""

import app.services.event_bus as event_bus_module
from app.services.event_bus import EventBus


async def test_publish_reaches_subscriber():
    bus = EventBus()
    q = bus.subscribe("r1")
    await bus.publish("r1", {"type": "plan"})
    assert (await q.get()) == {"type": "plan"}


async def test_publish_reaches_all_subscribers():
    bus = EventBus()
    q1, q2 = bus.subscribe("r1"), bus.subscribe("r1")
    await bus.publish("r1", {"type": "finding"})
    assert (await q1.get()) == {"type": "finding"}
    assert (await q2.get()) == {"type": "finding"}


async def test_publish_is_scoped_to_run():
    bus = EventBus()
    other = bus.subscribe("other-run")
    await bus.publish("r1", {"type": "plan"})
    assert other.empty()


async def test_publish_without_subscribers_is_noop():
    bus = EventBus()
    await bus.publish("nobody-listening", {"type": "plan"})  # must not raise


async def test_unsubscribe_stops_delivery_and_updates_count():
    bus = EventBus()
    q = bus.subscribe("r1")
    assert bus.subscriber_count("r1") == 1
    bus.unsubscribe("r1", q)
    assert bus.subscriber_count("r1") == 0
    await bus.publish("r1", {"type": "plan"})
    assert q.empty()


async def test_close_delivers_none_sentinel():
    bus = EventBus()
    q = bus.subscribe("r1")
    await bus.close("r1")
    assert (await q.get()) is None


async def test_bounded_queue_drops_oldest(monkeypatch):
    monkeypatch.setattr(event_bus_module, "_QUEUE_MAXSIZE", 3)
    bus = EventBus()
    q = bus.subscribe("r1")
    for i in range(5):  # two over capacity
        await bus.publish("r1", {"n": i})
    # Oldest two dropped; the newest three survive in order.
    assert [(await q.get())["n"] for _ in range(3)] == [2, 3, 4]
    assert q.empty()


async def test_close_sentinel_survives_full_queue(monkeypatch):
    monkeypatch.setattr(event_bus_module, "_QUEUE_MAXSIZE", 2)
    bus = EventBus()
    q = bus.subscribe("r1")
    await bus.publish("r1", {"n": 0})
    await bus.publish("r1", {"n": 1})
    await bus.close("r1")  # full queue must still take the end-of-stream marker
    assert (await q.get()) == {"n": 1}
    assert (await q.get()) is None
