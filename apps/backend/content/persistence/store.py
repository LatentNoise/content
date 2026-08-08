"""SQLite persistence: jobs, steps, events, artifacts, analyses.

Patterns proven in HomeTube and kept here: WAL mode, one short-lived connection
per call (safe across API and worker threads), atomic claim with BEGIN
IMMEDIATE, and re-queueing of orphaned running jobs at startup.

Status strings are only written through the domain state machines
(content.domain.job); this module enforces the mechanics, not the rules.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from content.domain.job import ensure_job_transition

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,
    status            TEXT NOT NULL,
    request           TEXT NOT NULL,          -- normalized GenerationRequest (JSON)
    plan_id           TEXT NOT NULL DEFAULT '',
    failure_policy    TEXT NOT NULL DEFAULT 'required_only',
    idempotency_key   TEXT,
    retry_of          TEXT NOT NULL DEFAULT '',
    error             TEXT NOT NULL DEFAULT '',
    cancel_requested  INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    finished_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_idempotency ON jobs(idempotency_key);
-- One *active* job per idempotency key (T3): terminally failed/cancelled jobs
-- release the key (contract D6), so the uniqueness is partial.
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency_active
    ON jobs(idempotency_key)
    WHERE idempotency_key IS NOT NULL AND status NOT IN ('failed', 'cancelled');

CREATE TABLE IF NOT EXISTS job_steps (
    job_id       TEXT NOT NULL,
    step_id      TEXT NOT NULL,
    status       TEXT NOT NULL,
    operation    TEXT NOT NULL,
    provider     TEXT NOT NULL,
    error        TEXT NOT NULL DEFAULT '',
    started_at   TEXT,
    finished_at  TEXT,
    PRIMARY KEY (job_id, step_id)
);

CREATE TABLE IF NOT EXISTS job_events (
    job_id     TEXT NOT NULL,
    sequence   INTEGER NOT NULL,
    type       TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    data       TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (job_id, sequence)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id                   TEXT PRIMARY KEY,
    job_id               TEXT NOT NULL,
    artifact_request_id  TEXT NOT NULL,
    type                 TEXT NOT NULL,
    filename             TEXT NOT NULL,
    display_filename     TEXT NOT NULL DEFAULT '',
    delivered_path       TEXT NOT NULL DEFAULT '',
    media_type           TEXT NOT NULL DEFAULT '',
    size_bytes           INTEGER NOT NULL DEFAULT 0,
    checksum             TEXT NOT NULL DEFAULT '',
    resource_key         TEXT NOT NULL DEFAULT '',
    step_signature       TEXT NOT NULL DEFAULT '',
    provenance           TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_signature
    ON artifacts(step_signature, created_at);

CREATE TABLE IF NOT EXISTS analyses (
    id            TEXT PRIMARY KEY,
    resource_key  TEXT NOT NULL,
    payload       TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_key ON analyses(resource_key, created_at);

-- Addressable analysis records (ADR 0014): a public resource keyed by
-- analysis_id that *references* the resource_key facts cache above rather than
-- duplicating the heavy facts. Holds the normalized sources + per-source
-- resource_keys + lifecycle so any client can resume a workflow from an id.
CREATE TABLE IF NOT EXISTS analysis_records (
    analysis_id       TEXT PRIMARY KEY,
    sources           TEXT NOT NULL,   -- normalized SourceDescriptor[] (JSON)
    resource_keys     TEXT NOT NULL,   -- resource_key per source, ordered (JSON)
    analyzer_version  TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_records_created
    ON analysis_records(created_at);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class IdempotencyKeyActive(Exception):
    """Another non-terminal job already holds this idempotency key."""


# Sequential migrations for databases created before a schema change
# (executescript's CREATE IF NOT EXISTS cannot add columns). PRAGMA
# user_version tracks the last applied entry; new installs start at the
# latest version directly (the base schema already includes everything).
_MIGRATIONS: list[list[str]] = [
    # 1: retry linkage + artifact reuse index (reuse_existing)
    [
        "ALTER TABLE jobs ADD COLUMN retry_of TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE artifacts ADD COLUMN resource_key TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE artifacts ADD COLUMN step_signature TEXT NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_signature "
        "ON artifacts(step_signature, created_at)",
    ],
    # 2: addressable analysis records (ADR 0014)
    [
        "CREATE TABLE IF NOT EXISTS analysis_records ("
        "  analysis_id TEXT PRIMARY KEY,"
        "  sources TEXT NOT NULL,"
        "  resource_keys TEXT NOT NULL,"
        "  analyzer_version TEXT NOT NULL,"
        "  created_at TEXT NOT NULL,"
        "  expires_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_analysis_records_created "
        "ON analysis_records(created_at)",
    ],
    # 3: user-facing artifact names (ADR 0017)
    [
        "ALTER TABLE artifacts ADD COLUMN display_filename TEXT NOT NULL DEFAULT ''",
    ],
    # 4: where the artifact was delivered, relative to the delivery root
    # (ADR 0018); '' = no delivered copy
    [
        "ALTER TABLE artifacts ADD COLUMN delivered_path TEXT NOT NULL DEFAULT ''",
    ],
]


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            fresh = not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            if not fresh:
                # Bring existing tables up to date BEFORE the base schema runs:
                # its indexes may reference columns added by migrations.
                self._migrate(conn)
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {len(_MIGRATIONS)}")

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for index in range(current, len(_MIGRATIONS)):
            for statement in _MIGRATIONS[index]:
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    # Tolerate re-application (e.g. duplicate column) so a
                    # crash between statement and version bump self-heals.
                    if "duplicate column" not in str(exc):
                        raise
            conn.execute(f"PRAGMA user_version = {index + 1}")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def ping(self) -> None:
        """The cheapest honest proof that the database is really there.

        Raises if the file vanished, the volume is not mounted, or the schema
        is unreadable. Reads from `sqlite_master` rather than a table so it
        costs nothing on a large database — `/api/v1/health` calls it on every
        container healthcheck.
        """
        with self._conn() as conn:
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()

    # --- jobs ------------------------------------------------------------------

    def create_job(
        self,
        request: dict,
        failure_policy: str,
        idempotency_key: str | None,
        retry_of: str = "",
    ) -> str:
        job_id = new_id("job")
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO jobs (id, status, request, failure_policy, "
                    "idempotency_key, retry_of, created_at) "
                    "VALUES (?, 'created', ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        json.dumps(request),
                        failure_policy,
                        idempotency_key,
                        retry_of,
                        utcnow(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise IdempotencyKeyActive(str(idempotency_key)) from exc
        return job_id

    def get_job(self, job_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._job_row(row) if row else None

    def list_jobs(self, status: str | None = None, limit: int = 200) -> list[dict]:
        query, params = "SELECT * FROM jobs", []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [self._job_row(r) for r in conn.execute(query, params).fetchall()]

    def find_job_by_idempotency_key(self, key: str) -> dict | None:
        """Latest job holding *key* that is not terminally failed/cancelled
        (those release the key — docs/contract.md D6)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ? "
                "AND status NOT IN ('failed', 'cancelled') "
                "ORDER BY created_at DESC LIMIT 1",
                (key,),
            ).fetchone()
            return self._job_row(row) if row else None

    def transition_job(self, job_id: str, target: str, **extra) -> None:
        """Move a job through the domain state machine, atomically."""
        allowed = {"error", "plan_id", "started_at", "finished_at"}
        unexpected = set(extra) - allowed
        if unexpected:
            raise ValueError(f"unexpected job fields: {unexpected}")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(f"job {job_id} not found")
            ensure_job_transition(row["status"], target)
            assignments = ["status = ?"]
            values: list = [target]
            for key, value in extra.items():
                assignments.append(f"{key} = ?")
                values.append(value)
            values.append(job_id)
            conn.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", values
            )
            conn.execute("COMMIT")

    def claim_next_queued(self) -> dict | None:
        """Atomically claim the oldest queued job, marking it running."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                (utcnow(), row["id"]),
            )
            conn.execute("COMMIT")
            claimed = self._job_row(row)
            claimed["status"] = "running"
            return claimed

    def requeue_running(self) -> int:
        """Startup recovery: orphaned running jobs go back to the queue."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status = 'queued', started_at = NULL "
                "WHERE status = 'running'"
            )
            return cur.rowcount

    def request_cancel(self, job_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE jobs SET cancel_requested = 1 WHERE id = ? AND status IN "
                "('created', 'validating', 'planning', 'queued', 'running')",
                (job_id,),
            )
            # Not-yet-running jobs cancel immediately.
            conn.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ? "
                "WHERE id = ? AND status IN "
                "('created', 'validating', 'planning', 'queued')",
                (utcnow(), job_id),
            )
            return cur.rowcount > 0

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return bool(row and row["cancel_requested"])

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["request"] = json.loads(data["request"])
        data["cancel_requested"] = bool(data["cancel_requested"])
        return data

    # --- steps -----------------------------------------------------------------

    def create_steps(self, job_id: str, steps: list[dict]) -> None:
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO job_steps (job_id, step_id, status, operation, provider) "
                "VALUES (?, ?, 'pending', ?, ?)",
                [(job_id, s["id"], s["operation"], s["provider"]) for s in steps],
            )

    def update_step(self, job_id: str, step_id: str, **fields) -> None:
        allowed = {"status", "error", "started_at", "finished_at"}
        unexpected = set(fields) - allowed
        if unexpected:
            raise ValueError(f"unexpected step fields: {unexpected}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id, step_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE job_steps SET {assignments} WHERE job_id = ? AND step_id = ?",
                values,
            )

    def list_steps(self, job_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM job_steps WHERE job_id = ? ORDER BY rowid", (job_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    # --- events ----------------------------------------------------------------

    def append_event(self, job_id: str, event_type: str, data: dict) -> int:
        """Append-only, per-job monotonically increasing sequence."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS seq FROM job_events "
                "WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            sequence = row["seq"] + 1
            conn.execute(
                "INSERT INTO job_events (job_id, sequence, type, timestamp, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, sequence, event_type, utcnow(), json.dumps(data)),
            )
            conn.execute("COMMIT")
            return sequence

    def list_events(
        self, job_id: str, after_sequence: int = 0, limit: int = 1000
    ) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM job_events WHERE job_id = ? AND sequence > ? "
                "ORDER BY sequence LIMIT ?",
                (job_id, after_sequence, limit),
            ).fetchall()
            return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    # --- artifacts -------------------------------------------------------------

    def register_artifact(self, artifact: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO artifacts (id, job_id, artifact_request_id, type, "
                "filename, display_filename, media_type, size_bytes, checksum, "
                "resource_key, step_signature, provenance, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact["id"],
                    artifact["job_id"],
                    artifact["artifact_request_id"],
                    artifact["type"],
                    artifact["filename"],
                    artifact.get("display_filename", ""),
                    artifact["media_type"],
                    artifact["size_bytes"],
                    artifact["checksum"],
                    artifact.get("resource_key", ""),
                    artifact.get("step_signature", ""),
                    json.dumps(artifact.get("provenance", {})),
                    utcnow(),
                ),
            )

    def set_artifact_delivered(self, artifact_id: str, delivered_path: str) -> None:
        """Record where a delivered copy landed, relative to the delivery
        root. Written after the copy succeeded — the row exists either way,
        the path only when the file really is there."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE artifacts SET delivered_path = ? WHERE id = ?",
                (delivered_path, artifact_id),
            )

    def find_reusable_artifact_group(
        self, step_signature: str, exclude_job_id: str
    ) -> list[dict]:
        """The complete product set of the most recent other job whose step had
        this signature (artifacts only exist for succeeded steps)."""
        if not step_signature:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE step_signature = ? AND job_id != ? "
                "ORDER BY created_at DESC",
                (step_signature, exclude_job_id),
            ).fetchall()
        if not rows:
            return []
        newest_job = rows[0]["job_id"]
        return [self._artifact_row(r) for r in rows if r["job_id"] == newest_job]

    def list_artifacts(self, job_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE job_id = ? ORDER BY created_at",
                (job_id,),
            ).fetchall()
            return [self._artifact_row(r) for r in rows]

    def get_artifact(self, artifact_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
            return self._artifact_row(row) if row else None

    @staticmethod
    def _artifact_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["provenance"] = json.loads(data["provenance"])
        return data

    # --- analyses --------------------------------------------------------------

    def save_analysis(self, analysis_id: str, resource_key: str, payload: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO analyses "
                "(id, resource_key, payload, created_at) VALUES (?, ?, ?, ?)",
                (analysis_id, resource_key, json.dumps(payload), utcnow()),
            )

    def load_fresh_analysis(self, resource_key: str, ttl_hours: float) -> dict | None:
        if ttl_hours <= 0:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM analyses WHERE resource_key = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (resource_key,),
            ).fetchone()
        if row is None:
            return None
        created = datetime.fromisoformat(row["created_at"])
        age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        if age_hours > ttl_hours:
            return None
        return json.loads(row["payload"])

    def list_analyses(self, limit: int = 50) -> list[dict]:
        """Cached analyses, newest first — for the console cache view."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT resource_key, payload, created_at FROM analyses "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out: list[dict] = []
        for row in rows:
            resource = (json.loads(row["payload"]) or {}).get("resource", {})
            out.append(
                {
                    "resource_key": row["resource_key"],
                    "title": resource.get("title", ""),
                    "resource_type": resource.get("resource_type", ""),
                    "created_at": row["created_at"],
                }
            )
        return out

    def purge_analyses(self) -> int:
        """Drop every cached analysis (DB). Returns how many were removed."""
        with self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
            conn.execute("DELETE FROM analyses")
            conn.execute("DELETE FROM analysis_records")
        return int(count)

    # --- addressable analysis records (ADR 0014) -------------------------------

    def save_analysis_record(
        self,
        analysis_id: str,
        sources: list[dict],
        resource_keys: list[str],
        analyzer_version: str,
        created_at: str,
        expires_at: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO analysis_records "
                "(analysis_id, sources, resource_keys, analyzer_version, "
                "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    analysis_id,
                    json.dumps(sources),
                    json.dumps(resource_keys),
                    analyzer_version,
                    created_at,
                    expires_at,
                ),
            )

    def load_analysis_record(self, analysis_id: str) -> dict | None:
        """The addressable record, or None if there is no such id. Facts are not
        joined here — that is the service's job (it references the facts cache)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_records WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "analysis_id": row["analysis_id"],
            "sources": json.loads(row["sources"]),
            "resource_keys": json.loads(row["resource_keys"]),
            "analyzer_version": row["analyzer_version"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }
