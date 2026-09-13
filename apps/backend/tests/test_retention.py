"""Freeing disk: deleting on purpose, and sweeping what nobody came back for.

A quota with no way to free space is a dead end — whoever reaches the ceiling
can never do anything again. Deletion is the other half of limiting, which is
why it arrives with the quotas rather than after them.

The rule guarded hardest here: **the delivery library is not swept**. Files
there have been renamed, filed into folders and added to playlists by a human.
An unattended job that removes a film from someone's collection is not a
feature, whatever the retention setting says.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.application import retention
from content.identity import LOCAL_OWNER
from content.storage.layout import JobStorage, delivery_root_for

OWNER = "usr_someone"


def _finished_job(store, settings, owner=OWNER, *, finished_at=None, bytes_=4096):
    """A job as the executor leaves one: bytes on disk and a row pointing at
    them. The row matters now — the sweep looks for jobs that still hold
    content, so a job without a registered artifact is nothing to reclaim."""
    job_id = store.create_job(owner, {"sources": []}, "fail_fast", None)
    storage = JobStorage.from_settings(settings, owner, job_id).ensure()
    (storage.artifacts / "video.mp4").write_bytes(b"x" * bytes_)
    _register(store, job_id, owner)
    _reach_running(store, job_id)
    store.transition_job(
        job_id,
        "succeeded",
        finished_at=(finished_at or datetime.now(timezone.utc).isoformat()),
    )
    return job_id, storage


def _register(store, job_id: str, owner: str = OWNER) -> str:
    """Register one artifact for a job, the way the executor does."""
    artifact_id = "art_" + job_id[-12:]
    store.register_artifact(
        owner,
        {
            "id": artifact_id,
            "job_id": job_id,
            "artifact_request_id": "a",
            "type": "video",
            "filename": "video.mp4",
            "display_filename": "My Film.mp4",
            "media_type": "video/mp4",
            "size_bytes": 4096,
            "checksum": "",
            "resource_key": "",
            "step_signature": "",
            "provenance": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return artifact_id


def _reach_running(store, job_id: str) -> None:
    """Walk the real state machine rather than jumping: a test that invents an
    illegal transition proves nothing about the code that forbids it."""
    for step in ("validating", "planning", "queued", "running"):
        store.transition_job(job_id, step)


# --- deleting on purpose --------------------------------------------------------


def test_deleting_removes_the_files_and_the_rows(settings, store):
    job_id, storage = _finished_job(store, settings)
    assert storage.root.exists()

    done = retention.delete_job(OWNER, job_id, store=store, settings=settings)
    assert done is not None and done.freed_bytes >= 4096
    assert not storage.root.exists()
    assert store.get_job(OWNER, job_id) is None


def test_another_owners_job_is_not_found(settings, store):
    job_id, storage = _finished_job(store, settings)
    assert (
        retention.delete_job("usr_other", job_id, store=store, settings=settings)
        is None
    )
    # Untouched, not merely refused.
    assert storage.root.exists()
    assert store.get_job(OWNER, job_id) is not None


def test_the_delivered_copy_is_kept_unless_asked(settings, store):
    """The default that protects a library someone organised."""
    scoped = replace(settings, delivery_scope="per_owner", storage_layout="per_user")
    job_id, _ = _finished_job(store, scoped)
    delivered = delivery_root_for(scoped, OWNER) / "My Film.mp4"
    delivered.parent.mkdir(parents=True)
    delivered.write_bytes(b"y" * 2048)

    retention.delete_job(OWNER, job_id, store=store, settings=scoped)
    assert delivered.exists()


def test_the_delivered_copy_goes_when_asked(settings, store):
    scoped = replace(settings, delivery_scope="per_owner", storage_layout="per_user")
    job_id, storage = _finished_job(store, scoped)
    delivered = delivery_root_for(scoped, OWNER) / "My Film.mp4"
    delivered.parent.mkdir(parents=True)
    delivered.write_bytes(b"y" * 2048)
    artifact_id = store.list_artifacts(OWNER, job_id)[0]["id"]
    store.set_artifact_delivered(OWNER, artifact_id, "My Film.mp4")

    done = retention.delete_job(
        OWNER, job_id, store=store, settings=scoped, include_delivered=True
    )
    assert done.delivered_removed == 1
    assert not delivered.exists()


def test_a_delivered_file_someone_moved_is_not_an_error(settings, store):
    """A library is for organising. Losing track of a file is expected."""
    scoped = replace(settings, delivery_scope="per_owner", storage_layout="per_user")
    job_id, _ = _finished_job(store, scoped)
    artifact_id = store.list_artifacts(OWNER, job_id)[0]["id"]
    store.set_artifact_delivered(OWNER, artifact_id, "gone/elsewhere.mp4")
    done = retention.delete_job(
        OWNER, job_id, store=store, settings=scoped, include_delivered=True
    )
    assert done is not None and done.delivered_removed == 0


# --- the unattended sweep -------------------------------------------------------


def test_nothing_is_swept_by_default(settings, store):
    """Upgrading must not start deleting data nobody asked to delete."""
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    _, storage = _finished_job(store, settings, finished_at=old)
    assert retention.sweep_all(store=store, settings=settings) == {
        "enabled": False,
        "jobs": 0,
        "freed_bytes": 0,
    }
    assert storage.root.exists()


def test_a_job_past_the_window_loses_its_bytes_and_keeps_its_history(settings, store):
    """The distinction this sweep exists for: the video goes, the record stays."""
    keeping = replace(settings, retention_days=7)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    job_id, storage = _finished_job(store, keeping, finished_at=old)
    (storage.snapshots / "plan.json").write_text("{}")

    result = retention.sweep_all(store=store, settings=keeping)

    assert result["jobs"] == 1 and result["freed_bytes"] >= 4096
    # The bytes are gone…
    assert not storage.artifacts.exists()
    # …and everything that makes the job answerable is not.
    assert store.get_job(OWNER, job_id) is not None
    assert (storage.snapshots / "plan.json").exists()
    artifact = store.list_artifacts(OWNER, job_id)[0]
    assert artifact["content_removed_at"]


def test_an_already_expired_job_is_not_swept_again(settings, store):
    """Otherwise the sweep walks the same directory on every tick, forever."""
    keeping = replace(settings, retention_days=7)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    _finished_job(store, keeping, finished_at=old)

    assert retention.sweep_all(store=store, settings=keeping)["jobs"] == 1
    assert retention.sweep_all(store=store, settings=keeping)["jobs"] == 0


def test_a_recent_job_is_left_alone(settings, store):
    keeping = replace(settings, retention_days=7)
    _, storage = _finished_job(store, keeping)
    assert retention.sweep_all(store=store, settings=keeping)["jobs"] == 0
    assert (storage.artifacts / "video.mp4").exists()


def test_a_running_job_is_never_swept_however_old(settings, store):
    """Age is not a reason to delete work from under itself."""
    keeping = replace(settings, retention_days=1)
    job_id = store.create_job(OWNER, {"sources": []}, "fail_fast", None)
    JobStorage.from_settings(keeping, OWNER, job_id).ensure()
    _reach_running(store, job_id)
    assert retention.sweep_all(store=store, settings=keeping)["jobs"] == 0
    assert store.get_job(OWNER, job_id) is not None


def test_the_sweep_never_touches_the_library(settings, store):
    """The rule this module exists to protect."""
    keeping = replace(
        settings,
        retention_days=7,
        delivery_scope="per_owner",
        storage_layout="per_user",
    )
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    job_id, _ = _finished_job(store, keeping, finished_at=old)
    delivered = delivery_root_for(keeping, OWNER) / "My Film.mp4"
    delivered.parent.mkdir(parents=True)
    delivered.write_bytes(b"y" * 2048)
    artifact_id = store.list_artifacts(OWNER, job_id)[0]["id"]
    store.set_artifact_delivered(OWNER, artifact_id, "My Film.mp4")

    retention.sweep_all(store=store, settings=keeping)
    assert delivered.exists()


def test_the_sweep_covers_every_owner(settings, store):
    keeping = replace(settings, retention_days=7)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _finished_job(store, keeping, owner="usr_a", finished_at=old)
    _finished_job(store, keeping, owner="usr_b", finished_at=old)
    assert retention.sweep_all(store=store, settings=keeping)["jobs"] == 2


# --- through the API ------------------------------------------------------------


@pytest.fixture
def client(settings, store, providers):
    app = create_app(settings, store=store, providers=providers, start_worker=False)
    with TestClient(app) as test_client:
        yield test_client


def test_deleting_frees_space_under_the_quota(client, settings, store):
    job_id, storage = _finished_job(store, settings, owner=LOCAL_OWNER)
    before = client.get("/api/v1/storage").json()["total_bytes"]
    assert before >= 4096

    assert client.delete(f"/api/v1/jobs/{job_id}").status_code == 204
    assert client.get("/api/v1/storage").json()["total_bytes"] < before
    assert not storage.root.exists()


def test_deleting_an_unknown_job_is_a_404(client):
    assert client.delete("/api/v1/jobs/job_nope").status_code == 404


def test_a_running_job_must_be_cancelled_first(client, settings, store):
    job_id = store.create_job(LOCAL_OWNER, {"sources": []}, "fail_fast", None)
    _reach_running(store, job_id)
    response = client.delete(f"/api/v1/jobs/{job_id}")
    # 409 and not 404: it exists, and the verb is wrong for its state.
    assert response.status_code == 409
    assert "cancel" in response.json()["detail"]


def test_expired_content_says_so_rather_than_reading_as_a_lost_file(
    client, settings, store
):
    """The one thing a caller can act on: expired means ask again, missing does
    not. Both are 410, and only the code tells them apart."""
    keeping = replace(settings, retention_days=7)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    job_id, _ = _finished_job(store, keeping, owner=LOCAL_OWNER, finished_at=old)
    artifact_id = store.list_artifacts(LOCAL_OWNER, job_id)[0]["id"]
    assert client.get(f"/api/v1/artifacts/{artifact_id}/content").status_code == 200

    retention.sweep_all(store=store, settings=keeping)

    response = client.get(f"/api/v1/artifacts/{artifact_id}/content")
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "artifact_content_expired"
    # The record answers for itself with no bytes behind it: that is the point
    # of expiring rather than deleting.
    assert client.get(f"/api/v1/artifacts/{artifact_id}").status_code == 200
