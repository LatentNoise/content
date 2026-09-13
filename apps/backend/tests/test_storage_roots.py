"""Where an owner's files live, in either layout (ADR 0037).

The layout is a value read in one module; nothing else asks which mode is in
force. These tests pin the two trees, the one decision the layout takes about
the library, and the refusal that keeps a flat tree from ever holding two
people.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from content.identity import LOCAL_OWNER
from content.storage.roots import (
    owner_base,
    owner_roots,
    shared_roots,
    validate_layout,
)

# --- flat: the self-hosted majority ---------------------------------------------


def test_flat_is_the_historical_tree_with_no_owner_level(settings):
    roots = owner_roots(settings, LOCAL_OWNER)
    data = settings.data_dir
    assert roots.jobs == data / "jobs"
    assert roots.tmp == data / "tmp"
    assert roots.uploads == data / "uploads"
    assert roots.resources == data / "resources"
    assert roots.output == data / "delivery"
    assert owner_base(settings, LOCAL_OWNER) is None


def test_flat_honours_the_configured_roots(settings, tmp_path):
    custom = replace(
        settings,
        tmp_dir=tmp_path / "fast",
        uploads_dir=tmp_path / "up",
        delivery_dir=tmp_path / "nas",
    )
    roots = owner_roots(custom, LOCAL_OWNER)
    assert (roots.tmp, roots.uploads, roots.output) == (
        tmp_path / "fast",
        tmp_path / "up",
        tmp_path / "nas",
    )


def test_flat_collapses_per_owner_delivery_onto_the_library(settings):
    """One owner: there is nobody to separate, and a `<root>/local/` level
    would break every path a media server already reads."""
    scoped = replace(settings, delivery_scope="per_owner")
    assert owner_roots(scoped, LOCAL_OWNER).output == settings.data_dir / "delivery"
    assert owner_roots(scoped, LOCAL_OWNER).output_is_private is False


# --- per_user: several people ---------------------------------------------------


def test_per_user_puts_everything_of_one_person_under_one_directory(settings):
    per_user = replace(settings, storage_layout="per_user", delivery_scope="per_owner")
    roots = owner_roots(per_user, "usr_abc")
    base = settings.data_dir / "users" / "usr_abc"
    assert owner_base(per_user, "usr_abc") == base
    assert roots.jobs == base / "jobs"
    assert roots.tmp == base / "tmp"
    assert roots.uploads == base / "uploads"
    assert roots.resources == base / "resources"
    assert roots.output == base / "output"
    assert roots.output_is_private is True
    assert set(roots.all_owned()) == {
        base / "jobs",
        base / "tmp",
        base / "uploads",
        base / "resources",
        base / "output",
    }


def test_per_user_keeps_a_shared_library_when_asked(settings):
    """A family instance: several accounts, one library everyone reads."""
    family = replace(settings, storage_layout="per_user", delivery_scope="shared")
    roots = owner_roots(family, "usr_abc")
    assert roots.output == settings.data_dir / "delivery"
    assert roots.output_is_private is False
    assert roots.output not in roots.all_owned()


def test_per_user_respects_a_separate_tmp_disk(settings, tmp_path):
    fast = replace(settings, storage_layout="per_user", tmp_dir=tmp_path / "fast")
    assert owner_roots(fast, "usr_abc").tmp == tmp_path / "fast" / "usr_abc"


def test_delivery_off_means_no_library_in_either_layout(settings):
    for layout in ("flat", "per_user"):
        off = replace(settings, storage_layout=layout, delivery_scope="off")
        assert owner_roots(off, LOCAL_OWNER).output is None


def test_job_and_upload_paths_are_contained(settings):
    per_user = replace(settings, storage_layout="per_user")
    roots = owner_roots(per_user, "usr_abc")
    assert roots.job("job_1") == roots.jobs / "job_1"
    assert roots.upload("upl_1") == roots.uploads / "upl_1"
    for bad in ("..", "a/b", ""):
        with pytest.raises(ValueError):
            roots.job(bad)


@pytest.mark.parametrize("owner", ["..", "a/b", "", "x" * 200])
def test_an_owner_id_can_never_escape(settings, owner):
    per_user = replace(settings, storage_layout="per_user")
    with pytest.raises(ValueError):
        owner_roots(per_user, owner)


# --- what belongs to nobody -----------------------------------------------------


def test_shared_roots_sit_outside_every_owner_in_both_layouts(settings):
    for layout in ("flat", "per_user"):
        shared = shared_roots(replace(settings, storage_layout=layout))
        assert shared.cache_analysis == settings.data_dir / "cache" / "analysis"
        assert shared.tmp_analysis == settings.data_dir / "tmp" / "analysis"
        assert Path("users") not in shared.cache_analysis.parents


# --- the refusal ----------------------------------------------------------------


def test_a_flat_tree_refuses_to_host_sign_in(settings):
    with pytest.raises(ValueError, match="exactly one owner"):
        validate_layout(replace(settings, auth_mode="token", storage_layout="flat"))


def test_the_default_layout_follows_the_mode(monkeypatch):
    from content.config import settings_from_env

    monkeypatch.delenv("CONTENT_STORAGE_LAYOUT", raising=False)
    monkeypatch.setenv("CONTENT_AUTH_MODE", "none")
    assert settings_from_env().storage_layout == "flat"
    monkeypatch.setenv("CONTENT_AUTH_MODE", "token")
    assert settings_from_env().storage_layout == "per_user"


def test_an_explicit_layout_wins(monkeypatch):
    from content.config import settings_from_env

    monkeypatch.setenv("CONTENT_AUTH_MODE", "none")
    monkeypatch.setenv("CONTENT_STORAGE_LAYOUT", "per_user")
    assert settings_from_env().storage_layout == "per_user"
