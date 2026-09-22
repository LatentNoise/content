"""What language a surface speaks, and the one place its words live.

Legacy HomeTube ships a complete French interface (`app/translations/{en,fr}.py`,
selected by `UI_LANGUAGE`), and its ~1 450 stars are the audience HomeTube by
Content has to be at least as good for. An English-only interface is one of the
three or four things legacy does that Content did not, so this is the layer that
closes it.

**Why the SDK.** Three single-file Streamlit apps plus this package: the SDK is
the only place all three can share code from, which is why `legal.py`,
`notifications.py`, `quota.py`, `signin.py` and `status.py` already live here
(D-21 records what happened the last time a helper was copy-pasted into three
UIs). The catalogue holds the shared components' words *and* each surface's,
namespaced by prefix — one dictionary per language, as legacy does, because two
hundred keys split across five files is a lookup problem nobody has.

**Why not legacy's module-level cache.** Legacy resolves the language once per
process and memoises it (`_translations_cache`), which is correct for a desktop
app run by one person. A Content surface is a server: one process renders for
every visitor, so the language has to be resolved *per session*, and a cached
global would make the first visitor's choice everybody's — the same class of
bug `get_client` documents for credentials. Hence `st.session_state`, and hence
a plain dict lookup rather than a cache; the catalogues are module constants and
there is nothing to memoise.

**Resolution order**, most specific first:

1. ``?lang=fr`` in the address — a link can carry the language, which is what
   makes a French instance shareable;
2. the choice made in this browser session (the selector writes it there);
3. ``CONTENT_UI_LANGUAGE`` — the operator's default for the installation, the
   same gesture as legacy's ``UI_LANGUAGE``;
4. English.

**What this does not translate.** Everything the *engine* says — refusal
messages, notification bodies, error prose — arrives from the API in English
and stays English. Teaching the contract to negotiate a language is a decision
about the public API, not about a UI, and it is not made here.
"""

from __future__ import annotations

import os
from typing import Any

from content_sdk.locales import en as _en
from content_sdk.locales import fr as _fr

__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "catalogue",
    "current_language",
    "language_selector",
    "set_language",
    "t",
]

DEFAULT_LANGUAGE = "en"

#: `code -> the name the language calls itself`. A selector that offers
#: "French" to someone who reads French is asking them to recognise their own
#: language in a foreign one.
LANGUAGES: dict[str, str] = {"en": "English", "fr": "Français"}

_CATALOGUES: dict[str, dict[str, str]] = {
    "en": _en.TRANSLATIONS,
    "fr": _fr.TRANSLATIONS,
}

#: Where the session's choice is remembered. Underscored like the SDK's other
#: session keys so a surface never collides with it.
SESSION_KEY = "_content_ui_language"

_QUERY_PARAM = "lang"


def catalogue(language: str | None = None) -> dict[str, str]:
    """Every key of one language, for whoever needs the whole table."""
    return _CATALOGUES.get(language or current_language(), _CATALOGUES["en"])


def _normalise(value: Any) -> str:
    """``"FR"``, ``"fr-CH"`` → ``"fr"``; anything unknown → ``""``.

    A browser and an operator both write region tags, and refusing `fr-CH`
    because it is not `fr` would be a bug nobody would think to look for.
    """
    code = str(value or "").strip().lower().replace("_", "-").split("-")[0]
    return code if code in _CATALOGUES else ""


def _from_environment() -> str:
    return _normalise(os.getenv("CONTENT_UI_LANGUAGE"))


def current_language() -> str:
    """The language this run of the page speaks.

    Outside Streamlit — a test, the CLI, anything importing the SDK — only the
    environment and the default apply, and nothing is imported that is not
    installed.
    """
    try:
        import streamlit as st
    except ImportError:
        return _from_environment() or DEFAULT_LANGUAGE

    try:
        asked = _normalise(st.query_params.get(_QUERY_PARAM))
    except Exception:  # noqa: BLE001 — no script run, or an older Streamlit
        asked = ""
    try:
        if asked:
            # A link that carries the language sets it for the session too, so
            # the next click does not fall back to English once the parameter
            # is gone.
            st.session_state[SESSION_KEY] = asked
            return asked
        chosen = _normalise(st.session_state.get(SESSION_KEY))
    except Exception:  # noqa: BLE001 — no script run (a test, a bare import)
        return asked or _from_environment() or DEFAULT_LANGUAGE
    return chosen or _from_environment() or DEFAULT_LANGUAGE


def set_language(language: str) -> str:
    """Make this session speak *language*; returns what was actually set."""
    import streamlit as st

    code = _normalise(language) or DEFAULT_LANGUAGE
    st.session_state[SESSION_KEY] = code
    return code


def t(key: str, **fmt: Any) -> str:
    """The sentence for *key* in the current language.

    Falls back to English for a key a translation has not caught up with, and
    to the key itself for one no catalogue has — a surface must not break over
    a missing word, and `test_i18n.py` is what keeps either from shipping.

    Formatting failures fall back to the unformatted sentence for the same
    reason: a translator who drops a placeholder costs a bad-looking line, not
    a stack trace on the page.
    """
    text = _CATALOGUES.get(current_language(), {}).get(key)
    if text is None:
        text = _CATALOGUES["en"].get(key, key)
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, IndexError, ValueError):
        return text


def language_selector(*, key: str = "content_ui_language_selector") -> str:
    """The language picker, for a surface's sidebar. Returns the language.

    Rendered only when there is a choice to make: a build carrying one
    catalogue shows nothing rather than a selectbox with one entry.
    """
    import streamlit as st

    if len(LANGUAGES) < 2:
        return current_language()

    codes = list(LANGUAGES)
    current = current_language()
    chosen = st.selectbox(
        t("ui.language"),
        codes,
        index=codes.index(current) if current in codes else 0,
        format_func=lambda code: LANGUAGES.get(code, code),
        key=key,
    )
    if chosen != current:
        set_language(chosen)
        st.rerun()
    return chosen
