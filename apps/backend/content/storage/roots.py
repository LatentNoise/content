"""Where one owner's files live — the only module that knows the layout.

Two layouts, one code path (ADR 0037):

    flat        <data>/jobs/<job>      one owner, the self-hosted majority
    per_user    <data>/users/<owner>/jobs/<job>      several owners

Nothing downstream asks which layout is in force, exactly as nothing asks
which authentication mode is in force (ADR 0030). Callers ask for an owner's
roots and get paths; the layout is a *value* read here and nowhere else, so the
rule cannot be re-implemented slightly differently in five places.

Two things belong to nobody and therefore sit outside any owner, in both
layouts: the analysis cache (facts about public resources, shared on purpose)
and the analysis probe scratch (disposable, keyed by resource, not by person).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from content.storage.paths import safe_segment

FLAT = "flat"
PER_USER = "per_user"
LAYOUTS = frozenset({FLAT, PER_USER})

# The directory that holds every owner in the per-user layout. Chosen over
# "owners" because it is the word people say; the code keeps saying owner_id.
USERS_DIR = "users"


@dataclass(frozen=True)
class OwnerRoots:
    """Every root an owner's files can be under. Built once per request from
    settings; asked for paths, never assembled by hand elsewhere."""

    owner_id: str
    jobs: Path
    tmp: Path
    uploads: Path
    resources: Path
    # None when the installation delivers nothing (delivery scope `off`).
    output: Path | None

    def job(self, job_id: str) -> Path:
        return self.jobs / safe_segment(job_id, "job_id")

    def job_tmp(self, job_id: str) -> Path:
        return self.tmp / safe_segment(job_id, "job_id")

    def upload(self, upload_id: str) -> Path:
        return self.uploads / safe_segment(upload_id, "upload_id")

    def all_owned(self) -> tuple[Path, ...]:
        """What belongs to this owner and may be measured or removed together.
        The library is included only when it is theirs alone."""
        owned = [self.jobs, self.tmp, self.uploads, self.resources]
        if self.output is not None and self.output_is_private:
            owned.append(self.output)
        return tuple(owned)

    @property
    def output_is_private(self) -> bool:
        """Is the library this owner's alone, or shared with everyone?"""
        return self._output_private

    # Set by the factory; a frozen dataclass cannot carry a derived flag any
    # other way without recomputing the policy, which is what we avoid.
    _output_private: bool = False


@dataclass(frozen=True)
class SharedRoots:
    """What belongs to nobody, in either layout."""

    cache: Path
    cache_analysis: Path
    tmp_analysis: Path


def layout_of(settings) -> str:
    return getattr(settings, "storage_layout", FLAT) or FLAT


def _data(settings) -> Path:
    return Path(settings.data_dir)


def users_root(settings) -> Path:
    return _data(settings) / USERS_DIR


def owner_base(settings, owner_id: str) -> Path | None:
    """The one directory holding everything of this owner, or None in the flat
    layout, where there is no such directory because there is no such need."""
    if layout_of(settings) == PER_USER:
        return users_root(settings) / safe_segment(owner_id, "owner_id")
    return None


def owner_roots(settings, owner_id: str) -> OwnerRoots:
    safe_segment(owner_id, "owner_id")
    data = _data(settings)
    scope = getattr(settings, "delivery_scope", "shared") or "shared"
    delivery_dir = Path(settings.delivery_dir or data / "delivery")

    if layout_of(settings) == PER_USER:
        base = users_root(settings) / owner_id
        # An explicit CONTENT_TMP_ROOT keeps its meaning — a separate, faster or
        # disposable disk — and gains the owner level under it.
        tmp = (Path(settings.tmp_dir) / owner_id) if settings.tmp_dir else base / "tmp"
        if scope == "off":
            output, private = None, False
        elif scope == "shared":
            # A family instance: several accounts, one library everyone reads.
            output, private = delivery_dir, False
        else:
            output, private = base / "output", True
        return OwnerRoots(
            owner_id=owner_id,
            jobs=base / "jobs",
            tmp=tmp,
            uploads=base / "uploads",
            resources=base / "resources",
            output=output,
            _output_private=private,
        )

    # flat: the historical tree, untouched by ownership. One owner, so the
    # configured roots are used as they are.
    if scope == "off":
        output, private = None, False
    else:
        # `per_owner` under flat is meaningless — one owner — and collapses to
        # the plain library rather than inventing a `<root>/local/` level that
        # would break every path a media server already reads.
        output, private = delivery_dir, False
    return OwnerRoots(
        owner_id=owner_id,
        jobs=data / "jobs",
        tmp=Path(settings.tmp_dir or data / "tmp"),
        uploads=Path(settings.uploads_dir or data / "uploads"),
        resources=data / "resources",
        output=output,
        _output_private=private,
    )


def shared_roots(settings) -> SharedRoots:
    data = _data(settings)
    cache = Path(settings.cache_dir or data / "cache")
    tmp = Path(settings.tmp_dir or data / "tmp")
    return SharedRoots(
        cache=cache, cache_analysis=cache / "analysis", tmp_analysis=tmp / "analysis"
    )


def validate_layout(settings) -> None:
    """Refuse a configuration that cannot hold what it will be asked to hold.

    A flat tree has exactly one owner by construction. An engine that asks
    people to sign in will have many, and filing them into one tree is not a
    degraded mode — it is two people's files in one directory. So the pair is
    refused at startup, where the operator reads the message, rather than at
    the first second sign-in, where nobody does.
    """
    layout = layout_of(settings)
    if layout not in LAYOUTS:
        raise ValueError(
            f"CONTENT_STORAGE_LAYOUT must be one of {sorted(LAYOUTS)}, got {layout!r}"
        )
    if layout == FLAT and getattr(settings, "auth_mode", "none") == "token":
        raise ValueError(
            "CONTENT_STORAGE_LAYOUT=flat holds exactly one owner, but "
            "CONTENT_AUTH_MODE=token will create many. Set "
            "CONTENT_STORAGE_LAYOUT=per_user (the engine files existing data "
            "under users/local on the next start)."
        )
