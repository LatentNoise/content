"""The upgrade path: an existing install must survive gaining owners.

These tests describe what happens to Yann's own machine — a database and a job
tree that predate ADR 0030 — and to every self-hosted instance that pulls the
release. Nothing here is hypothetical: the schema backfill and the directory
move are the two halves of an upgrade that must not lose a byte.
"""

import sqlite3

from content.identity import LOCAL_OWNER
from content.persistence.store import Store
from content.storage.migrate_owner import migrate_jobs_to_owner

PRE_OWNERSHIP_SCHEMA = """
CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL, request TEXT NOT NULL,
 plan_id TEXT NOT NULL DEFAULT '', failure_policy TEXT NOT NULL DEFAULT 'required_only',
 idempotency_key TEXT, retry_of TEXT NOT NULL DEFAULT '',
 error TEXT NOT NULL DEFAULT '',
 cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 started_at TEXT, finished_at TEXT);
CREATE TABLE artifacts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
 artifact_request_id TEXT NOT NULL, type TEXT NOT NULL, filename TEXT NOT NULL,
 display_filename TEXT NOT NULL DEFAULT '', delivered_path TEXT NOT NULL DEFAULT '',
 media_type TEXT NOT NULL DEFAULT '', size_bytes INTEGER NOT NULL DEFAULT 0,
 checksum TEXT NOT NULL DEFAULT '', resource_key TEXT NOT NULL DEFAULT '',
 step_signature TEXT NOT NULL DEFAULT '', provenance TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL);
CREATE TABLE uploads (id TEXT PRIMARY KEY, filename TEXT NOT NULL,
 media_type TEXT NOT NULL DEFAULT '', size_bytes INTEGER NOT NULL DEFAULT 0,
 sha256 TEXT NOT NULL DEFAULT '', path TEXT NOT NULL, created_at TEXT NOT NULL,
 last_referenced_at TEXT NOT NULL);
CREATE TABLE analysis_records (analysis_id TEXT PRIMARY KEY, sources TEXT NOT NULL,
 resource_keys TEXT NOT NULL, analyzer_version TEXT NOT NULL, created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL);
CREATE TABLE analyses (id TEXT PRIMARY KEY, resource_key TEXT NOT NULL,
 payload TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def _pre_ownership_database(path):
    conn = sqlite3.connect(path)
    conn.executescript(PRE_OWNERSHIP_SCHEMA)
    conn.execute(
        "INSERT INTO jobs (id, status, request, created_at) "
        "VALUES ('job_old', 'succeeded', '{}', '2026-09-01T10:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO artifacts (id, job_id, artifact_request_id, type, filename, "
        "created_at) VALUES ('art_old', 'job_old', 'r1', 'video', 'a.mp4', "
        "'2026-09-01T10:05:00+00:00')"
    )
    conn.execute(
        "INSERT INTO uploads (id, filename, path, created_at, last_referenced_at) "
        "VALUES ('upl_old', 'x.mp3', '/data/uploads/x.mp3', "
        "'2026-09-01T10:00:00+00:00', '2026-09-01T10:00:00+00:00')"
    )
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()


def test_an_existing_database_keeps_its_rows_and_gains_an_owner(tmp_path):
    db = tmp_path / "content.db"
    _pre_ownership_database(db)

    store = Store(db)  # opening is what migrates

    assert store.get_job(LOCAL_OWNER, "job_old")["status"] == "succeeded"
    assert len(store.list_jobs(LOCAL_OWNER)) == 1
    assert store.get_artifact(LOCAL_OWNER, "art_old") is not None
    assert store.get_upload(LOCAL_OWNER, "upl_old") is not None


def test_the_migrated_rows_are_invisible_to_anyone_else(tmp_path):
    db = tmp_path / "content.db"
    _pre_ownership_database(db)
    store = Store(db)

    assert store.get_job("usr_someone", "job_old") is None
    assert store.list_jobs("usr_someone") == []
    assert store.get_artifact("usr_someone", "art_old") is None
    assert store.get_upload("usr_someone", "upl_old") is None


def test_opening_twice_is_harmless(tmp_path):
    db = tmp_path / "content.db"
    _pre_ownership_database(db)
    Store(db)
    store = Store(db)  # a second start must not re-run or fail
    assert store.get_job(LOCAL_OWNER, "job_old") is not None


def test_job_directories_move_under_their_owner(tmp_path):
    data = tmp_path / "data"
    artifact = data / "jobs" / "job_old" / "artifacts" / "a.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"bytes that must survive")
    (data / "tmp" / "job_old").mkdir(parents=True)

    result = migrate_jobs_to_owner(data)

    assert result["moved"] == 2 and result["failed"] == 0
    moved = data / "jobs" / LOCAL_OWNER / "job_old" / "artifacts" / "a.mp4"
    assert moved.read_bytes() == b"bytes that must survive"
    assert not (data / "jobs" / "job_old").exists()
    assert (data / "tmp" / LOCAL_OWNER / "job_old").is_dir()


def test_migrating_twice_moves_nothing_the_second_time(tmp_path):
    data = tmp_path / "data"
    (data / "jobs" / "job_old" / "artifacts").mkdir(parents=True)

    first = migrate_jobs_to_owner(data)
    second = migrate_jobs_to_owner(data)

    assert first["moved"] == 1
    assert second["moved"] == 0
    # The owner directory must never be filed under itself.
    assert not (data / "jobs" / LOCAL_OWNER / LOCAL_OWNER).exists()


def test_an_interrupted_migration_finishes_on_the_next_run(tmp_path):
    """Half-moved is the realistic crash: some jobs filed, some not."""
    data = tmp_path / "data"
    (data / "jobs" / LOCAL_OWNER / "job_already").mkdir(parents=True)
    (data / "jobs" / "job_pending" / "artifacts").mkdir(parents=True)

    result = migrate_jobs_to_owner(data)

    assert result["moved"] == 1
    assert (data / "jobs" / LOCAL_OWNER / "job_already").is_dir()
    assert (data / "jobs" / LOCAL_OWNER / "job_pending").is_dir()


def test_a_leftover_source_is_dropped_when_the_destination_already_holds_it(tmp_path):
    data = tmp_path / "data"
    (data / "jobs" / LOCAL_OWNER / "job_dup" / "artifacts").mkdir(parents=True)
    (data / "jobs" / LOCAL_OWNER / "job_dup" / "artifacts" / "kept.txt").write_text("x")
    (data / "jobs" / "job_dup").mkdir(parents=True)

    migrate_jobs_to_owner(data)

    assert not (data / "jobs" / "job_dup").exists()
    assert (data / "jobs" / LOCAL_OWNER / "job_dup" / "artifacts" / "kept.txt").exists()


def test_nothing_to_migrate_on_a_fresh_install(tmp_path):
    result = migrate_jobs_to_owner(tmp_path / "data")
    assert result == {"moved": 0, "failed": 0, "owner_id": LOCAL_OWNER}
