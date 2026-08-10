"""The container must survive a fresh bind mount (the one-command install).

Docker creates a missing bind-mount source directory as root:root, so the
first `docker compose up -d` in an empty folder hands the engine a /data it
cannot write to when the process runs unprivileged — the database is never
created, the health check never passes, and compose reports "container content
is unhealthy". It only worked on macOS, where Docker Desktop and Colima remap
ownership for shared folders, which is why it survived development and broke
on a Linux host.

The image therefore starts as root, adopts the mounts in its entrypoint, and
drops to the app user. These guards keep that contract intact: they are static
(the real behaviour is verified by building and running the image, which is
too slow for the hermetic suite) but they catch the three ways it silently
regresses — a reinstated `USER`, an entrypoint that bypasses the script, and a
script that forgets to drop privileges.
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = (REPO / "apps" / "backend" / "Dockerfile").read_text()
ENTRYPOINT = (REPO / "apps" / "backend" / "docker-entrypoint.sh").read_text()


def test_the_entrypoint_runs_the_ownership_script():
    """tini stays PID 1 (it reaps yt-dlp/ffmpeg children); the script runs
    under it."""
    line = next(
        line for line in DOCKERFILE.splitlines() if line.startswith("ENTRYPOINT")
    )
    assert "/sbin/tini" in line, "tini must remain the init process"
    assert "docker-entrypoint.sh" in line, (
        "the entrypoint must run the ownership script, or a fresh install "
        "starts with an unwritable /data again"
    )


def test_the_image_does_not_pin_itself_to_the_unprivileged_user():
    """A `USER content` line would make the entrypoint unable to chown the
    mount — the exact failure this design removes."""
    users = [
        line
        for line in DOCKERFILE.splitlines()
        if line.strip().startswith("USER ") and "root" not in line
    ]
    assert not users, f"remove {users}: the entrypoint drops privileges itself"


def test_the_script_drops_privileges():
    assert "su-exec" in ENTRYPOINT, "the app must not keep running as root"
    assert "apk add --no-cache su-exec" in DOCKERFILE, "su-exec must be installed"
    assert 'exec su-exec "$APP_USER" "$@"' in ENTRYPOINT


def test_the_script_adopts_data_but_never_an_existing_library():
    """/data is Content's own state. /output is the operator's media library:
    adopting a non-empty one would rewrite the ownership of their collection."""
    assert 'chown "$APP_USER:$APP_USER" /data' in ENTRYPOINT
    assert '-z "$(ls -A /output 2>/dev/null)"' in ENTRYPOINT, (
        "the delivery library must only be adopted when empty"
    )
