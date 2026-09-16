"""The refusal a visitor can act on, drawn by the surfaces.

Two rules here are not style, they are the difference between a refusal and a
gift, and they are what this file exists to hold:

- **the self-host command comes before any other exit.** A wall that hides the
  free alternative is detected in three seconds by this audience;
- **an instance that configured nothing shows the engine's plain sentence.**
  That is every self-hosted installation, and a UI that invented an offer there
  would be advertising a service its own user is running.

And one rule about the page rather than the wall: the counters belong in the
sidebar *before* the refusal. A limit someone cannot watch themselves approach
is a trap rather than a rule (ADR 0036).
"""

import pytest
from conftest import QUOTA_WALL
from streamlit.testing.v1 import AppTest

WALL_SCRIPT = """
import streamlit as st
from content_sdk.errors import error_for
from content_sdk import quota

body = {
    "detail": {
        "valid": False,
        "phase": "feasibility",
        "errors": [
            {
                "code": "quota_exceeded",
                "path": "execution",
                "message": "this installation allows 60",
                "details": {"limit": LIMIT, "used": USED, "allowed": ALLOWED},
            }
        ],
        "warnings": [],
    }
}
st.session_state["drawn"] = quota.render_streamlit_wall(error_for(422, body), CONFIG)
"""


def _wall(config, limit="media_minutes", used=62.0, allowed=60.0):
    script = (
        WALL_SCRIPT.replace("CONFIG", repr(config))
        .replace("LIMIT", repr(limit))
        .replace("USED", repr(used))
        .replace("ALLOWED", repr(allowed))
    )
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _texts(at):
    """Everything the page said, in the order it said it."""
    return (
        [e.value for e in at.error]
        + [m.value for m in at.markdown]
        + [c.value for c in at.code]
        + [c.value for c in at.caption]
    )


def _ordered_text(at):
    """The page as one string, in render order — the only way to assert that
    one block comes *before* another."""
    return "\n".join(
        el.value for el in at.main if isinstance(getattr(el, "value", None), str)
    )


# --- the wall -------------------------------------------------------------------


def test_a_configured_instance_offers_self_hosting_first():
    at = _wall({"quota_wall": dict(QUOTA_WALL), "retention": {"days": 15.0}})
    page = _ordered_text(at)
    assert QUOTA_WALL["self_host_command"] in page
    assert QUOTA_WALL["cta_label"] in page
    # The whole point: free before paid.
    assert page.index(QUOTA_WALL["self_host_command"]) < page.index(
        QUOTA_WALL["cta_label"]
    )


def test_the_wall_names_no_price_and_offers_no_purchase():
    page = _ordered_text(_wall({"quota_wall": dict(QUOTA_WALL)})).lower()
    for forbidden in ("€", "$", "/month", "per month", "subscribe", "buy", "upgrade"):
        assert forbidden not in page


def test_an_unconfigured_instance_says_only_what_the_engine_said():
    """A self-hosted instance: no offer exists, so none is invented."""
    at = _wall({})
    page = _ordered_text(at)
    assert "60 minutes" in page  # the per-limit sentence, which is not an offer
    assert "docker" not in page.lower()
    assert "Two ways forward" not in page


def test_a_docs_url_alone_is_not_an_offer():
    """Half a configuration is not a wall — without a command or a call to
    action there is nothing to propose."""
    page = _ordered_text(_wall({"quota_wall": {"docs_url": "https://example.test"}}))
    assert "Two ways forward" not in page


def test_the_retention_note_uses_the_instances_own_window():
    page = _ordered_text(
        _wall({"quota_wall": dict(QUOTA_WALL), "retention": {"days": 7.0}})
    )
    assert "7 days" in page


def test_no_retention_window_means_no_promise_about_one():
    page = _ordered_text(
        _wall({"quota_wall": dict(QUOTA_WALL), "retention": {"days": 0}})
    )
    assert "downloadable" not in page


@pytest.mark.parametrize(
    "limit,expected",
    [
        ("media_minutes", "age out"),
        ("storage_bytes", "Delete"),
        ("active_jobs", "nothing is lost"),
    ],
)
def test_each_limit_says_what_to_do_about_itself(limit, expected):
    at = _wall({"quota_wall": dict(QUOTA_WALL)}, limit=limit, used=1, allowed=1)
    assert any(expected in text for text in _texts(at))


# --- the approach to it ---------------------------------------------------------


@pytest.mark.parametrize("surface", ["hometube", "studio"])
def test_the_counters_are_visible_before_any_refusal(run_app, surface):
    at = run_app(surface)
    assert not at.exception
    sidebar = "\n".join(
        el.value for el in at.sidebar if isinstance(getattr(el, "value", None), str)
    )
    assert "41 / 60 min of media" in sidebar
    assert "0 / 1 jobs" in sidebar
