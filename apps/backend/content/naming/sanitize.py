"""Filename sanitization — the single module every layer imports (ADR 0017).

Two profiles, one authority:

- ``sanitize_filename`` — the *technical* profile: a conservative allowlist for
  files the backend addresses itself (job-store artifacts, logs, cache keys,
  delivery targets on disk). Aggressive by design; never user-facing.
- ``display_name`` — the *display* profile: the user-facing shape of a name.
  Spaces and unicode survive; path separators become ``" - "``; control
  characters and filesystem-hostile punctuation are dropped; Windows-reserved
  stems are defused. This is what ``display_filename`` and client name intent
  go through — sanitized, never rejected (D-51).

Sanitization is exclusively the backend's job: clients send *intent*.
"""

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

_SEPARATORS = re.compile(r"[/\\]+")
_FORBIDDEN = re.compile(r'[\x00-\x1f\x7f|:"*?<>]+')
_WHITESPACE = re.compile(r"\s+")
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def sanitize_filename(name: str, max_length: int = 120) -> str:
    """Conservative allowlist sanitization; the backend names every file."""
    cleaned = _UNSAFE.sub("_", name).strip("._") or "artifact"
    return cleaned[:max_length]


def display_name(name: str, max_length: int = 120) -> str:
    """User-facing name profile. Returns ``""`` when nothing survives — the
    caller decides the fallback (an empty *intent* is not the same as an
    empty *name*)."""
    cleaned = _SEPARATORS.sub(" - ", name)
    cleaned = _FORBIDDEN.sub(" ", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip(" .")
    cleaned = cleaned[:max_length].rstrip(" .")
    if not cleaned.strip("- "):
        return ""  # only separator residue survived — that is no name
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"{cleaned}_"
    return cleaned


def item_slug(text: str, index: int) -> str:
    """Stable per-item label for ``each_item`` expansion (playlist entries).
    Shared by the planner (step params) and the naming engine (item lookup) so
    both sides always compute the same key."""
    base = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return f"{index:03d}-{base[:60]}" if base else f"{index:03d}"
