#!/bin/sh
# Make the mounted state writable, then drop to the unprivileged app user.
#
# Docker creates a missing bind-mount source directory as root:root. A first
# `docker compose up -d` in an empty folder — the documented one-command
# install — therefore handed the container a /data owned by root while the
# process runs as uid 1000: the database was never created, the health check
# never passed, and compose reported "container content is unhealthy". It only
# worked on macOS, where Docker Desktop and Colima remap ownership for shared
# folders, which is exactly why it survived development and broke on a Linux
# homelab.
#
# Fixing it here rather than asking the operator to pre-create and chown
# directories keeps the install one command on any host, whatever their uid.
# The container still runs unprivileged: root exists for the length of this
# script and nothing more.
set -e

APP_USER=content

if [ "$(id -u)" = "0" ]; then
    # Content's own state (database, jobs, artifacts, cache): always ours.
    # Non-recursive on purpose — the app creates everything below /data as
    # itself once the mount point is writable, and a recursive pass would
    # scan an arbitrarily large artifact store on every start.
    [ -d /data ] && chown "$APP_USER:$APP_USER" /data 2>/dev/null || true

    # The delivery library belongs to the operator, not to Content: adopt it
    # only when it is the empty directory Docker just created for us. An
    # existing library keeps its ownership — silently chowning someone's media
    # collection would be a worse bug than the one this fixes. When it is not
    # writable, delivery fails per job with a clear error instead, and the
    # operator points CONTENT_DELIVERY_DIR_HOST at a directory they own or
    # sets `user:` in compose.
    if [ -d /output ] && [ -z "$(ls -A /output 2>/dev/null)" ]; then
        chown "$APP_USER:$APP_USER" /output 2>/dev/null || true
    fi

    exec su-exec "$APP_USER" "$@"
fi

# Already unprivileged (an explicit `user:` in compose, or a hardened
# runtime): nothing to adjust, and nothing we could adjust anyway.
exec "$@"
