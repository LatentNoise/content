"""The queue and the log of what was sent — one SQLite file.

A message is accepted before it is delivered, so an SMTP hiccup never fails
the caller's request. The row is the durable record: it survives a restart,
it carries the attempt count, and it is the only place that knows whether a
message actually left.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

QUEUED = "queued"
SENDING = "sending"
SENT = "sent"
FAILED = "failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    client          TEXT NOT NULL,
    to_addrs        TEXT NOT NULL,
    from_addr       TEXT NOT NULL,
    reply_to        TEXT,
    subject         TEXT NOT NULL,
    text_body       TEXT NOT NULL,
    html_body       TEXT,
    status          TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    next_attempt_at REAL NOT NULL,
    sent_at         REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_due ON messages (status, next_attempt_at);
"""


@dataclass(frozen=True)
class Message:
    id: str
    client: str
    to_addrs: list[str]
    from_addr: str
    reply_to: str | None
    subject: str
    text_body: str
    html_body: str | None
    status: str
    attempts: int
    last_error: str | None
    created_at: float
    updated_at: float
    next_attempt_at: float
    sent_at: float | None

    def public(self) -> dict[str, object]:
        """What a caller may read back. Bodies stay out; they are not its business
        once accepted, and a body in a status response ends up in someone's log."""
        return {
            "id": self.id,
            "status": self.status,
            "to": self.to_addrs,
            "subject": self.subject,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "sent_at": self.sent_at,
        }


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        client=row["client"],
        to_addrs=json.loads(row["to_addrs"]),
        from_addr=row["from_addr"],
        reply_to=row["reply_to"],
        subject=row["subject"],
        text_body=row["text_body"],
        html_body=row["html_body"],
        status=row["status"],
        attempts=row["attempts"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        next_attempt_at=row["next_attempt_at"],
        sent_at=row["sent_at"],
    )


class Store:
    """Every read and write of the queue goes through here."""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def enqueue(
        self,
        *,
        client: str,
        to_addrs: list[str],
        from_addr: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
        reply_to: str | None = None,
    ) -> Message:
        now = time.time()
        message_id = uuid.uuid4().hex
        self._conn.execute(
            """INSERT INTO messages (id, client, to_addrs, from_addr, reply_to, subject,
                                     text_body, html_body, status, created_at, updated_at,
                                     next_attempt_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message_id,
                client,
                json.dumps(to_addrs),
                from_addr,
                reply_to,
                subject,
                text_body,
                html_body,
                QUEUED,
                now,
                now,
                now,
            ),
        )
        got = self.get(message_id)
        assert got is not None
        return got

    def get(self, message_id: str) -> Message | None:
        row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return _row_to_message(row) if row else None

    def claim_next_due(self, now: float | None = None) -> Message | None:
        """Take the oldest message that is due, atomically.

        `BEGIN IMMEDIATE` takes the write lock before reading, so two workers
        on the same machine never claim the same row.
        """
        now = time.time() if now is None else now
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                """SELECT * FROM messages
                   WHERE status = ? AND next_attempt_at <= ?
                   ORDER BY next_attempt_at ASC LIMIT 1""",
                (QUEUED, now),
            ).fetchone()
            if row is None:
                self._conn.execute("COMMIT")
                return None
            self._conn.execute(
                """UPDATE messages SET status = ?, attempts = attempts + 1, updated_at = ?
                   WHERE id = ?""",
                (SENDING, now, row["id"]),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        claimed = self.get(row["id"])
        return claimed

    def mark_sent(self, message_id: str) -> None:
        now = time.time()
        self._conn.execute(
            """UPDATE messages SET status = ?, sent_at = ?, updated_at = ?, last_error = NULL
               WHERE id = ?""",
            (SENT, now, now, message_id),
        )

    def mark_failed(self, message_id: str, error: str) -> None:
        now = time.time()
        self._conn.execute(
            "UPDATE messages SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (FAILED, error[:2000], now, message_id),
        )

    def reschedule(self, message_id: str, error: str, delay_seconds: float) -> None:
        now = time.time()
        self._conn.execute(
            """UPDATE messages SET status = ?, last_error = ?, updated_at = ?, next_attempt_at = ?
               WHERE id = ?""",
            (QUEUED, error[:2000], now, now + delay_seconds, message_id),
        )

    def requeue_stuck(self, older_than_seconds: float = 300.0) -> int:
        """A crash mid-send leaves a row in `sending`. Nothing else would ever
        pick it up, so a restart puts those back in the queue."""
        cutoff = time.time() - older_than_seconds
        cursor = self._conn.execute(
            """UPDATE messages SET status = ?, next_attempt_at = ?
               WHERE status = ? AND updated_at < ?""",
            (QUEUED, time.time(), SENDING, cutoff),
        )
        return cursor.rowcount

    def counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM messages GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}
