"""Instance notifications — the data behind the UIs' banner.

The engine, not the UI, decides what is worth telling the operator: it is the
only side that knows its own version and the version of the tools it runs
(``yt-dlp``). The UIs render what they are given; they invent nothing (ADR 0011).

Two sources today:

* **A newer release is available** — an opt-in HTTP check against
  ``CONTENT_RELEASE_CHECK_URL`` (GitHub's and Forgejo's release APIs share the
  ``tag_name`` shape). Only *major/minor* releases notify: a patch bump would
  turn the banner into noise, and a banner people ignore is worse than none.
* **yt-dlp is stale** — opt-in (``CONTENT_YTDLP_MAX_AGE_DAYS``, off by
  default). YouTube breaks old builds quickly, and the symptom is an opaque
  ``analysis_failed`` / "No video formats found" (D-20) — but *age* cannot
  tell "stale" from "newest available" (yt-dlp releases irregularly, so the
  freshest possible image may already be weeks old). Off by default so a
  fresh install never opens on an unactionable warning; upstream freshness
  is the maintainer's loop, and users hear about it through Content
  releases.

Everything here is **failure-silent by contract**: an unreachable, slow, rate
limited or malformed release endpoint yields *no* notification, never an error.
A banner is a courtesy; it must never be able to break a page that would
otherwise render.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone

from content import __version__

# Levels are the UI's rendering hint, not a taxonomy: "info" is neutral news,
# "success" is a welcome upgrade, "warning" is something to act on.
Level = str

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
_RELEASE_TIMEOUT_SECONDS = 4.0


@dataclass(frozen=True)
class Notification:
    """One thing worth telling the operator. Data, never markup — the UI owns
    presentation, this module owns what is worth saying."""

    id: str
    level: Level
    title: str
    message: str
    action_label: str | None = None
    action_url: str | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "level": self.level,
            "title": self.title,
            "message": self.message,
            "action_label": self.action_label,
            "action_url": self.action_url,
        }


# --- version comparison --------------------------------------------------------


def parse_version(value: str) -> tuple[int, int, int]:
    """``"v2.6.1"`` → ``(2, 6, 1)``. Anything unparseable is ``(0, 0, 0)`` so a
    garbage tag can never be read as "newer than you"."""
    match = _VERSION_RE.search((value or "").lstrip("vV").strip())
    if not match:
        return (0, 0, 0)
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
    )


def is_major_or_minor_update(current: str, latest: str) -> bool:
    """True only when *latest* is a major or minor step up from *current*.

    A patch difference is deliberately silent: patches are frequent and rarely
    urgent, and a banner that appears every week stops being read.
    """
    cur = parse_version(current)
    new = parse_version(latest)
    if new == (0, 0, 0):  # unparseable → never notify
        return False
    if new[0] != cur[0]:
        return new[0] > cur[0]
    return new[1] > cur[1]


# --- release check -------------------------------------------------------------


def fetch_latest_release(url: str, *, timeout: float = _RELEASE_TIMEOUT_SECONDS) -> str:
    """The latest release tag published at *url*, or ``""``.

    GitHub (``/releases/latest``) and Forgejo (``/api/v1/repos/…/releases/latest``)
    both answer ``{"tag_name": …}``; a list endpoint answering an array is
    accepted too, newest first. Every failure mode — unreachable, timeout, 404,
    rate limit, HTML instead of JSON — returns ``""``.
    """
    if not url:
        return ""
    try:
        request = urllib.request.Request(
            url, headers={"accept": "application/json", "user-agent": "content"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return ""
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return ""
    tag = payload.get("tag_name") or payload.get("name") or ""
    return tag if isinstance(tag, str) else ""


def release_notification(
    current: str, latest: str, url: str = ""
) -> Notification | None:
    """A "newer version available" notification, or None when there is nothing
    worth saying."""
    if not latest or not is_major_or_minor_update(current, latest):
        return None
    tag = latest.lstrip("vV")
    return Notification(
        # Keyed by the target version so dismissing 1.2 does not also silence 2.0.
        id=f"release:{tag}",
        level="success",
        title="A new version is available",
        message=f"Content {tag} is out — this instance runs {current}.",
        action_label="View the release" if url else None,
        action_url=url or None,
    )


# --- yt-dlp staleness (D-20) ---------------------------------------------------


def ytdlp_age_days(tool_version: str, *, today: date | None = None) -> int | None:
    """Age in days of a yt-dlp build from its date-based version (``2026.07.04``).

    ``None`` when the version is absent or not date-shaped — yt-dlp has used
    ``YYYY.MM.DD`` for years, but guessing from an unknown format would be worse
    than staying quiet.
    """
    match = re.fullmatch(
        r"(\d{4})\.(\d{2})\.(\d{2})(?:\..*)?", (tool_version or "").strip()
    )
    if not match:
        return None
    try:
        released = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    reference = today or datetime.now(timezone.utc).date()
    return (reference - released).days


def ytdlp_notification(
    tool_version: str, max_age_days: int, *, today: date | None = None
) -> Notification | None:
    """Warn when the installed yt-dlp is old enough that YouTube is likely to
    have broken it (D-20). Silent when it is fresh, undetectable, or when the
    check is disabled (``max_age_days <= 0``)."""
    if max_age_days <= 0:
        return None
    age = ytdlp_age_days(tool_version, today=today)
    if age is None or age < max_age_days:
        return None
    return Notification(
        # Keyed by the build, so a dismissal does not carry over to the next one.
        id=f"ytdlp-stale:{tool_version}",
        level="warning",
        title="yt-dlp is out of date",
        message=(
            f"The installed yt-dlp ({tool_version}) is {age} days old. YouTube "
            "breaks older builds quickly — the symptom is an analysis that fails "
            "with 'No video formats found'. Updates ship with Content releases; "
            "to refresh this image yourself, rebuild with "
            "--build-arg YTDLP_SELF_UPDATE=true."
        ),
    )


# --- assembly ------------------------------------------------------------------


class _ReleaseCache:
    """Remembers the last release lookup so a page render never turns into an
    outbound HTTP call. A failed lookup is cached too — a down endpoint must not
    be retried on every rerun."""

    def __init__(self) -> None:
        self._tag = ""
        self._checked_at: datetime | None = None
        self._url = ""

    def get(self, url: str, ttl_hours: float) -> str:
        now = datetime.now(timezone.utc)
        fresh = (
            self._checked_at is not None
            and self._url == url
            and (now - self._checked_at).total_seconds() < ttl_hours * 3600
        )
        if not fresh:
            self._tag = fetch_latest_release(url)
            self._checked_at = now
            self._url = url
        return self._tag

    def clear(self) -> None:
        self._tag = ""
        self._checked_at = None
        self._url = ""


_release_cache = _ReleaseCache()


def build_notifications(
    settings, providers=None, *, today: date | None = None
) -> list[dict]:
    """Everything worth telling the operator right now, newest concern first.

    *providers* is a ``ProviderRegistry`` (optional): its ``describe()`` supplies
    the installed yt-dlp version. Never raises.
    """
    found: list[Notification] = []

    url = getattr(settings, "release_check_url", "") or ""
    if url:
        try:
            ttl = getattr(settings, "release_check_ttl_hours", 6.0)
            latest = _release_cache.get(url, ttl)
            note = release_notification(
                __version__, latest, getattr(settings, "release_page_url", "") or ""
            )
            if note:
                found.append(note)
        except Exception:  # pragma: no cover - the check is a courtesy, never a fault
            pass

    if providers is not None:
        try:
            runners = {entry["name"]: entry for entry in providers.describe()}
            ytdlp = runners.get("ytdlp") or {}
            note = ytdlp_notification(
                ytdlp.get("tool_version", ""),
                int(getattr(settings, "ytdlp_max_age_days", 0) or 0),
                today=today,
            )
            if note:
                found.append(note)
        except Exception:  # pragma: no cover - defensive
            pass

    return [note.as_dict() for note in found]
