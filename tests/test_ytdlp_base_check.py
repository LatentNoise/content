"""The base-image watcher must say which of two things happened.

Issue #28 was titled `yt-dlp base image: 2026.07.04 available (pinned:
2026.07.04)`. The detection was right — the tag had been rebuilt and the digest
moved, which is exactly why the pin carries a digest — but the title read as a
broken checker, and it looked identical to the case that actually matters.

They are not the same thing. A new yt-dlp version is how YouTube downloads stop
working; a rebuilt base is usually distro patches. A watcher whose alerts all
look alike trains its reader to ignore them.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / ".github/scripts"))

from ytdlp_base_check import issue_body, issue_title

DIGEST = "sha256:9587e8c5a54b1b8539e744747798c16b217cb35410abf4f239a7b6f1f09542ae"


def test_a_new_version_leads_with_the_version():
    title = issue_title("2026.07.04", "2026.08.19", DIGEST)
    assert title == "yt-dlp 2026.08.19 available (pinned: 2026.07.04)"


def test_a_rebuild_says_rebuilt_rather_than_repeating_the_version():
    """The #28 case: same version either side, and the old title said so twice."""
    title = issue_title("2026.07.04", "2026.07.04", DIGEST)
    assert "rebuilt" in title
    assert "available (pinned: 2026.07.04)" not in title


def test_an_untagged_digest_is_named_as_such():
    title = issue_title("2026.07.04", "", DIGEST)
    assert "untagged" in title and DIGEST[:19] in title


def test_the_body_opens_on_the_distinction_too():
    """A reader who only skims the first line still learns which case it is."""
    upgrade = issue_body("2026.07.04", "sha256:old", "2026.08.19", DIGEST)
    rebuild = issue_body("2026.07.04", "sha256:old", "2026.07.04", DIGEST)

    assert "is out" in upgrade and "stale yt-dlp" in upgrade
    assert "rebuilt" in rebuild and "rarely urgent" in rebuild
    # Both keep the promise the workflow makes: it never edits anything.
    for body in (upgrade, rebuild):
        assert "Nothing has been changed" in body


def test_the_body_does_not_present_its_digest_as_still_current():
    """The digest goes stale between runs, so the body must not invite a copy.

    This check runs weekly; upstream rebuilds `latest` several times a week. So
    a reader who copies the digest out of an open issue can pin a *superseded*
    rebuild — the older set of distro patches, which is the one thing the bump
    exists to collect. It has happened: issue #88's bump went to the 21/09
    rebuild rather than the digest the issue named, because `2026.08.19` had been
    republished twice in the interval.

    The remedy is not a fresher digest — it cannot be, at a weekly cadence — it
    is a body that says so and points at the one command that answers it now.
    """
    body = issue_body("2026.07.04", "sha256:old", "2026.08.19", DIGEST)

    # The table is dated rather than presented as the present tense.
    assert "Available, at this check" in body
    assert "| Available |" not in body

    # Step 1 sends the reader to the check rather than to the table.
    assert "python3 .github/scripts/ytdlp_base_check.py" in body
    assert "superseded" in body
    # The digest is still shown — it is the evidence that triggered the issue —
    # but labelled as a snapshot, never as the value to paste into the pin.
    assert f"(at this check: {DIGEST})" in body
    assert f"ARG YTDLP_BASE_DIGEST={DIGEST}" not in body


# --- what the refresh workflow builds with -------------------------------------


def test_the_check_exposes_what_a_rebuild_needs(tmp_path, monkeypatch):
    """The daily refresh rebuilds the released tree on the newer base, so it
    needs the version and the digest the check resolved — not just a boolean.

    The digest is what Docker resolves and is the authority; the version rides
    along so the rebuilt image can still say, in a label a human reads, which
    yt-dlp is inside it.
    """
    import ytdlp_base_check as mod

    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "ARG YTDLP_BASE_VERSION=2026.01.01\n"
        "ARG YTDLP_BASE_DIGEST=sha256:" + "a" * 64 + "\n"
        "FROM jauderho/yt-dlp:${YTDLP_BASE_VERSION}@${YTDLP_BASE_DIGEST}\n"
    )
    out = tmp_path / "out"
    out.write_text("")

    monkeypatch.setattr(mod, "DOCKERFILE", str(dockerfile))
    monkeypatch.setattr(mod, "upstream_digest", lambda: "sha256:" + "b" * 64)
    monkeypatch.setattr(mod, "version_for", lambda digest: "2026.08.19")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.chdir(tmp_path)

    mod.main()

    written = dict(
        line.split("=", 1) for line in out.read_text().splitlines() if "=" in line
    )
    assert written["update_available"] == "true"
    assert written["pinned_version"] == "2026.01.01"
    assert written["new_version"] == "2026.08.19"
    assert written["new_digest"] == "sha256:" + "b" * 64


def test_a_rebuilt_base_still_reports_its_version(tmp_path, monkeypatch):
    """Upstream republishes the same yt-dlp when the distro under it is
    patched. That is a digest change with no version change, it is still worth
    rebuilding for — the image scan's findings all live in that layer — and the
    two must stay distinguishable downstream."""
    import ytdlp_base_check as mod

    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "ARG YTDLP_BASE_VERSION=2026.08.19\n"
        "ARG YTDLP_BASE_DIGEST=sha256:" + "a" * 64 + "\n"
        "FROM jauderho/yt-dlp:${YTDLP_BASE_VERSION}@${YTDLP_BASE_DIGEST}\n"
    )
    out = tmp_path / "out"
    out.write_text("")

    monkeypatch.setattr(mod, "DOCKERFILE", str(dockerfile))
    monkeypatch.setattr(mod, "upstream_digest", lambda: "sha256:" + "b" * 64)
    monkeypatch.setattr(mod, "version_for", lambda digest: "2026.08.19")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.chdir(tmp_path)

    mod.main()

    written = dict(
        line.split("=", 1) for line in out.read_text().splitlines() if "=" in line
    )
    assert written["update_available"] == "true"
    # Same version on both sides: the refresh must still be able to tell the
    # reader this was a rebuild rather than a new yt-dlp.
    assert written["pinned_version"] == written["new_version"] == "2026.08.19"
