"""The interface actually speaks the language it is asked for.

`test_i18n.py` holds the catalogues together; this holds the *page* to them.
Three things have to be true and none of them is provable from a dictionary:
the operator's `CONTENT_UI_LANGUAGE` reaches the widgets, the picker in the
sidebar changes the page rather than just itself, and an installation that
configures nothing renders exactly the English it rendered before the layer
existed — the other seventy-three AppTests assert that English by name.
"""

import pytest

LANGUAGE_KEY = "content_ui_language_selector"


def test_english_is_what_an_unconfigured_installation_renders(monkeypatch, run_app):
    monkeypatch.delenv("CONTENT_UI_LANGUAGE", raising=False)
    at = run_app("hometube")
    assert at.text_input(key="url").label == "Video or Playlist URL"


def test_the_operators_default_reaches_the_widgets(monkeypatch, run_app):
    """`CONTENT_UI_LANGUAGE=fr` is the whole gesture on a self-hosted install —
    the same one legacy HomeTube spells `UI_LANGUAGE`."""
    monkeypatch.setenv("CONTENT_UI_LANGUAGE", "fr")
    at = run_app("hometube")
    assert at.text_input(key="url").label == "Vidéo ou playlist URL"
    labels = [box.label for box in at.selectbox]
    assert "Destination" in labels


@pytest.mark.parametrize("configured", ["fr-CH", "FR"])
def test_a_region_tag_is_still_french(monkeypatch, run_app, configured):
    monkeypatch.setenv("CONTENT_UI_LANGUAGE", configured)
    at = run_app("hometube")
    assert at.text_input(key="url").label == "Vidéo ou playlist URL"


def test_an_unknown_language_falls_back_rather_than_breaking(monkeypatch, run_app):
    monkeypatch.setenv("CONTENT_UI_LANGUAGE", "klingon")
    at = run_app("hometube")
    assert not at.exception
    assert at.text_input(key="url").label == "Video or Playlist URL"


def test_the_picker_changes_the_page_not_only_itself(monkeypatch, run_app):
    """The selector reruns the script, so the whole page follows it — that is
    the difference between a language setting and a decorative dropdown."""
    monkeypatch.delenv("CONTENT_UI_LANGUAGE", raising=False)
    at = run_app("hometube")
    assert at.text_input(key="url").label == "Video or Playlist URL"

    at.selectbox(key=LANGUAGE_KEY).set_value("fr").run()

    assert not at.exception
    assert at.text_input(key="url").label == "Vidéo ou playlist URL"
    assert at.selectbox(key=LANGUAGE_KEY).label == "Langue"


def test_the_choice_survives_the_next_interaction(monkeypatch, run_app):
    """A language reset by the next click would be worse than no picker."""
    monkeypatch.delenv("CONTENT_UI_LANGUAGE", raising=False)
    at = run_app("hometube")
    at.selectbox(key=LANGUAGE_KEY).set_value("fr").run()
    at.text_input(key="url").set_value("https://example.com/watch?v=abc").run()
    assert not at.exception
    assert at.text_input(key="url").label == "Vidéo ou playlist URL"
