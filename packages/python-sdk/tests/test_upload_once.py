"""De-duplicating a browser upload across Streamlit re-runs (ADR 0020).

Streamlit re-executes the entire script on every interaction, so an uploader
that sends on each run re-transmits the same file whenever the user touches an
unrelated widget — a gigabyte of video per checkbox. This rule lives in the SDK
precisely so it can be tested without a browser.
"""

from __future__ import annotations

from content_sdk.uploads import upload_once


def _sender(log: list[str], upload_id: str = "upl_1"):
    def send() -> dict:
        log.append(upload_id)
        return {"upload_id": upload_id}

    return send


def test_the_same_selection_is_sent_once():
    cache: dict = {}
    log: list[str] = []
    first = upload_once(cache, 0, "report.pdf", 11, _sender(log))
    second = upload_once(cache, 0, "report.pdf", 11, _sender(log))
    assert first == second == "upl_1"
    assert log == ["upl_1"], f"sent {len(log)} times"


def test_choosing_a_different_file_replaces_it():
    """A new selection is what the user means — the cache must not pin the old
    one just because the picker index is the same."""
    cache: dict = {}
    log: list[str] = []
    upload_once(cache, 0, "first.pdf", 11, _sender(log, "upl_1"))
    second = upload_once(cache, 0, "second.pdf", 22, _sender(log, "upl_2"))
    assert second == "upl_2"
    assert log == ["upl_1", "upl_2"]


def test_a_same_named_file_of_a_different_size_is_a_different_file():
    cache: dict = {}
    log: list[str] = []
    upload_once(cache, 0, "notes.md", 10, _sender(log, "upl_1"))
    again = upload_once(cache, 0, "notes.md", 999, _sender(log, "upl_2"))
    assert again == "upl_2"


def test_several_pickers_on_one_page_do_not_share_a_slot():
    cache: dict = {}
    log: list[str] = []
    upload_once(cache, 0, "a.pdf", 1, _sender(log, "upl_a"))
    upload_once(cache, 1, "b.pdf", 2, _sender(log, "upl_b"))
    upload_once(cache, 0, "a.pdf", 1, _sender(log, "upl_a"))
    assert log == ["upl_a", "upl_b"], "the second picker evicted the first"


def test_a_failed_send_is_not_remembered():
    """If the upload raised, nothing is cached — the next run must retry rather
    than hand back an id that was never issued."""
    cache: dict = {}

    def failing() -> dict:
        raise RuntimeError("engine unreachable")

    try:
        upload_once(cache, 0, "a.pdf", 1, failing)
    except RuntimeError:
        pass
    assert cache == {}
