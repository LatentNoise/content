"""The loop that actually sends.

It runs inside the same process as the API by default, because at this volume
a second deployment would be ceremony. The queue is claimed atomically, so
splitting it out later is a configuration change, not a rewrite.
"""

from __future__ import annotations

import asyncio
import logging

from mailer.config import Settings
from mailer.sender import PermanentSendError, TemporarySendError
from mailer.store import Store

log = logging.getLogger("mailer.worker")


def backoff_seconds(attempts: int, base: int) -> float:
    """Exponential, capped at an hour. `attempts` is the count after claiming,
    so the first retry waits `base` seconds."""
    return float(min(base * (2 ** max(attempts - 1, 0)), 3600))


async def deliver_once(store: Store, sender, settings: Settings) -> bool:
    """Claim one due message and deal with it. True when one was handled."""
    message = store.claim_next_due()
    if message is None:
        return False
    try:
        await asyncio.to_thread(sender.send, message)
    except PermanentSendError as exc:
        log.warning("message %s refused permanently: %s", message.id, exc)
        store.mark_failed(message.id, str(exc))
    except TemporarySendError as exc:
        if message.attempts >= settings.max_attempts:
            log.warning("message %s exhausted %s attempts: %s", message.id, message.attempts, exc)
            store.mark_failed(message.id, f"gave up after {message.attempts} attempts: {exc}")
        else:
            delay = backoff_seconds(message.attempts, settings.retry_base_seconds)
            log.info("message %s deferred %.0fs: %s", message.id, delay, exc)
            store.reschedule(message.id, str(exc), delay)
    except Exception as exc:  # noqa: BLE001 — a bug here must not stop the loop
        log.exception("message %s hit an unexpected error", message.id)
        store.mark_failed(message.id, f"unexpected: {type(exc).__name__}: {exc}")
    else:
        log.info("message %s sent to %s", message.id, message.to_addrs)
        store.mark_sent(message.id)
    return True


async def run_worker(store: Store, sender, settings: Settings) -> None:
    requeued = store.requeue_stuck()
    if requeued:
        log.info("requeued %s message(s) left mid-send by a previous run", requeued)
    while True:
        try:
            handled = await deliver_once(store, sender, settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop outlives any single failure
            log.exception("worker iteration failed")
            handled = False
        if not handled:
            await asyncio.sleep(settings.poll_seconds)
