"""The translation layer, and the two ways a catalogue rots.

A catalogue rots by *drift* — a key added to one language and not the other,
which ships a hole in the interface — and by *placeholder mismatch*, where a
translator drops a `{name}` and the sentence silently renders unformatted. Both
are mechanical, so both are checked here rather than noticed in production.

The third check is the one that matters most to a surface: every key a tracked
Python file passes to `t()` must exist in English. That is what stops a
rename in `app.py` from reaching a visitor as a raw key.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from content_sdk import i18n
from content_sdk.locales import en as en_locale
from content_sdk.locales import fr as fr_locale

REPO_ROOT = Path(__file__).resolve().parents[3]

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)[^}]*\}")
# Only literal keys are checked: `t(f"status.job.{status}")` is resolved at run
# time and `job_label` already falls back to the raw status for an unknown one.
_T_CALL = re.compile(r"""\bt\(\s*["']([a-zA-Z][\w.]*)["']""")


def _placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


def test_every_language_carries_every_key():
    """Drift in either direction is a hole in one of the two interfaces."""
    english = set(en_locale.TRANSLATIONS)
    for code, catalogue in i18n._CATALOGUES.items():
        missing = english - set(catalogue)
        extra = set(catalogue) - english
        assert not missing, f"{code} is missing: {sorted(missing)}"
        assert not extra, f"{code} has keys English does not: {sorted(extra)}"


def test_placeholders_match_english():
    """A translation that drops `{used}` renders the unformatted sentence."""
    for key, english in en_locale.TRANSLATIONS.items():
        for code, catalogue in i18n._CATALOGUES.items():
            assert _placeholders(catalogue[key]) == _placeholders(english), (
                f"{code}:{key} does not carry the same placeholders as English"
            )


def test_no_catalogue_entry_is_empty():
    for code, catalogue in i18n._CATALOGUES.items():
        blank = [key for key, value in catalogue.items() if not value.strip()]
        assert not blank, f"{code} has empty entries: {blank}"


def test_french_actually_differs_from_english():
    """A key copied verbatim from English is an untranslated one.

    The exceptions are words French does not translate — the product's own
    names, the em dash placeholder — and they are listed rather than inferred,
    so adding one is a deliberate act.
    """
    same_in_both = {
        "ht.api_docs",
        "ht.audio_section",
        "ht.output.audio",
        "ht.playlist_untitled",
        "ht.sponsorblock_label",
        "ht.tech_auto_suffix",
        "status.ago_unknown",
    }
    copied = {
        key
        for key, value in fr_locale.TRANSLATIONS.items()
        if value == en_locale.TRANSLATIONS[key] and key not in same_in_both
    }
    assert not copied, f"still English in the French catalogue: {sorted(copied)}"


def _tracked_sources() -> list[Path]:
    roots = [
        REPO_ROOT / "packages" / "python-sdk" / "content_sdk",
        REPO_ROOT / "apps" / "web-hometube",
        REPO_ROOT / "apps" / "web-studio",
        REPO_ROOT / "apps" / "web-admin",
    ]
    return [path for root in roots if root.is_dir() for path in root.rglob("*.py")]


def test_every_key_a_surface_asks_for_exists():
    sources = _tracked_sources()
    assert sources, "no source files found — the repository layout moved"
    unknown: dict[str, list[str]] = {}
    for path in sources:
        for key in _T_CALL.findall(path.read_text(encoding="utf-8")):
            if key not in en_locale.TRANSLATIONS:
                unknown.setdefault(key, []).append(path.name)
    assert not unknown, f"keys no catalogue defines: {unknown}"


def test_t_falls_back_to_english_then_to_the_key(monkeypatch):
    monkeypatch.setitem(i18n._CATALOGUES, "xx", {})
    monkeypatch.setattr(i18n, "current_language", lambda: "xx")
    assert i18n.t("ui.language") == en_locale.TRANSLATIONS["ui.language"]
    assert i18n.t("nothing.defines.this") == "nothing.defines.this"


def test_t_formats_and_survives_a_missing_placeholder(monkeypatch):
    monkeypatch.setitem(i18n._CATALOGUES, "xx", {"k": "{a} and {b}"})
    monkeypatch.setattr(i18n, "current_language", lambda: "xx")
    assert i18n.t("k", a=1, b=2) == "1 and 2"
    # The caller forgot `b`: the raw sentence beats a stack trace on the page.
    assert i18n.t("k", a=1) == "{a} and {b}"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("fr", "fr"),
        ("FR", "fr"),
        ("fr-CH", "fr"),
        ("fr_CH", "fr"),
        ("en", "en"),
        ("de", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_language_codes_are_normalised(value, expected):
    assert i18n._normalise(value) == expected


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("fr", "fr"), ("fr-CH", "fr"), ("EN", "en"), ("klingon", ""), ("", "")],
)
def test_the_environment_sets_the_installation_default(
    monkeypatch, configured, expected
):
    """`CONTENT_UI_LANGUAGE` is the operator's gesture, the same one legacy
    HomeTube spells `UI_LANGUAGE`. A value nothing knows is not an error — the
    surface falls back to English rather than refusing to start."""
    monkeypatch.setenv("CONTENT_UI_LANGUAGE", configured)
    assert i18n._from_environment() == expected


def test_french_is_served_when_the_session_speaks_french(monkeypatch):
    monkeypatch.setattr(i18n, "current_language", lambda: "fr")
    assert i18n.t("ui.language") == "Langue"
    assert i18n.t("notifications.dismiss") == "Masquer"


def test_the_default_is_english_and_unchanged(monkeypatch):
    """Nothing about this layer may move an English deployment's words."""
    monkeypatch.delenv("CONTENT_UI_LANGUAGE", raising=False)
    assert i18n.current_language() == "en"
    assert i18n.t("notifications.dismiss") == "Dismiss"
    assert i18n.t("signin.sign_out") == "Sign out"
