"""A person's last request for a source (ADR 0039).

Pasting a video or a playlist again should find the choices made last time —
above all the folder it went to, because that folder is what lets Content see
what is already there instead of downloading it again. The engine keeps it per
person and per source, and a client reads it back to prefill its form.

Two properties carry the design. The source is named by what the site says it
is (`source_ref`), not by the provider's cache key, so an upgrade or another way
of writing the same link does not make the memory forget. And the memory is its
own record, so deleting a job to free space does not erase where a playlist
lives.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.domain.analysis import NormalizedResource, source_ref_of
from content.identity import LOCAL_OWNER
from tests.conftest import minimal_payload

# --- the name of a source ---------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "url", "provider_id", "expected"),
    [
        (
            "video",
            "https://www.youtube.com/watch?v=pXRviuL6vMY",
            "pXRviuL6vMY",
            "youtube.com:item:pXRviuL6vMY",
        ),
        (
            "video",
            "https://youtu.be/pXRviuL6vMY",
            "pXRviuL6vMY",
            "youtube.com:item:pXRviuL6vMY",
        ),
        (
            "video",
            "https://music.youtube.com/watch?v=pXRviuL6vMY",
            "pXRviuL6vMY",
            "youtube.com:item:pXRviuL6vMY",
        ),
        (
            "collection",
            "https://www.youtube.com/playlist?list=PLabc",
            "PLabc",
            "youtube.com:collection:PLabc",
        ),
        (
            "webpage",
            "https://Example.org/article/#top",
            "",
            "url:https://example.org/article",
        ),
        ("video", "", "", ""),
    ],
)
def test_a_source_is_named_by_what_the_site_says_it_is(
    kind, url, provider_id, expected
):
    resource = NormalizedResource(
        resource_type=kind, canonical_url=url, provider_id=provider_id
    )
    assert source_ref_of(resource) == expected


# --- the memory -------------------------------------------------------------------


def _video(folder: str, height: int, uri: str = "https://example.com/video") -> dict:
    return minimal_payload(
        sources=[{"id": "main", "type": "url", "uri": uri}],
        outputs=[
            {
                "id": "video_main",
                "type": "video",
                "options": {"selection": {"max_height": height}, "container": "mp4"},
                "delivery": {"folder": folder},
            }
        ],
    )


@pytest.fixture
def client(settings, store, providers):
    app = create_app(settings, store=store, providers=providers, start_worker=False)
    with TestClient(app) as test_client:
        yield test_client


def _ref(client) -> str:
    analysis = client.post(
        "/api/v1/analyses",
        json={
            "sources": [
                {"id": "main", "type": "url", "uri": "https://example.com/video"}
            ]
        },
    ).json()
    return analysis["sources"][0]["source_ref"]


def test_an_analysis_carries_the_stable_name(client):
    assert _ref(client) == "example.com:item:fake123"


def test_nothing_is_remembered_before_anything_was_asked(client):
    response = client.get("/api/v1/last-request", params={"source_ref": _ref(client)})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "last_request_not_found"


def test_what_was_asked_comes_back_with_its_folder_and_options(client):
    job = client.post("/api/v1/jobs", json=_video("Music/Twenty One Pilots", 720))
    assert job.status_code == 201

    memory = client.get(
        "/api/v1/last-request", params={"source_ref": _ref(client)}
    ).json()

    output = memory["request"]["outputs"][0]
    assert output["delivery"]["folder"] == "Music/Twenty One Pilots"
    assert output["options"]["selection"]["max_height"] == 720
    assert memory["title"] == "Fake conference"
    assert memory["job"]["job_id"] == job.json()["job_id"]


def test_the_newest_request_replaces_the_previous_one(client):
    client.post("/api/v1/jobs", json=_video("Old folder", 720))
    client.post("/api/v1/jobs", json=_video("New folder", 1080))
    memory = client.get(
        "/api/v1/last-request", params={"source_ref": _ref(client)}
    ).json()
    assert memory["request"]["outputs"][0]["delivery"]["folder"] == "New folder"


def test_deleting_the_job_does_not_forget_where_it_went(client, store):
    """Freeing space must not make Content forget which folder a playlist
    lives in — the memory is its own record, not a view of the jobs."""
    job_id = client.post("/api/v1/jobs", json=_video("Music/Keep", 720)).json()[
        "job_id"
    ]
    store.delete_job(LOCAL_OWNER, job_id)

    memory = client.get(
        "/api/v1/last-request", params={"source_ref": _ref(client)}
    ).json()
    assert memory["request"]["outputs"][0]["delivery"]["folder"] == "Music/Keep"
    assert memory["job"] is None


def test_one_persons_memory_is_never_anothers(store):
    store.remember_request("usr_a", "youtube.com:item:x", "job_a", "A", {"outputs": []})
    assert store.last_request("usr_b", "youtube.com:item:x") is None
    assert store.last_request("usr_a", "youtube.com:item:x")["job_id"] == "job_a"


def test_a_source_with_no_name_is_not_remembered(store):
    """An upload has no address to paste again, so nothing is kept for it."""
    store.remember_request("usr_a", "", "job_a", "A", {"outputs": []})
    assert store.last_request("usr_a", "") is None


def test_the_memory_is_owner_scoped_on_a_signed_in_instance(settings, store, providers):
    """Hosted: the route answers only for the caller's own sources."""
    hosted = replace(settings, auth_mode="token", storage_layout="per_user")
    app = create_app(hosted, store=store, providers=providers, start_worker=False)
    with TestClient(app) as anonymous:
        response = anonymous.get(
            "/api/v1/last-request", params={"source_ref": "example.com:item:fake123"}
        )
    assert response.status_code == 401
