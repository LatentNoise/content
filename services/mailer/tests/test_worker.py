"""Delivery: what happens after a message is accepted."""

from dataclasses import replace

from mailer.sender import PermanentSendError, TemporarySendError
from mailer.store import FAILED, QUEUED, SENDING, SENT
from mailer.worker import backoff_seconds, deliver_once


class Refusing:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def send(self, message):
        self.calls += 1
        raise self.error


def _queue(store, **overrides):
    payload = dict(
        client="content",
        to_addrs=["someone@example.com"],
        from_addr="Test <test@latentnoise.dev>",
        subject="Hello",
        text_body="Body",
    )
    payload.update(overrides)
    return store.enqueue(**payload)


def test_backoff_grows_and_is_capped():
    assert backoff_seconds(1, 30) == 30
    assert backoff_seconds(2, 30) == 60
    assert backoff_seconds(3, 30) == 120
    assert backoff_seconds(20, 30) == 3600


async def test_a_message_is_sent_and_marked(store, sender, settings):
    message = _queue(store)
    assert await deliver_once(store, sender, settings) is True
    assert store.get(message.id).status == SENT
    assert sender.sent[0].subject == "Hello"


async def test_an_empty_queue_does_nothing(store, sender, settings):
    assert await deliver_once(store, sender, settings) is False


async def test_a_permanent_refusal_is_final(store, settings):
    refusing = Refusing(PermanentSendError("550 no such user"))
    message = _queue(store)
    await deliver_once(store, refusing, settings)
    stored = store.get(message.id)
    assert stored.status == FAILED
    assert "550" in stored.last_error
    assert await deliver_once(store, refusing, settings) is False


async def test_a_temporary_refusal_is_retried_then_given_up_on(store, settings):
    # A zero base makes every retry due at once, so the test exercises the
    # give-up rule without waiting for real backoff.
    immediate = replace(settings, retry_base_seconds=0)
    refusing = Refusing(TemporarySendError("451 try later"))
    message = _queue(store)
    for _ in range(immediate.max_attempts):
        await deliver_once(store, refusing, immediate)
    stored = store.get(message.id)
    assert stored.status == FAILED
    assert stored.attempts == settings.max_attempts
    assert "gave up" in stored.last_error


async def test_a_deferred_message_is_not_claimed_before_its_time(store, settings):
    refusing = Refusing(TemporarySendError("451 try later"))
    _queue(store)
    await deliver_once(store, refusing, settings)
    assert await deliver_once(store, refusing, settings) is False


async def test_an_unexpected_error_does_not_wedge_the_queue(store, settings):
    boom = Refusing(RuntimeError("bug"))
    message = _queue(store)
    await deliver_once(store, boom, settings)
    assert store.get(message.id).status == FAILED


def test_a_claim_is_taken_only_once(store):
    _queue(store)
    assert store.claim_next_due() is not None
    assert store.claim_next_due() is None


def test_a_crash_mid_send_is_recovered_on_restart(store):
    message = _queue(store)
    store.claim_next_due()
    assert store.get(message.id).status == SENDING
    assert store.requeue_stuck(older_than_seconds=-1) == 1
    assert store.get(message.id).status == QUEUED
