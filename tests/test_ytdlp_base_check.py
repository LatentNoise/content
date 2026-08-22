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
