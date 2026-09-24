"""The interface actually speaks the language it is asked for.

`test_i18n.py` holds the catalogues together; this holds the *page* to them.
Three things have to be true and none of them is provable from a dictionary:
the operator's `CONTENT_UI_LANGUAGE` reaches the widgets, the picker in the
sidebar changes the page rather than just itself, and an installation that
configures nothing renders exactly the English it rendered before the layer
existed — the other seventy-three AppTests assert that English by name.

**Parametrised over the surfaces, not copied per surface.** Each one is
described by a *probe*: one widget, addressed by its stable key, whose label
is a sentence in both languages. The six facts are then asserted identically
everywhere, so a surface joining the layer costs one row here rather than a
second copy of the file.

`console` is absent on purpose: the Console is still English — it is the half
of brief `48` that was not done. Adding its row is what will prove it French.
"""

from dataclasses import dataclass

import pytest

LANGUAGE_KEY = "content_ui_language_selector"


@dataclass(frozen=True)
class Probe:
    """One widget per surface that says, by its label, which language won."""

    surface: str
    widget: str
    key: str
    english: str
    french: str
    #: A text input the test can touch to prove the choice outlives a rerun.
    interact: str

    def label(self, at) -> str:
        return getattr(at, self.widget)(key=self.key).label


PROBES = [
    Probe(
        surface="hometube",
        widget="text_input",
        key="url",
        english="Video or Playlist URL",
        french="Vidéo ou playlist URL",
        interact="url",
    ),
    Probe(
        surface="studio",
        widget="number_input",
        key="n_sources",
        english="How many sources?",
        french="Combien de sources ?",
        interact="uri-0",
    ),
]
SURFACES = pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.surface)


@SURFACES
def test_english_is_what_an_unconfigured_installation_renders(
    monkeypatch, run_app, probe
):
    monkeypatch.delenv("CONTENT_UI_LANGUAGE", raising=False)
    at = run_app(probe.surface)
    assert probe.label(at) == probe.english


@SURFACES
def test_the_operators_default_reaches_the_widgets(monkeypatch, run_app, probe):
    """`CONTENT_UI_LANGUAGE=fr` is the whole gesture on a self-hosted install —
    the same one legacy HomeTube spells `UI_LANGUAGE`."""
    monkeypatch.setenv("CONTENT_UI_LANGUAGE", "fr")
    at = run_app(probe.surface)
    assert probe.label(at) == probe.french


@SURFACES
@pytest.mark.parametrize("configured", ["fr-CH", "FR"])
def test_a_region_tag_is_still_french(monkeypatch, run_app, probe, configured):
    monkeypatch.setenv("CONTENT_UI_LANGUAGE", configured)
    at = run_app(probe.surface)
    assert probe.label(at) == probe.french


@SURFACES
def test_an_unknown_language_falls_back_rather_than_breaking(
    monkeypatch, run_app, probe
):
    monkeypatch.setenv("CONTENT_UI_LANGUAGE", "klingon")
    at = run_app(probe.surface)
    assert not at.exception
    assert probe.label(at) == probe.english


@SURFACES
def test_the_picker_changes_the_page_not_only_itself(monkeypatch, run_app, probe):
    """The selector reruns the script, so the whole page follows it — that is
    the difference between a language setting and a decorative dropdown."""
    monkeypatch.delenv("CONTENT_UI_LANGUAGE", raising=False)
    at = run_app(probe.surface)
    assert probe.label(at) == probe.english

    at.selectbox(key=LANGUAGE_KEY).set_value("fr").run()

    assert not at.exception
    assert probe.label(at) == probe.french
    assert at.selectbox(key=LANGUAGE_KEY).label == "Langue"


@SURFACES
def test_the_choice_survives_the_next_interaction(monkeypatch, run_app, probe):
    """A language reset by the next click would be worse than no picker."""
    monkeypatch.delenv("CONTENT_UI_LANGUAGE", raising=False)
    at = run_app(probe.surface)
    at.selectbox(key=LANGUAGE_KEY).set_value("fr").run()
    at.text_input(key=probe.interact).set_value("https://example.com/watch?v=abc").run()
    assert not at.exception
    assert probe.label(at) == probe.french


def test_hometubes_destination_selector_still_renders_in_french(monkeypatch, run_app):
    """The one surface-specific assertion worth keeping from before the
    parametrisation: HomeTube's destination picker is built from the engine's
    folder list, so a translated label there proves the form followed the
    language and not only the header."""
    monkeypatch.setenv("CONTENT_UI_LANGUAGE", "fr")
    at = run_app("hometube")
    assert "Destination" in [box.label for box in at.selectbox]


def test_studio_keeps_the_contracts_own_words_in_french(monkeypatch, run_app):
    """Studio shows the public contract, so its tokens are not translated.

    A French Studio offering « fichier » instead of `file` would be hiding the
    discriminator the request actually carries — the one thing this surface
    exists to make visible. The sentence around the token moves; the token
    does not.
    """
    monkeypatch.setenv("CONTENT_UI_LANGUAGE", "fr")
    at = run_app("studio")
    assert not at.exception
    types = at.selectbox(key="stype-0")
    assert types.label == "Type"
    assert [str(option) for option in types.options] == ["url", "file", "text"]


def test_studios_file_location_is_a_sentence_and_does_move(monkeypatch, run_app):
    """The counterpart: *where* a file is, is prose, so it is translated.

    Its options are stable codes behind a `format_func` precisely so that the
    branch choosing between an upload and a server path never depends on a
    translated sentence.
    """
    monkeypatch.setenv("CONTENT_UI_LANGUAGE", "fr")
    at = run_app("studio")
    at.selectbox(key="stype-0").set_value("file").run()
    assert not at.exception
    where = at.radio(key="floc-0")
    assert where.label == "Où se trouve-t-il ?"
    assert [str(option) for option in where.options] == [
        "Depuis cet appareil",
        "Sur le serveur",
    ]
