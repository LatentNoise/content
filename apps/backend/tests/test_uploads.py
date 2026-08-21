"""Client uploads: the endpoint, the boundary, and the resolution (ADR 0020).

Two properties carry this feature, and most of what follows defends them.

**Upload is acquisition, never processing.** Once resolved, an uploaded file
behaves exactly like the same file sitting on the engine's disk — same
analysis, same capabilities, same planner. Nothing downstream may learn that
uploads exist.

**Uploads and `file` sources are governed separately.** `CONTENT_ALLOWED_INPUT_ROOTS`
is operator policy for `file`; the upload store is engine-owned. An earlier
attempt merged the two and silently repealed the policy — with no roots
configured, `file` sources must stay refused as *valid but not supported*,
while uploads keep working.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.config import ContentSettings
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.documents import DocumentProvider

MARKDOWN = b"# A title\n\nA paragraph that is long enough to be a document.\n"


@pytest.fixture
def settings(tmp_path) -> ContentSettings:
    """Deliberately no allowed input roots: `file` sources are off."""
    return ContentSettings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
        max_upload_bytes=4096,
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


def _upload(client, name: str = "notes.md", body: bytes = MARKDOWN) -> str:
    response = client.post(
        "/api/v1/uploads", files={"file": (name, body, "text/markdown")}
    )
    assert response.status_code == 201, response.text
    return response.json()["upload_id"]


# --- the endpoint ---------------------------------------------------------------


def test_an_upload_answers_with_its_identity_and_never_its_path(client):
    body = client.post(
        "/api/v1/uploads", files={"file": ("notes.md", MARKDOWN, "text/markdown")}
    ).json()
    assert body["upload_id"].startswith("upl_")
    assert body["size_bytes"] == len(MARKDOWN)
    assert body["sha256"].startswith("sha256:")
    # The id is the address; a filesystem path would leak the layout and
    # invite callers to construct their own.
    assert "path" not in body


def test_the_size_limit_is_enforced_and_leaves_nothing_behind(client, settings):
    response = client.post(
        "/api/v1/uploads",
        files={"file": ("big.bin", b"x" * 5000, "application/octet-stream")},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "upload_too_large"
    root = settings.data_dir / "uploads"
    assert not any(p.is_file() for p in root.rglob("*")), "a rejected upload was kept"


def test_deleting_is_idempotent(client):
    upload_id = _upload(client)
    assert client.delete(f"/api/v1/uploads/{upload_id}").status_code == 204
    assert client.get(f"/api/v1/uploads/{upload_id}").status_code == 404
    # Deleting again still succeeds: the caller's intent already holds.
    assert client.delete(f"/api/v1/uploads/{upload_id}").status_code == 204


# --- resolution: an upload is just a file -------------------------------------


def test_an_uploaded_file_analyses_like_any_other(client):
    upload_id = _upload(client)
    response = client.post(
        "/api/v1/analyses",
        json={"sources": [{"id": "s", "type": "upload", "upload_id": upload_id}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sources"][0]["resource"]["resource_type"] == "document"


def test_an_uploaded_file_can_be_submitted_as_a_job(client):
    upload_id = _upload(client)
    response = client.post(
        "/api/v1/jobs",
        json={
            "schema_version": "1.0",
            "sources": [{"id": "s", "type": "upload", "upload_id": upload_id}],
            "outputs": [{"id": "m", "type": "markdown"}],
        },
    )
    assert response.status_code == 201, response.text


def test_an_uploaded_file_runs_to_a_finished_artifact(client, settings):
    """The test above stops at 201 — the job is *accepted*. That gap hid a real
    defect: an upload analysed fine and then failed in its first step with
    "File sources are disabled", because the execution-time path check forgot
    the engine's own uploads directory while the analysis-time one remembered
    it. An upload that cannot be executed is not an upload feature.

    Found by driving the published MCP server against a real engine, which is
    the only place a job actually runs to completion from an uploaded file.
    """
    from content.execution.executor import JobExecutor
    from content.persistence.store import Store

    upload_id = _upload(client)
    response = client.post(
        "/api/v1/jobs",
        json={
            "schema_version": "1.0",
            "sources": [{"id": "s", "type": "upload", "upload_id": upload_id}],
            "outputs": [{"id": "m", "type": "markdown"}],
        },
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    store = Store(settings.db_path)
    executor = JobExecutor(
        store=store,
        settings=settings,
        providers=ProviderRegistry(
            [DocumentProvider()], processors=[TranscriptProcessor()]
        ),
    )
    executor.execute(store.claim_next_queued())

    job = store.get_job(job_id)
    assert job["status"] == "succeeded", job.get("error")
    artifacts = store.list_artifacts(job_id)
    assert artifacts, "the upload produced no artifact"


def test_capabilities_answer_for_an_upload(client):
    upload_id = _upload(client)
    response = client.post(
        "/api/v1/capabilities",
        json={"sources": [{"id": "s", "type": "upload", "upload_id": upload_id}]},
    )
    assert response.status_code == 200, response.text
    statuses = {
        c["id"]: c["status"] for c in response.json()["sources"][0]["capabilities"]
    }
    assert statuses, "an upload must resolve capabilities like any source"


# --- the two policies stay apart ------------------------------------------------


def test_uploads_work_while_file_sources_are_disabled(client, tmp_path):
    """The property the earlier attempt broke, stated directly."""
    upload_id = _upload(client)
    assert (
        client.post(
            "/api/v1/analyses",
            json={"sources": [{"id": "s", "type": "upload", "upload_id": upload_id}]},
        ).status_code
        == 200
    )
    refused = client.post(
        "/api/v1/analyses",
        json={"sources": [{"id": "s", "type": "file", "path": str(tmp_path / "x.md")}]},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["errors"][0]["code"] == "source_type_not_supported"


# --- refusals say which thing went wrong ----------------------------------------


def test_an_unknown_id_is_not_the_same_answer_as_an_unsupported_type(client):
    response = client.post(
        "/api/v1/analyses",
        json={"sources": [{"id": "s", "type": "upload", "upload_id": "upl_nope"}]},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0]["code"] == "upload_not_found"


def test_an_expired_upload_says_so_rather_than_vanishing(client, tmp_path):
    """`upload_expired` tells a caller to upload again; `upload_not_found`
    tells them their reference is wrong. Answering both the same way would
    leave them guessing which."""
    from content.application.uploads import resolve_upload_sources
    from content.domain.request import UploadSource
    from content.persistence.store import Store

    upload_id = _upload(client)
    store = Store(tmp_path / "db.sqlite")
    with store._conn() as conn:  # noqa: SLF001 — the clock is the point of the test
        conn.execute(
            "UPDATE uploads SET last_referenced_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", upload_id),
        )
    expiring = ContentSettings(
        data_dir=tmp_path / "data", db_path=tmp_path / "db.sqlite", upload_ttl_hours=1.0
    )
    _, issues = resolve_upload_sources(
        [UploadSource(id="s", type="upload", upload_id=upload_id)], store, expiring
    )
    assert [i.code for i in issues] == ["upload_expired"]


def test_referencing_an_upload_restarts_its_clock(client, tmp_path):
    """The TTL runs from the last reference, so retrying a job never finds its
    input swept away."""
    from content.persistence.store import Store

    upload_id = _upload(client)
    store = Store(tmp_path / "db.sqlite")
    before = store.get_upload(upload_id)["last_referenced_at"]
    with store._conn() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE uploads SET last_referenced_at = ? WHERE id = ?",
            ("2001-01-01T00:00:00+00:00", upload_id),
        )
    client.post(
        "/api/v1/analyses",
        json={"sources": [{"id": "s", "type": "upload", "upload_id": upload_id}]},
    )
    after = store.get_upload(upload_id)["last_referenced_at"]
    assert after > "2001-01-01", "referencing an upload must refresh its clock"
    assert before is not None
