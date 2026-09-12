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
from content.identity import LOCAL_OWNER

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL DEFAULT 'local',
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
CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id, created_at);
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
    owner_id             TEXT NOT NULL DEFAULT 'local',
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
CREATE INDEX IF NOT EXISTS idx_artifacts_owner ON artifacts(owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_signature
    ON artifacts(step_signature, created_at);

CREATE TABLE IF NOT EXISTS uploads (
    id                  TEXT PRIMARY KEY,
    owner_id            TEXT NOT NULL DEFAULT 'local',
    filename            TEXT NOT NULL,
    media_type          TEXT NOT NULL DEFAULT '',
    size_bytes          INTEGER NOT NULL DEFAULT 0,
    sha256              TEXT NOT NULL DEFAULT '',
    path                TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    -- The TTL runs from the last job that referenced this upload, not from
    -- creation, so retrying a job never finds its input swept away.
    last_referenced_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_uploads_referenced ON uploads(last_referenced_at);
CREATE INDEX IF NOT EXISTS idx_uploads_owner ON uploads(owner_id, created_at);

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
    owner_id          TEXT NOT NULL DEFAULT 'local',
    sources           TEXT NOT NULL,   -- normalized SourceDescriptor[] (JSON)
    resource_keys     TEXT NOT NULL,   -- resource_key per source, ordered (JSON)
    analyzer_version  TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_records_created
    ON analysis_records(created_at);

-- Accounts, magic-link tokens and sessions (ADR 0030, decision 4).
-- A fingerprint is stored, never a secret: see content/identity/credentials.py.
-- A self-hosted instance creates no row in any of these three — `local` is a
-- real owner id that simply never had to sign in.
CREATE TABLE IF NOT EXISTS users (
    owner_id      TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,   -- normalized: trimmed, lower-cased
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL DEFAULT '',
    -- Operating the installation is a PRIVILEGE, not a bigger share of the
    -- data: an operator owns their own rows like anyone else and additionally
    -- may read facts about the machine. Hence a flag on the account rather
    -- than a second kind of credential — one sign-in, two capabilities.
    is_operator   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash  TEXT PRIMARY KEY,         -- sha256 of the token, never the token
    email       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT NOT NULL DEFAULT ''  -- non-empty = burnt, never reusable
);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_email
    ON auth_tokens(email, created_at);
CREATE TABLE IF NOT EXISTS sessions (
    session_hash  TEXT PRIMARY KEY,       -- sha256 of the cookie value
    owner_id      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    revoked_at    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_owner
    ON sessions(owner_id, created_at);

-- Named API keys (ADR 0030, decision 6). A program cannot hold a session
-- cookie, so it holds one of these instead. Same rule as everywhere else here:
-- the fingerprint is stored, never the key.
--
-- `id` exists so a key can be revoked by name from a list; the hash is what a
-- request is looked up by, and is indexed for it. Looking a key up by its
-- fingerprint is also what makes a wrong key cost the same as a right one.
CREATE TABLE IF NOT EXISTS api_keys (
    id            TEXT PRIMARY KEY,
    key_hash      TEXT NOT NULL UNIQUE,
    owner_id      TEXT NOT NULL,
    name          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT NOT NULL DEFAULT '',
    revoked_at    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_api_keys_owner
    ON api_keys(owner_id, created_at);
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
    # 5: client uploads (ADR 0020). last_referenced_at, not created_at: the TTL
    # counts from the last time a job used it, so a retry still finds its input.
    [
        "CREATE TABLE IF NOT EXISTS uploads ("
        "  id TEXT PRIMARY KEY,"
        "  filename TEXT NOT NULL,"
        "  media_type TEXT NOT NULL DEFAULT '',"
        "  size_bytes INTEGER NOT NULL DEFAULT 0,"
        "  sha256 TEXT NOT NULL DEFAULT '',"
        "  path TEXT NOT NULL,"
        "  created_at TEXT NOT NULL,"
        "  last_referenced_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_uploads_referenced "
        "ON uploads(last_referenced_at)",
    ],
    # 6: ownership (ADR 0030). Every user-facing row gains an owner.
    #
    # The default is 'local' on purpose and it is the whole migration story:
    # an existing self-hosted database already holds exactly one user's data,
    # so backfilling it to the single implicit user is correct, instantaneous
    # and needs no data pass. Nothing to rewrite, nothing to guess.
    #
    # `analyses` is deliberately NOT owned: it caches facts about a public
    # resource (a URL's title, duration, formats), keyed by resource_key. It
    # holds nothing of the requester and sharing it across users is a real
    # saving. `analysis_records` IS owned — it holds the caller's own
    # normalized sources (ADR 0014).
    [
        "ALTER TABLE jobs ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'local'",
        "ALTER TABLE artifacts ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'local'",
        "ALTER TABLE uploads ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'local'",
        "ALTER TABLE analysis_records "
        "ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'local'",
        "CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_owner "
        "ON artifacts(owner_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_uploads_owner ON uploads(owner_id, created_at)",
    ],
    # 7: accounts, magic-link tokens and sessions (ADR 0030, decision 4).
    #
    # Three tables and one rule: **a fingerprint is stored, never a secret**
    # (content/identity/credentials.py). A leaked backup must be a list of
    # useless hashes, not a working keyring.
    #
    # `users` is the only place an email lives. It is deliberately separate
    # from `owner_id`: the owner id is what every other table joins on and it
    # never changes, while an address can be corrected without rewriting a
    # single row anywhere else.
    #
    # A self-hosted instance creates no row here at all. `local` is a real
    # owner id that simply never signed in — there is no account behind it,
    # and none is invented.
    [
        "CREATE TABLE IF NOT EXISTS users ("
        "  owner_id TEXT PRIMARY KEY,"
        "  email TEXT NOT NULL UNIQUE,"
        "  created_at TEXT NOT NULL,"
        "  last_seen_at TEXT NOT NULL DEFAULT '')",
        "CREATE TABLE IF NOT EXISTS auth_tokens ("
        "  token_hash TEXT PRIMARY KEY,"
        "  email TEXT NOT NULL,"
        "  created_at TEXT NOT NULL,"
        "  expires_at TEXT NOT NULL,"
        "  used_at TEXT NOT NULL DEFAULT '')",
        "CREATE INDEX IF NOT EXISTS idx_auth_tokens_email "
        "ON auth_tokens(email, created_at)",
        "CREATE TABLE IF NOT EXISTS sessions ("
        "  session_hash TEXT PRIMARY KEY,"
        "  owner_id TEXT NOT NULL,"
        "  created_at TEXT NOT NULL,"
        "  expires_at TEXT NOT NULL,"
        "  last_seen_at TEXT NOT NULL,"
        "  revoked_at TEXT NOT NULL DEFAULT '')",
        "CREATE INDEX IF NOT EXISTS idx_sessions_owner "
        "ON sessions(owner_id, created_at)",
    ],
    # 8: named API keys (ADR 0030, decision 6).
    [
        "CREATE TABLE IF NOT EXISTS api_keys ("
        "  id TEXT PRIMARY KEY,"
        "  key_hash TEXT NOT NULL UNIQUE,"
        "  owner_id TEXT NOT NULL,"
        "  name TEXT NOT NULL,"
        "  created_at TEXT NOT NULL,"
        "  last_used_at TEXT NOT NULL DEFAULT '',"
        "  revoked_at TEXT NOT NULL DEFAULT '')",
        "CREATE INDEX IF NOT EXISTS idx_api_keys_owner "
        "ON api_keys(owner_id, created_at)",
    ],
    # 9: who may operate the installation.
    [
        "ALTER TABLE users ADD COLUMN is_operator INTEGER NOT NULL DEFAULT 0",
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
        owner_id: str,
        request: dict,
        failure_policy: str,
        idempotency_key: str | None,
        retry_of: str = "",
    ) -> str:
        job_id = new_id("job")
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO jobs (id, owner_id, status, request, "
                    "failure_policy, idempotency_key, retry_of, created_at) "
                    "VALUES (?, ?, 'created', ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        owner_id,
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

    def get_job(self, owner_id: str, job_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ? AND owner_id = ?",
                (job_id, owner_id),
            ).fetchone()
            return self._job_row(row) if row else None

    def list_jobs(
        self, owner_id: str, status: str | None = None, limit: int = 200
    ) -> list[dict]:
        query, params = "SELECT * FROM jobs WHERE owner_id = ?", [owner_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [self._job_row(r) for r in conn.execute(query, params).fetchall()]

    def find_job_by_idempotency_key(self, owner_id: str, key: str) -> dict | None:
        """Latest job holding *key* that is not terminally failed/cancelled
        (those release the key — docs/contract.md D6)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ? AND owner_id = ? "
                "AND status NOT IN ('failed', 'cancelled') "
                "ORDER BY created_at DESC LIMIT 1",
                (key, owner_id),
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

    def request_cancel(self, owner_id: str, job_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE jobs SET cancel_requested = 1 "
                "WHERE id = ? AND owner_id = ? AND status IN "
                "('created', 'validating', 'planning', 'queued', 'running')",
                (job_id, owner_id),
            )
            # Not-yet-running jobs cancel immediately.
            conn.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ? "
                "WHERE id = ? AND owner_id = ? AND status IN "
                "('created', 'validating', 'planning', 'queued')",
                (utcnow(), job_id, owner_id),
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

    def list_steps(self, owner_id: str, job_id: str) -> list[dict]:
        """Steps of a job the caller owns.

        `job_steps` carries no owner column on purpose: a step has no identity
        of its own, it belongs to a job. The isolation is therefore a join on
        `jobs`, which keeps a single source of truth for ownership — one row to
        change if a job ever moves hands.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT s.* FROM job_steps s JOIN jobs j ON j.id = s.job_id "
                "WHERE s.job_id = ? AND j.owner_id = ? ORDER BY s.rowid",
                (job_id, owner_id),
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
        self, owner_id: str, job_id: str, after_sequence: int = 0, limit: int = 1000
    ) -> list[dict]:
        """Events of a job the caller owns — same join rationale as
        :meth:`list_steps`."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT e.* FROM job_events e JOIN jobs j ON j.id = e.job_id "
                "WHERE e.job_id = ? AND j.owner_id = ? AND e.sequence > ? "
                "ORDER BY e.sequence LIMIT ?",
                (job_id, owner_id, after_sequence, limit),
            ).fetchall()
            return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    # --- artifacts -------------------------------------------------------------

    def register_artifact(self, owner_id: str, artifact: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO artifacts (id, owner_id, job_id, "
                "artifact_request_id, type, "
                "filename, display_filename, media_type, size_bytes, checksum, "
                "resource_key, step_signature, provenance, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact["id"],
                    owner_id,
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

    def set_artifact_delivered(
        self, owner_id: str, artifact_id: str, delivered_path: str
    ) -> None:
        """Record where a delivered copy landed, relative to the delivery
        root. Written after the copy succeeded — the row exists either way,
        the path only when the file really is there."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE artifacts SET delivered_path = ? WHERE id = ? AND owner_id = ?",
                (delivered_path, artifact_id, owner_id),
            )

    def find_reusable_artifact_group(
        self, owner_id: str, step_signature: str, exclude_job_id: str
    ) -> list[dict]:
        """The complete product set of the most recent other job whose step had
        this signature (artifacts only exist for succeeded steps)."""
        if not step_signature:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                # Reuse never crosses an owner: someone else's rendered file
                # is someone else's file, however identical the recipe.
                "SELECT * FROM artifacts WHERE step_signature = ? AND job_id != ? "
                "AND owner_id = ? ORDER BY created_at DESC",
                (step_signature, exclude_job_id, owner_id),
            ).fetchall()
        if not rows:
            return []
        newest_job = rows[0]["job_id"]
        return [self._artifact_row(r) for r in rows if r["job_id"] == newest_job]

    def list_artifacts(self, owner_id: str, job_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE job_id = ? AND owner_id = ? "
                "ORDER BY created_at",
                (job_id, owner_id),
            ).fetchall()
            return [self._artifact_row(r) for r in rows]

    def artifact_labels(self, owner_id: str, job_ids: list[str]) -> dict[str, dict]:
        """First artifact name + artifact count per job, in one query.

        Feeds the jobs list so a client can label rows with a human name
        ("Me at the zoo") without one artifacts fetch per row. The display
        name (the engine-computed, human-facing one — ADR 0017) wins over the
        internal filename.
        """
        if not job_ids:
            return {}
        marks = ",".join("?" * len(job_ids))
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT job_id, COALESCE(NULLIF(display_filename, ''), filename) "
                f"AS name FROM artifacts WHERE job_id IN ({marks}) "
                "AND owner_id = ? ORDER BY created_at, rowid",
                [*job_ids, owner_id],
            ).fetchall()
        labels: dict[str, dict] = {}
        for row in rows:
            entry = labels.setdefault(
                row["job_id"], {"artifact_name": row["name"], "artifact_count": 0}
            )
            entry["artifact_count"] += 1
        return labels

    def get_artifact(self, owner_id: str, artifact_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE id = ? AND owner_id = ?",
                (artifact_id, owner_id),
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
        owner_id: str,
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
                "(analysis_id, owner_id, sources, resource_keys, "
                "analyzer_version, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    analysis_id,
                    owner_id,
                    json.dumps(sources),
                    json.dumps(resource_keys),
                    analyzer_version,
                    created_at,
                    expires_at,
                ),
            )

    def load_analysis_record(self, owner_id: str, analysis_id: str) -> dict | None:
        """The addressable record, or None if there is no such id. Facts are not
        joined here — that is the service's job (it references the facts cache)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_records WHERE analysis_id = ? AND owner_id = ?",
                (analysis_id, owner_id),
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

    # --- uploads (ADR 0020) -----------------------------------------------------

    def register_upload(self, owner_id: str, row: dict) -> None:
        """Record an upload that is fully written and addressable."""
        now = utcnow()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO uploads (id, owner_id, filename, media_type, "
                "size_bytes, sha256, path, created_at, last_referenced_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    owner_id,
                    row["filename"],
                    row.get("media_type", ""),
                    row.get("size_bytes", 0),
                    row.get("sha256", ""),
                    str(row["path"]),
                    now,
                    now,
                ),
            )

    def get_upload(self, owner_id: str, upload_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM uploads WHERE id = ? AND owner_id = ?",
                (upload_id, owner_id),
            ).fetchone()
        return dict(row) if row else None

    def touch_upload(self, owner_id: str, upload_id: str) -> None:
        """Restart the expiry clock: this upload was just referenced by a job."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE uploads SET last_referenced_at = ? "
                "WHERE id = ? AND owner_id = ?",
                (utcnow(), upload_id, owner_id),
            )

    def expired_uploads(self, before: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM uploads WHERE last_referenced_at < ?", (before,)
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_upload(self, owner_id: str, upload_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM uploads WHERE id = ? AND owner_id = ?",
                (upload_id, owner_id),
            )

    def upload_exists_any_owner(self, upload_id: str) -> bool:
        """Does this id exist at all, whoever owns it? Housekeeping only.

        The TTL sweeper walks directories on disk and must tell an orphan
        directory from one a row still points at. Asking "does anyone own
        this?" is not a read of anybody's data — it returns a boolean and
        never a row — and it is named so that its two callers are greppable.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM uploads WHERE id = ?", (upload_id,)
            ).fetchone()
        return row is not None

    def delete_upload_any_owner(self, upload_id: str) -> None:
        """Housekeeping deletion, used by the TTL sweeper only.

        The sweeper is infrastructure: it acts on nobody's behalf and must be
        able to reclaim an expired upload whoever owns it. It is named
        explicitly so that an owner-less delete can never be reached by
        accident from a request path — grep finds exactly one caller.
        """
        with self._conn() as conn:
            conn.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))

    # --- accounts, tokens and sessions (ADR 0030, decision 4) ------------------
    #
    # ⚠️ These methods deliberately do NOT take `owner_id` first, and they are
    # the only ones in this class that do not. Everything else in the Store
    # receives an identity because identity has already been established;
    # these are the methods that *establish* it. A method here that took an
    # owner id would be asking the caller for the answer it exists to produce.
    #
    # They are grouped and named so that grep finds them as a set: nothing
    # outside content/api/auth.py and the auth routes should call them.

    def account_for_email(self, email: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT owner_id, email, created_at, last_seen_at, is_operator "
                "FROM users WHERE email = ?",
                (email,),
            ).fetchone()
        return dict(row) if row else None

    def account_for_owner(self, owner_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT owner_id, email, created_at, last_seen_at, is_operator "
                "FROM users WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_account(self, owner_id: str, email: str) -> dict:
        """Create the account for an address, or return the one that exists.

        Racing sign-ins for the same new address are ordinary — someone clicks
        twice, or two links arrive together — so a UNIQUE violation is a normal
        outcome here, not an error: the loser reads back the winner's row.
        """
        now = utcnow()
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO users (owner_id, email, created_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?)",
                    (owner_id, email, now, now),
                )
        except sqlite3.IntegrityError:
            existing = self.account_for_email(email)
            if existing is None:
                raise
            return existing
        account = self.account_for_email(email)
        assert account is not None
        return account

    def touch_account(self, owner_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET last_seen_at = ? WHERE owner_id = ?",
                (utcnow(), owner_id),
            )

    def set_operator(self, owner_id: str, is_operator: bool) -> None:
        """Grant or withdraw the privilege of operating this installation."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET is_operator = ? WHERE owner_id = ?",
                (1 if is_operator else 0, owner_id),
            )

    def is_operator(self, owner_id: str) -> bool:
        """May this owner see facts about the machine?

        `local` always may, and that is not a branch on the deployment mode: a
        self-hosted instance has exactly one user, who is by definition the
        person running it. Nothing here asks "am I hosted".
        """
        if owner_id == LOCAL_OWNER:
            return True
        with self._conn() as conn:
            row = conn.execute(
                "SELECT is_operator FROM users WHERE owner_id = ?", (owner_id,)
            ).fetchone()
        return bool(row and row["is_operator"])

    def create_auth_token(self, token_hash: str, email: str, expires_at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO auth_tokens (token_hash, email, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token_hash, email, utcnow(), expires_at),
            )

    def burn_auth_token(self, token_hash: str, now: str) -> dict | None:
        """Claim a token exactly once, and say which address it was for.

        The check and the burn are one statement under one transaction: a
        token that arrives twice — a double click, a mail client prefetching
        the link, a replay — finds `used_at` already set and gets nothing. A
        read followed by an update would leave a window in which both callers
        see an unused token, which on a sign-in endpoint means two sessions
        from one link.
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT token_hash, email, expires_at FROM auth_tokens "
                "WHERE token_hash = ? AND used_at = '' AND expires_at > ?",
                (token_hash, now),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE auth_tokens SET used_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )
            conn.execute("COMMIT")
        return dict(row)

    def count_recent_auth_tokens(self, email: str, since: str) -> int:
        """How many links this address asked for lately — the rate limit."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM auth_tokens "
                "WHERE email = ? AND created_at > ?",
                (email, since),
            ).fetchone()
        return int(row["n"])

    def delete_expired_auth_tokens(self, now: str) -> int:
        """Housekeeping: a spent or stale token is a hash of nothing useful,
        but it still occupies a row and names an address."""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM auth_tokens WHERE expires_at <= ? OR used_at != ''",
                (now,),
            )
        return cursor.rowcount

    def create_session(self, session_hash: str, owner_id: str, expires_at: str) -> None:
        now = utcnow()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (session_hash, owner_id, created_at, "
                "expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                (session_hash, owner_id, now, expires_at, now),
            )

    def live_session(self, session_hash: str, now: str) -> dict | None:
        """The live session behind a cookie, or None.

        A session row exists so that a session can be *revoked*: a cookie a
        client still holds stops working the moment the row says so. That is
        the whole reason the session is not a self-contained signed token.

        `last_seen_at` comes back with it so the caller can decide whether the
        expiry is worth sliding — writing on every single request would turn a
        read-only page view into a database write.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT owner_id, expires_at, last_seen_at FROM sessions "
                "WHERE session_hash = ? AND revoked_at = '' AND expires_at > ?",
                (session_hash, now),
            ).fetchone()
        return dict(row) if row else None

    def touch_session(self, session_hash: str, expires_at: str) -> None:
        """Slide the expiry forward for a session in active use."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET last_seen_at = ?, expires_at = ? "
                "WHERE session_hash = ? AND revoked_at = ''",
                (utcnow(), expires_at, session_hash),
            )

    def revoke_session(self, session_hash: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE session_hash = ? "
                "AND revoked_at = ''",
                (utcnow(), session_hash),
            )

    def revoke_all_sessions(self, owner_id: str) -> int:
        """Sign this account out everywhere. The answer to a lost laptop."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE owner_id = ? "
                "AND revoked_at = ''",
                (utcnow(), owner_id),
            )
        return cursor.rowcount

    def create_api_key(
        self, key_id: str, key_hash: str, owner_id: str, name: str
    ) -> dict:
        now = utcnow()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO api_keys (id, key_hash, owner_id, name, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key_id, key_hash, owner_id, name, now),
            )
        return {
            "id": key_id,
            "name": name,
            "created_at": now,
            "last_used_at": "",
        }

    def api_key_owner(self, key_hash: str) -> dict | None:
        """The live key behind a presented secret, or None.

        Looked up by fingerprint rather than compared against a list, which is
        what makes a wrong key cost the same as a right one: an index lookup
        on a hash reveals nothing through timing about the secret.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, owner_id, last_used_at FROM api_keys "
                "WHERE key_hash = ? AND revoked_at = ''",
                (key_hash,),
            ).fetchone()
        return dict(row) if row else None

    def list_api_keys(self, owner_id: str) -> list[dict]:
        """This owner's keys. The secret is not here and cannot be."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at, last_used_at FROM api_keys "
                "WHERE owner_id = ? AND revoked_at = '' ORDER BY created_at",
                (owner_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def touch_api_key(self, key_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (utcnow(), key_id),
            )

    def revoke_api_key(self, owner_id: str, key_id: str) -> bool:
        """Revoke one key. Scoped to its owner, so an id from someone else's
        list is simply not found."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND owner_id = ? "
                "AND revoked_at = ''",
                (utcnow(), key_id, owner_id),
            )
        return cursor.rowcount > 0

    def delete_expired_sessions(self, now: str) -> int:
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE expires_at <= ? OR revoked_at != ''",
                (now,),
            )
        return cursor.rowcount
