"""Unit tests for shared/events/consumer.py's reconnect behavior — the
fix for the chaos-test-found bug where a dropped Redis connection killed
a consumer task permanently (see docs/decisions/ADR-0018)."""

import asyncio

import pytest
import redis.exceptions

from shared.events.consumer import consume_forever


class FakePubsub:
    def __init__(self, messages: list, fail_after: int | None = None):
        self._messages = list(messages)
        self._fail_after = fail_after
        self._served = 0

    async def get_message(self, **kwargs):
        if self._fail_after is not None and self._served >= self._fail_after:
            raise redis.exceptions.ConnectionError("connection reset")
        self._served += 1
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(0)
        return None


class FakeBus:
    """subscribe() is called once per (re)connection attempt; `pubsubs`
    is the sequence handed out in order, one per call."""

    def __init__(self, pubsubs: list[FakePubsub]):
        self._pubsubs = list(pubsubs)
        self.subscribe_calls = 0

    async def subscribe(self, *channels):
        self.subscribe_calls += 1
        return self._pubsubs.pop(0)


async def test_messages_are_delivered_to_the_handler_in_order():
    received = []
    bus = FakeBus([FakePubsub([{"data": "a"}, {"data": "b"}])])

    async def handler(message):
        received.append(message["data"])
        if len(received) == 2:
            raise asyncio.CancelledError  # stop the forever-loop for the test

    with pytest.raises(asyncio.CancelledError):
        await consume_forever(bus, "chan", on_message=handler, service_name="test")

    assert received == ["a", "b"]


async def test_a_dropped_connection_triggers_resubscribe_not_a_crash():
    bus = FakeBus(
        [
            FakePubsub([{"data": "a"}], fail_after=1),  # serves "a", then the next get_message fails
            FakePubsub([{"data": "b"}]),  # the reconnect
        ]
    )
    received = []

    async def handler(message):
        received.append(message["data"])
        if message["data"] == "b":
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await consume_forever(bus, "chan", on_message=handler, service_name="test", reconnect_delay_seconds=0.0)

    assert received == ["a", "b"]
    assert bus.subscribe_calls == 2, "a dropped connection must trigger exactly one resubscribe"


async def test_only_redis_errors_trigger_reconnect_not_handler_bugs():
    bus = FakeBus([FakePubsub([{"data": "x"}])])

    async def broken_handler(message):
        raise ValueError("a bug in the handler, not a connectivity problem")

    with pytest.raises(ValueError):
        await consume_forever(bus, "chan", on_message=broken_handler, service_name="test")

    assert bus.subscribe_calls == 1, "a non-Redis exception must not be swallowed as if it were a reconnect case"
