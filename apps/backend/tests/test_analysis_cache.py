"""URL analysis JSON cache: 3-day TTL, durable file source of truth (S9).

The DB caches analyses by resource key; the file cache under cache/analysis/
survives a DB reset and is only active when the cache is enabled.
"""

import json
import os
import time
from dataclasses import replace

import pytest

from content.analysis.cache import AnalysisJsonCache
from content.analysis.service import AnalysisService
from content.identity import LOCAL_OWNER
from content.persistence.store import Store
from content.providers.base import ProviderRegistry
from tests.conftest import FakeProvider, make_request, minimal_payload

# --- unit: AnalysisJsonCache ---------------------------------------------------


def test_json_cache_round_trip(tmp_path):
    cache = AnalysisJsonCache(tmp_path / "cache", ttl_hours=72)
    cache.save("ytdlp:url:abc", {"resource": {"title": "x"}})
    assert cache.load("ytdlp:url:abc") == {"resource": {"title": "x"}}


def test_json_cache_missing_key_returns_none(tmp_path):
    assert AnalysisJsonCache(tmp_path / "cache", 72).load("nope") is None


def test_json_cache_expired_entry_returns_none(tmp_path):
    cache = AnalysisJsonCache(tmp_path / "cache", ttl_hours=72)
    cache.save("k", {"a": 1})
    path = cache._path("k")
    old = time.time() - 73 * 3600
    os.utime(path, (old, old))
    assert cache.load("k") is None


# --- integration: service uses the file cache on a DB miss ---------------------


class CountingProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.analyze_calls = 0

    def analyze(self, source, ctx):
        self.analyze_calls += 1
        return super().analyze(source, ctx)


@pytest.fixture
def cache_settings(settings):
    return replace(settings, cache_enabled=True)


def test_analysis_persists_json_file_when_cache_enabled(cache_settings):
    store = Store(cache_settings.db_path)
    provider = CountingProvider()
    service = AnalysisService(store, ProviderRegistry([provider]), cache_settings)
    request = make_request(minimal_payload())

    service.analyze_sources(LOCAL_OWNER, list(request.sources))
    assert provider.analyze_calls == 1
    files = list((cache_settings.data_dir / "cache" / "analysis").glob("*.json"))
    assert len(files) == 1  # the URL JSON is persisted as a source of truth


def test_db_miss_is_served_from_file_cache_without_reprobing(cache_settings, tmp_path):
    request = make_request(minimal_payload())

    # First installation: probes once and writes the durable file cache.
    store1 = Store(cache_settings.db_path)
    p1 = CountingProvider()
    AnalysisService(store1, ProviderRegistry([p1]), cache_settings).analyze_sources(
        LOCAL_OWNER, list(request.sources)
    )
    assert p1.analyze_calls == 1

    # Fresh DB (reset), same data/cache dir → DB miss, file hit, no re-probe.
    settings2 = replace(cache_settings, db_path=tmp_path / "data" / "content2.db")
    store2 = Store(settings2.db_path)
    p2 = CountingProvider()
    result = AnalysisService(store2, ProviderRegistry([p2]), settings2).analyze_sources(
        LOCAL_OWNER, list(request.sources)
    )
    assert p2.analyze_calls == 0  # served from the durable JSON cache
    assert result.sources[0].resource.title == "Fake conference"


def test_db_hit_rewrites_missing_source_of_truth(cache_settings):
    store = Store(cache_settings.db_path)
    provider = CountingProvider()
    service = AnalysisService(store, ProviderRegistry([provider]), cache_settings)
    request = make_request(minimal_payload())
    service.analyze_sources(LOCAL_OWNER, list(request.sources))
    assert provider.analyze_calls == 1

    cache_dir = cache_settings.data_dir / "cache" / "analysis"
    for f in cache_dir.glob("*.json"):
        f.unlink()  # durable file lost, DB still warm

    service.analyze_sources(LOCAL_OWNER, list(request.sources))
    assert provider.analyze_calls == 1  # DB hit, no re-probe
    assert list(cache_dir.glob("*.json"))  # write-through recreated the file


def test_json_cache_non_positive_ttl_never_serves(tmp_path):
    """A TTL of 0 is "nothing is fresh", not "fresh forever" — the meaning
    `Store.load_fresh_analysis` gives it explicitly."""
    for ttl in (0.0, -1.0):
        cache = AnalysisJsonCache(tmp_path / f"cache{ttl}", ttl_hours=ttl)
        cache.save("k", {"a": 1})
        assert cache.load("k") is None  # fresh on disk, and still not served

        path = cache._path("k")
        old = time.time() - 10 * 86400
        os.utime(path, (old, old))
        assert cache.load("k") is None


def test_ttl_zero_does_not_serve_a_stale_file_nor_reseed_the_db(
    cache_settings, tmp_path
):
    """The two halves of one cache agreed on every TTL but the one that turns it
    off. With `analysis_ttl_hours=0` the DB refused on principle while the
    durable file answered whatever its age, re-probing never happened, and the
    stale payload was written back to the DB with a current `created_at`."""
    settings = replace(cache_settings, analysis_ttl_hours=0.0)
    request = make_request(minimal_payload())

    store = Store(settings.db_path)
    first = CountingProvider()
    AnalysisService(store, ProviderRegistry([first]), settings).analyze_sources(
        LOCAL_OWNER, list(request.sources)
    )
    assert first.analyze_calls == 1

    # Make the durable file say something the provider never would, then age it
    # by ten days: whatever comes back next names who answered.
    files = list((settings.data_dir / "cache" / "analysis").glob("*.json"))
    assert len(files) == 1  # write-through still happens; only reading it back stops
    payload = json.loads(files[0].read_text())
    payload["resource"]["title"] = "ten days old"
    files[0].write_text(json.dumps(payload))
    old = time.time() - 10 * 86400
    os.utime(files[0], (old, old))

    key = store.list_analyses()[0]["resource_key"]
    assert store.load_fresh_analysis(key, 0.0) is None  # the DB half, unchanged

    second = CountingProvider()
    result = AnalysisService(
        store, ProviderRegistry([second]), settings
    ).analyze_sources(LOCAL_OWNER, list(request.sources))
    assert second.analyze_calls == 1  # re-probed instead of serving the old file
    assert result.sources[0].resource.title == "Fake conference"
    # And the ten-day-old facts were never written back into the DB under a
    # current timestamp, which is what made the DB's own guard moot.
    assert "ten days old" not in [row["title"] for row in store.list_analyses()]


def test_file_cache_inactive_when_cache_disabled(settings):
    store = Store(settings.db_path)
    provider = CountingProvider()
    service = AnalysisService(store, ProviderRegistry([provider]), settings)
    service.analyze_sources(LOCAL_OWNER, list(make_request(minimal_payload()).sources))
    assert not (settings.data_dir / "cache").exists()  # no cache/ when disabled
