"""Cache inspection + purge for the console (prompt 02): list cached analyses
and drop them (DB rows + durable JSON files), never touching delivered artifacts.
"""

from fastapi.testclient import TestClient

from content.api.app import create_app
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from tests.conftest import FakeProvider


def test_store_lists_and_purges_analyses(store):
    store.save_analysis(
        "ana_1", "k1", {"resource": {"title": "A", "resource_type": "video"}}
    )
    store.save_analysis(
        "ana_2", "k2", {"resource": {"title": "B", "resource_type": "audio"}}
    )
    listed = store.list_analyses()
    assert {a["resource_key"] for a in listed} == {"k1", "k2"}
    assert {a["title"] for a in listed} == {"A", "B"}
    assert {a["resource_type"] for a in listed} == {"video", "audio"}

    assert store.purge_analyses() == 2
    assert store.list_analyses() == []


def test_cache_endpoints(settings, store):
    store.save_analysis("ana_1", "k1", {"resource": {"title": "Cached"}})
    app = create_app(
        settings,
        store=store,
        providers=ProviderRegistry(
            [FakeProvider()], processors=[TranscriptProcessor()]
        ),
        start_worker=False,
    )
    with TestClient(app) as client:
        body = client.get("/api/v1/cache").json()
        assert body["analyses"][0]["title"] == "Cached"
        assert "enabled" in body and "ttl_hours" in body

        purged = client.post("/api/v1/cache/purge").json()
        assert purged["purged_analyses"] == 1
        assert client.get("/api/v1/cache").json()["analyses"] == []
