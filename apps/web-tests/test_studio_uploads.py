"""Studio accepts a file from the user's own device (ADR 0020).

Studio runs in a container with no volumes and speaks to the engine over HTTP,
so a path typed into it would mean nothing on the other side. The picker
uploads instead: bytes leave the browser, reach the app, and go to the engine
as an `upload` source.

Scope, stated so nothing here pretends to more than it proves: Streamlit's
AppTest cannot drive a `file_uploader`, so what is guarded here is that the app
still renders and still offers both ways to name a file, and that merely
loading the page sends nothing. The de-duplication rule — the part that is easy
to get wrong — lives in `content_sdk.uploads` and is tested directly there,
which is why it was moved out of this app in the first place.
"""

from __future__ import annotations

import pytest
from conftest import UPLOADED


@pytest.fixture(autouse=True)
def _clear_uploads():
    UPLOADED.clear()
    yield
    UPLOADED.clear()


def test_studio_still_offers_a_file_source(run_app):
    app = run_app("studio")
    assert not app.exception, app.exception
    offering_file = [
        box for box in app.selectbox if "file" in [str(o) for o in box.options]
    ]
    assert offering_file, "Studio must still offer a `file` source type"


def test_loading_the_page_uploads_nothing(run_app):
    """A page load must not send bytes anywhere — the upload happens on a
    user's explicit choice, never as a side effect of rendering."""
    app = run_app("studio")
    assert not app.exception, app.exception
    assert UPLOADED == []
