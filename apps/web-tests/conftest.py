"""Hermetic UI non-regression harness (prompt 05).

Runs the real Streamlit apps headlessly (streamlit AppTest) against a
``FakeContentClient`` — canned, contract-shaped responses, NO backend, NO
network — so the dynamic behaviour (what a source lets you do) is guarded
against regressions. Deterministic and fast.

The fake routes on the source URI: 'audio' → a pure-audio source, 'playlist' →
a collection, otherwise a video. The shapes mirror the real /analyses and
/capabilities payloads (ADR 0013).
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
APPS = {
    "hometube": REPO / "apps" / "web-hometube" / "app.py",
    "studio": REPO / "apps" / "web-studio" / "app.py",
    "console": REPO / "apps" / "web-admin" / "app.py",
}


def pytest_configure(config):
    """`release` means the same here as in the backend suite: slow, needs a real
    backend, excluded from the fast gate. Registered so `-m release` selects it
    without an unknown-marker warning (this directory has no pytest config of
    its own — see the Makefile's `test-ui` / `test-ui-live` targets)."""
    config.addinivalue_line(
        "markers",
        "release: drives a UI against a live backend (make validate-release)",
    )


def _caps(items: list[tuple]) -> list[dict]:
    out = []
    for cid, status, variant, derived, reason in items:
        out.append(
            {
                "id": cid,
                "title": cid,
                "description": "",
                "output_type": cid.split(".")[0],
                "status": status,
                "selected_variant": variant,
                "derived_from": derived,
                "reason": reason,
            }
        )
    return out


_MISS_VID = {"code": "missing_material", "missing_materials": ["video"]}
_MISS_SUB = {"code": "missing_material", "missing_materials": ["subtitles"]}
_MISS_IMG = {"code": "missing_material", "missing_materials": ["image"]}
_NO_STT = {
    "code": "implementation_unavailable",
    "missing_operations": ["audio.transcribe"],
}


def _kind(uri: str) -> str:
    if "playlist" in uri:
        return "playlist"
    if "audio" in uri:
        return "audio"
    return "video"


class ApiError(Exception):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}")


# Set by a test to hand the UIs a notification; empty by default so every
# existing assertion sees exactly the page it saw before this feature.
NOTIFICATIONS: list = []

# The version the fake backend reports. The apps compare their __version__
# against the backend's to warn about torn deployments, and every declaration
# in the monorepo moves in lockstep (`make version`) — so the fake must move
# too, or a release bump fabricates a mismatch banner on every page and breaks
# the whole suite (as 0.2.0 did to a hard-coded "0.1.0"). content_sdk is one
# of the lockstep declarations and is installed in the UI venv: the honest
# source. Mismatch tests patch their own, deliberately impossible versions.
from content_sdk import __version__ as ENGINE_VERSION


class FakeContentClient:
    """Canned, contract-shaped answers keyed by the source URI."""

    def __init__(self, base_url=None, timeout=0, session=None):
        pass

    def health(self):
        return {"status": "ok", "version": ENGINE_VERSION}

    def notifications(self):
        return list(NOTIFICATIONS)

    def config(self):
        # Mirrors a configured server: primary fr, secondaries en/es, VO first,
        # primary subtitles NOT wanted (the user understands fr) — the UI must
        # pre-select only the wanted languages the source offers.
        return {
            "credentials": [],
            "language": {
                "primary": "fr",
                "secondaries": ["en", "es"],
                "vo_first": True,
                "primary_include_subtitles": False,
            },
        }

    def folders(self):
        return [""]

    def list_jobs(self, limit=30):
        return []

    def _one(self, sources):
        return _kind(sources[0].get("uri", ""))

    def analyze(self, sources):
        kind = self._one(sources)
        sid = sources[0]["id"]
        if kind == "playlist":
            return {
                "sources": [
                    {
                        "source_id": sid,
                        "resource": {
                            "resource_type": "collection",
                            "title": "Fake playlist",
                        },
                        "media": {},
                        "subtitles": [],
                        "entries": [
                            {"id": "v1", "title": "First"},
                            {"id": "v2", "title": "Second"},
                        ],
                    }
                ]
            }
        if kind == "audio":
            return {
                "sources": [
                    {
                        "source_id": sid,
                        "resource": {
                            "resource_type": "audio",
                            "title": "Fake podcast",
                            "duration_seconds": 1800,
                        },
                        "media": {"has_audio": True, "audio_languages": ["en"]},
                        "subtitles": [],
                        "entries": [],
                    }
                ]
            }
        return {
            "sources": [
                {
                    "source_id": sid,
                    "resource": {
                        "resource_type": "video",
                        "title": "Fake video",
                        "channel": "Chan",
                        "view_count": 1234567,
                        "duration_seconds": 120,
                    },
                    "media": {
                        "has_video": True,
                        "has_audio": True,
                        "video_heights": [360, 720, 1080],
                        "video_codecs": ["h264", "vp9"],
                        "audio_languages": ["en", "ja"],
                        "original_audio_language": "ja",
                    },
                    "subtitles": [
                        {"language": "en", "origin": "manual"},
                        {"language": "fr", "origin": "manual"},
                        {"language": "de", "origin": "automatic"},
                    ],
                    "entries": [],
                }
            ]
        }

    def capabilities(self, sources, constraints=None):
        kind = self._one(sources)
        sid = sources[0]["id"]
        if kind == "playlist":
            caps = _caps([("video.download", "unavailable", None, [], _MISS_VID)])
            rtype = "collection"
        elif kind == "audio":
            caps = _caps(
                [
                    ("audio.download", "available", "audio.download.direct", [], None),
                    (
                        "metadata.export",
                        "available",
                        "metadata.export.direct",
                        [],
                        None,
                    ),
                    ("video.download", "unavailable", None, [], _MISS_VID),
                    ("subtitles.download", "unavailable", None, [], _MISS_SUB),
                    ("thumbnail.download", "unavailable", None, [], _MISS_IMG),
                    ("transcript.generate", "unavailable", None, [], _NO_STT),
                    ("summary.generate", "unavailable", None, [], _NO_STT),
                ]
            )
            rtype = "audio"
        else:
            caps = _caps(
                [
                    ("video.download", "available", "video.download.direct", [], None),
                    ("audio.download", "available", "audio.download.direct", [], None),
                    (
                        "subtitles.download",
                        "available",
                        "subtitles.download.direct",
                        [],
                        None,
                    ),
                    (
                        "thumbnail.download",
                        "available",
                        "thumbnail.download.direct",
                        [],
                        None,
                    ),
                    (
                        "metadata.export",
                        "available",
                        "metadata.export.direct",
                        [],
                        None,
                    ),
                    (
                        "transcript.generate",
                        "derivable",
                        "transcript.from_subtitles",
                        ["subtitles"],
                        None,
                    ),
                    (
                        "summary.generate",
                        "derivable",
                        "summary.from_subtitles",
                        ["subtitles"],
                        None,
                    ),
                ]
            )
            rtype = "video"
        return {
            "analysis_id": "ana_fake",
            "sources": [
                {
                    "source_id": sid,
                    "resource_type": rtype,
                    "title": "Fake",
                    # The naming engine's proposal, as the real endpoint sends
                    # it (ADR 0017): the display profile has already turned the
                    # raw title into what the file will actually be called.
                    "suggested_filename": "Fake - Official Video",
                    "capabilities": caps,
                }
            ],
        }

    # --- console feeds ---------------------------------------------------------

    def system(self):
        return {
            "version": ENGINE_VERSION,
            # AGPL §13: the source offer the UIs render in their footer.
            "license": "AGPL-3.0-or-later",
            "source_url": "https://example.invalid/content",
            "cache_enabled": True,
            "analysis_ttl_hours": 72,
            "max_concurrent_jobs": 2,
            "credentials": [],
            "language": {
                "primary": "",
                "secondaries": [],
                "vo_first": True,
                "primary_include_subtitles": True,
            },
            "runners": [
                {
                    "name": "ytdlp",
                    "kind": "provider",
                    "operations": [],
                    "tool_version": "x",
                    "location": "local",
                    "available": True,
                }
            ],
            "environment": [],
            "paths": {},
        }

    def storage(self):
        z = {"path": "/x", "bytes": 0, "files": 0}
        return {
            "jobs": {**z, "count": 0},
            "delivery": {**z, "folders": 0},
            "tmp": z,
            "cache": {**z, "enabled": True, "cached_analyses": 0},
        }

    def cache(self):
        return {"enabled": True, "ttl_hours": 72, "analyses": []}

    def catalog(self):
        return {
            "capabilities": [
                {
                    "id": "video.download",
                    "title": "",
                    "description": "",
                    "output_type": "video",
                    "variants": [
                        {
                            "id": "video.download.direct",
                            "operations": ["media.acquire_video"],
                            "requires_materials": ["video"],
                            "option_groups": [],
                        }
                    ],
                }
            ],
            "operations": [
                {
                    "operation": "media.acquire_video",
                    "input_kinds": ["source"],
                    "output_kinds": ["video"],
                    "deterministic": True,
                    "implementations": [
                        {"runner": "ytdlp", "version": 1, "available": True}
                    ],
                },
                {
                    "operation": "audio.transcribe",
                    "input_kinds": ["audio"],
                    "output_kinds": ["transcript"],
                    "deterministic": False,
                    "implementations": [],
                },
            ],
        }


@pytest.fixture
def run_app(monkeypatch, tmp_path):
    """Return a runner that patches the shared client to the fake and runs an
    app via AppTest, returning the finished AppTest."""
    from content_sdk import compat
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(compat, "ContentClient", FakeContentClient)
    monkeypatch.setattr(compat, "ApiError", ApiError)
    # Per-test dismissal state: a real dismissal must not leak between tests.
    monkeypatch.setenv("CONTENT_UI_STATE_DIR", str(tmp_path / "ui-state"))

    def _run(app_name: str, url: str | None = None):
        path = str(APPS[app_name])
        if str(path) not in sys.path:
            sys.path.insert(0, str(Path(path).parent))
        at = AppTest.from_file(path, default_timeout=30)
        at.run()
        if url is not None:
            at.text_input(key="url").set_value(url).run()
        return at

    return _run
