"""Collecting expired uploads, and the properties 0.5.0 shipped untested.

ADR 0020 promised that unreferenced uploads become collectable after their TTL.
0.5.0 implemented only the *refusal* — resolving an expired upload answered
`upload_expired` — while the bytes stayed on disk forever. A documented TTL
that deletes nothing is a bug, so the sweep and its ordering are pinned here.

The rest of this file closes the §7 checklist of the original prompt: the
policies and guarantees that were claimed in prose and never asserted.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.application.uploads import (
    ORPHAN_GRACE_SECONDS,
    sweep_expired_uploads,
    upload_path,
)
from content.config import ContentSettings, uploads_root
from content.identity import LOCAL_OWNER
from content.persistence.store import Store
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.documents import DocumentProvider
from content.storage.layout import UploadStore
from content.storage.roots import owner_roots

MARKDOWN = b"# A title\n\nA paragraph long enough to be a document.\n"


@pytest.fixture
def settings(tmp_path) -> ContentSettings:
    return ContentSettings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
        max_upload_bytes=4096,
        upload_ttl_hours=24.0,
    )


@pytest.fixture
def client(settings):
    app = create_app(
        settings,
        providers=ProviderRegistry(
            [DocumentProvider()], processors=[TranscriptProcessor()]
        ),
        start_worker=False,
    )
    with TestClient(app) as test_client:
        yield test_client


def _upload(client, name="notes.md", body=MARKDOWN) -> str:
    response = client.post(
        "/api/v1/uploads", files={"file": (name, body, "text/markdown")}
    )
    assert response.status_code == 201, response.text
    return response.json()["upload_id"]


def _age_directory(directory: Path, seconds: float) -> None:
    """Backdate a directory's mtime — what the orphan grace period reads."""
    stamp = directory.stat().st_mtime - seconds
    os.utime(directory, (stamp, stamp))


def _age(store: Store, upload_id: str, hours: float) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with store._conn() as conn:  # noqa: SLF001 — the clock is the subject here
        conn.execute(
            "UPDATE uploads SET last_referenced_at = ? WHERE id = ?",
            (stamp, upload_id),
        )


# --- the sweep ------------------------------------------------------------------


def test_an_expired_upload_is_actually_deleted(client, settings):
    """The hole 0.5.0 left: refused at resolution, but never removed."""
    upload_id = _upload(client)
    store = Store(settings.db_path)
    directory = uploads_root(settings) / upload_id
    assert directory.is_dir()

    _age(store, upload_id, 48)
    result = sweep_expired_uploads(store, settings)

    assert result["removed"] == 1
    assert result["bytes_reclaimed"] == len(MARKDOWN)
    assert not directory.exists(), "the bytes are still on disk"
    assert store.get_upload(LOCAL_OWNER, upload_id) is None


def test_a_fresh_upload_survives_the_sweep(client, settings):
    upload_id = _upload(client)
    store = Store(settings.db_path)
    assert sweep_expired_uploads(store, settings)["removed"] == 0
    assert store.get_upload(LOCAL_OWNER, upload_id) is not None


def test_a_recent_reference_keeps_an_upload_out_of_the_sweep(client, settings):
    """The TTL runs from the last reference, so an upload a job just used is
    not swept out from under a retry."""
    upload_id = _upload(client)
    store = Store(settings.db_path)
    _age(store, upload_id, 20)  # within the 24 h TTL
    client.post(
        "/api/v1/analyses",
        json={"sources": [{"id": "s", "type": "upload", "upload_id": upload_id}]},
    )
    _age_check = store.get_upload(LOCAL_OWNER, upload_id)["last_referenced_at"]
    assert _age_check > (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert sweep_expired_uploads(store, settings)["removed"] == 0


def test_an_expired_upload_cannot_be_revived_by_referencing_it(client, settings):
    """Worth stating because the opposite would be a subtle bug: resolution
    refuses *before* it touches the clock, so a caller cannot keep bytes alive
    indefinitely by poking an upload the engine has already disowned."""
    upload_id = _upload(client)
    store = Store(settings.db_path)
    _age(store, upload_id, 48)

    refused = client.post(
        "/api/v1/analyses",
        json={"sources": [{"id": "s", "type": "upload", "upload_id": upload_id}]},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["errors"][0]["code"] == "upload_expired"
    assert sweep_expired_uploads(store, settings)["removed"] == 1


def test_an_orphan_directory_is_reclaimed(client, settings):
    """Self-healing: a crash between deleting the row and the directory leaves
    a directory nothing points at. A later sweep takes it.

    The directory is aged past `ORPHAN_GRACE_SECONDS` first, because a row-less
    directory that is merely *recent* is indistinguishable from an upload still
    being written — see `test_an_upload_in_flight_is_not_swept_as_an_orphan`.
    """
    upload_id = _upload(client)
    store = Store(settings.db_path)
    store.delete_upload(LOCAL_OWNER, upload_id)  # the crash, simulated
    directory = uploads_root(settings) / upload_id
    _age_directory(directory, ORPHAN_GRACE_SECONDS + 60)

    result = sweep_expired_uploads(store, settings)
    assert result["orphans"] == 1
    assert not directory.exists()


def test_a_recent_orphan_waits_for_the_next_sweep(client, settings):
    """The other side of the grace period: a fresh row-less directory is left
    alone. It is invisible and reclaimable, so one more tick costs nothing."""
    upload_id = _upload(client)
    store = Store(settings.db_path)
    store.delete_upload(LOCAL_OWNER, upload_id)

    assert sweep_expired_uploads(store, settings)["orphans"] == 0
    assert (uploads_root(settings) / upload_id).is_dir()


def test_an_upload_in_flight_is_not_swept_as_an_orphan(settings):
    """The defect the grace period exists for, at the layer it happened on.

    `create_upload` writes the bytes and registers the row afterwards, so for
    the length of the drain the directory exists and no row points at it. The
    sweep runs on its own 15-minute clock; when a tick landed in that window it
    deleted the directory under the writer, and the drain died on `os.replace`
    — a 500 on a perfectly valid upload.
    """
    store = Store(settings.db_path)
    uploads = UploadStore(owner_roots(settings, LOCAL_OWNER).uploads)

    def chunks():
        yield MARKDOWN[:10]
        sweep_expired_uploads(store, settings)  # the tick lands mid-stream
        yield MARKDOWN[10:]

    written = uploads.write_stream("upl_inflight", "notes.md", chunks(), limit=0)

    assert written["size_bytes"] == len(MARKDOWN)
    assert written["path"].exists(), "the sweep took an upload that was arriving"


def test_a_sweep_just_before_registration_leaves_the_bytes(settings, monkeypatch):
    """The narrower half of the same window, and the worse one.

    If the tick lands after the drain but before `register_upload`, the request
    still answers 201 — and the row it writes points at bytes the sweep has
    just deleted. A later job then meets a confusing "file not found" instead
    of the honest `upload_expired`, which is precisely what this module's
    row-first ordering exists to prevent.
    """
    sweep_store = Store(settings.db_path)
    register = Store.register_upload

    def register_after_a_sweep(self, owner_id, record):
        sweep_expired_uploads(sweep_store, settings)
        return register(self, owner_id, record)

    monkeypatch.setattr(Store, "register_upload", register_after_a_sweep)

    app = create_app(
        settings,
        providers=ProviderRegistry([DocumentProvider()]),
        start_worker=False,
    )
    with TestClient(app) as client:
        upload_id = _upload(client)

    row = Store(settings.db_path).get_upload(LOCAL_OWNER, upload_id)
    assert row is not None
    assert upload_path(LOCAL_OWNER, row, settings).exists(), (
        "the row points at bytes the sweep deleted"
    )


def test_a_ttl_of_zero_disables_collection(client, settings):
    """0 means "never expire" — an operator keeping uploads deliberately must
    not have them swept."""
    import dataclasses

    upload_id = _upload(client)
    store = Store(settings.db_path)
    _age(store, upload_id, 10_000)
    never = dataclasses.replace(settings, upload_ttl_hours=0)
    assert sweep_expired_uploads(store, never)["removed"] == 0
    assert store.get_upload(LOCAL_OWNER, upload_id) is not None


def test_sweeping_an_empty_store_is_harmless(settings):
    store = Store(settings.db_path)
    assert sweep_expired_uploads(store, settings) == {
        "removed": 0,
        "orphans": 0,
        "bytes_reclaimed": 0,
    }


# --- the §7 gaps ----------------------------------------------------------------


def test_a_zero_byte_upload_is_accepted(client):
    """Decided policy: an empty file is a legitimate thing to send. It analyses
    to nothing useful, which is the source's problem, not the endpoint's —
    refusing it would be a special case earning nothing."""
    response = client.post(
        "/api/v1/uploads", files={"file": ("empty.txt", b"", "text/plain")}
    )
    assert response.status_code == 201
    assert response.json()["size_bytes"] == 0


def test_the_quota_refuses_further_uploads(tmp_path):
    import dataclasses

    tight = ContentSettings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
        uploads_total_bytes=len(MARKDOWN),  # room for exactly one
    )
    app = create_app(
        tight, providers=ProviderRegistry([DocumentProvider()]), start_worker=False
    )
    with TestClient(app) as client:
        _upload(client)
        second = client.post(
            "/api/v1/uploads", files={"file": ("again.md", MARKDOWN, "text/markdown")}
        )
        assert second.status_code == 507
        assert second.json()["detail"]["code"] == "upload_quota_exceeded"
        assert dataclasses.is_dataclass(tight)


def test_one_upload_can_feed_several_jobs(client):
    """The headline claim of an independently addressable resource."""
    upload_id = _upload(client)
    source = [{"id": "s", "type": "upload", "upload_id": upload_id}]
    for _ in range(2):
        response = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": source,
                "outputs": [{"id": "m", "type": "markdown"}],
            },
        )
        assert response.status_code == 201, response.text


def test_uploads_are_immutable(client, settings):
    """A second upload never lands on an existing id's bytes."""
    first = _upload(client, body=b"# One\n\nfirst.\n")
    second = _upload(client, body=b"# Two\n\nsecond.\n")
    assert first != second
    store = Store(settings.db_path)
    assert (
        store.get_upload(LOCAL_OWNER, first)["sha256"]
        != store.get_upload(LOCAL_OWNER, second)["sha256"]
    )


def test_no_api_response_leaks_a_filesystem_path(client, settings):
    """Resolution turns an upload into a file on disk. If any response carried
    that path, the opaque id would stop being an indirection at all."""
    upload_id = _upload(client)
    source = [{"id": "s", "type": "upload", "upload_id": upload_id}]
    root = str(settings.data_dir.resolve())

    responses = [
        client.post(
            "/api/v1/uploads", files={"file": ("a.md", MARKDOWN, "text/markdown")}
        ),
        client.get(f"/api/v1/uploads/{upload_id}"),
        client.post("/api/v1/analyses", json={"sources": source}),
        client.post("/api/v1/capabilities", json={"sources": source}),
        client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": source,
                "outputs": [{"id": "m", "type": "markdown"}],
            },
        ),
    ]
    job_id = responses[-1].json().get("job_id")
    if job_id:
        responses.append(client.get(f"/api/v1/jobs/{job_id}"))

    for response in responses:
        assert root not in response.text, f"a server path leaked: {response.url}"
